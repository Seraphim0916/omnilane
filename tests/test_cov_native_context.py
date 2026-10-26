import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "aa-model-policy.json"
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import native_context  # noqa: E402


def reads_as(vendor, model, effort):
    return lambda pid: (pid, (vendor, model, effort), {})


def unreadable(pid):
    raise ValueError("fixture: no launcher")


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-context-cov-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.workdir = self.base / "work"
        self.workdir.mkdir()
        patcher = mock.patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *flags, caller=reads_as("claude", "claude-opus-5-5", "medium"), out=True):
        argv = ["--workdir", str(self.workdir), "--registry", str(REGISTRY), *flags]
        if out:
            argv += ["--out", str(self.base / "out.json")]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native_context.main(argv, read_caller=caller)
        return code, stdout.getvalue(), stderr.getvalue()


class ArgumentTests(Fixture):
    def test_vendor_and_model_go_together(self):
        for flags in (("--vendor", "claude"), ("--model", "m")):
            with self.subTest(flags=flags):
                code, stdout, stderr = self.run_main(*flags)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn("--vendor and --model go together", stderr)

    def test_host_statement_names_are_validated(self):
        code, _, stderr = self.run_main("--vendor", "claude", "--model", "bad name")
        self.assertEqual(code, 2)
        self.assertIn("invalid name: bad name", stderr)

    def test_workdir_must_be_a_directory(self):
        code, stdout, stderr = self.run_main("--workdir", str(self.base / "absent"))
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("not a directory", stderr)
        self.assertFalse((self.base / "out.json").exists())


class IdentityTests(Fixture):
    def test_unreadable_identity_without_host_statement_is_three(self):
        code, stdout, stderr = self.run_main(caller=unreadable)
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("cannot read the caller identity: fixture: no launcher", stderr)
        self.assertIn(native_context.HOST_STATEMENT_HINT, stderr)

    def test_unreadable_identity_falls_back_to_host_statement(self):
        code, stdout, stderr = self.run_main("--vendor", "claude", "--model", "claude-opus-5-5",
                                             "--inherits-caller-runtime", caller=unreadable)
        self.assertEqual(code, 0, stderr)
        value = json.loads(Path(stdout.strip()).read_text())
        self.assertFalse(value["caller_identity_verified"])
        self.assertTrue(value["inherits_caller_runtime"])
        self.assertEqual(value["capabilities"][0]["efforts"], [native_context.UNVERIFIED_EFFORT])
        self.assertNotIn("current_effort", value)
        self.assertIn("identity host-asserted", stderr)
        self.assertNotIn("pass --inherits-caller-runtime", stderr)

    def test_unreadable_registry_is_treated_like_an_unreadable_identity(self):
        argv = ["--workdir", str(self.workdir), "--registry", str(self.base / "absent.json")]
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = native_context.main(argv, read_caller=reads_as("claude", "claude-opus-5-5", "max"))
        self.assertEqual(code, 3)
        self.assertIn("cannot read the caller identity", stderr.getvalue())

    def test_host_statement_must_match_a_readable_identity(self):
        for flags in (("--vendor", "codex", "--model", "claude-opus-5-5"),
                      ("--vendor", "claude", "--model", "claude-sonnet-5-5")):
            with self.subTest(flags=flags):
                code, stdout, stderr = self.run_main(*flags)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn("this harness reads as claude/claude-opus-5-5", stderr)

    def test_matching_host_statement_is_accepted(self):
        code, _, stderr = self.run_main("--vendor", "claude", "--model", "claude-opus-5-5")
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("caller_identity_verified", json.loads((self.base / "out.json").read_text()))

    def test_unscored_identity_is_three_or_host_asserted(self):
        caller = reads_as("claude", "claude-unscored", "max")
        code, stdout, stderr = self.run_main(caller=caller)
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("cannot describe this harness", stderr)
        code, _, stderr = self.run_main("--vendor", "claude", "--model", "claude-unscored", caller=caller)
        self.assertEqual(code, 0, stderr)
        self.assertIn("caller identity not verified", stderr)
        value = json.loads((self.base / "out.json").read_text())
        self.assertEqual(value["current_model"], "claude-unscored")
        self.assertFalse(value["caller_identity_verified"])

    def test_default_output_path_is_content_addressed_under_home(self):
        code, stdout, stderr = self.run_main("--mode", "work", "--mode", "advise", "--mode", "work",
                                             "--workdir", str(self.workdir), out=False)
        self.assertEqual(code, 0, stderr)
        path = Path(stdout.strip())
        self.assertEqual(path.parent, self.home / "native-context")
        self.assertRegex(path.name, r"^claude-code--claude-opus-5-5--[0-9a-f]{12}\.json$")
        value = json.loads(path.read_text())
        self.assertEqual(value["capabilities"][0]["modes"], ["advise", "work"])
        self.assertEqual(value["capabilities"][0]["workdirs"], [str(self.workdir.resolve())])
        self.assertEqual(value["current_effort"], "medium")
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name])
        self.assertIn("pass --inherits-caller-runtime", stderr)


class HostRowsFileTests(Fixture):
    def rows(self, value):
        path = self.base / "rows.json"
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def assert_invalid(self, value, message):
        with self.assertRaisesRegex(native_context.HostRowsError, message):
            native_context.load_host_rows(self.rows(value))

    def test_shape_errors_are_named(self):
        def doc(harnesses):
            return {"schema_version": 1, "harnesses": harnesses}

        self.assert_invalid("{", "cannot read")
        self.assert_invalid('{"schema_version": 1, "schema_version": 1}', "duplicate key")
        self.assert_invalid({"schema_version": 1}, "exactly the keys")
        self.assert_invalid({"schema_version": True, "harnesses": {}}, "unsupported schema_version")
        self.assert_invalid({"schema_version": 1, "harnesses": []}, '"harnesses" must be an object')
        self.assert_invalid(doc({"bad name": []}), "invalid harness name")
        self.assert_invalid(doc({"claude-code": {}}), "rows must be a list")
        self.assert_invalid(doc({"claude-code": [{"model": "m"}]}), 'exactly "model" and "efforts"')
        self.assert_invalid(doc({"claude-code": [{"model": 5, "efforts": ["max"]}]}), "invalid model")
        self.assert_invalid(doc({"claude-code": [{"model": "m", "efforts": ["max"]},
                                                 {"model": "m", "efforts": ["low"]}]}), "listed twice")
        self.assert_invalid(doc({"claude-code": [{"model": "m", "efforts": []}]}), "non-empty list")
        self.assert_invalid(doc({"claude-code": [{"model": "m", "efforts": ["-x"]}]}), "invalid effort")
        self.assert_invalid(doc({"claude-code": [{"model": "m", "efforts": ["max", "max"]}]}),
                            "duplicate effort")

    def test_valid_file_round_trips(self):
        path = self.rows({"schema_version": 1, "harnesses": {
            "claude-code": [{"model": "a", "efforts": ["low", "max"]}], "codex": []}})
        self.assertEqual(native_context.load_host_rows(path),
                         {"claude-code": [("a", ["low", "max"])], "codex": []})

    def test_explicit_rows_file_must_exist(self):
        code, stdout, stderr = self.run_main("--host-rows", str(self.base / "absent.json"))
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("host rows file not found", stderr)

    def test_rows_are_skipped_for_a_host_asserted_identity(self):
        path = self.rows("{")
        code, _, stderr = self.run_main("--host-rows", str(path), "--vendor", "claude",
                                        "--model", "claude-opus-5-5", caller=unreadable)
        self.assertEqual(code, 0, stderr)
        self.assertIn("host rows skipped", stderr)

    def test_capability_cap_is_enforced(self):
        path = self.rows({"schema_version": 1, "harnesses": {
            "claude-code": [{"model": "claude-sonnet-5-5", "efforts": ["max"]}]}})
        code, _, stderr = self.run_main("--host-rows", str(path))
        self.assertEqual(code, 0, stderr)
        self.assertIn("+ 1 host rows", stderr)
        with mock.patch.object(native_context, "MAX_CAPABILITIES", 1):
            code, stdout, stderr = self.run_main("--host-rows", str(path))
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("yields more than 1 capability rows", stderr)


if __name__ == "__main__":
    unittest.main()
