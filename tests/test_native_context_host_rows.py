#!/usr/bin/env python3
"""Host-declared native capability rows merged by `omnilane native-context`."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
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


class HostRowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-host-rows-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.workdir = self.base / "work"
        self.workdir.mkdir()
        self.out = self.base / "out" / "context.json"
        patcher = mock.patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_rows(self, value, path=None):
        path = path or self.home / "native-rows.json"
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def run_main(self, *flags, caller=reads_as("claude", "claude-opus-5-5", "medium")):
        argv = ["--workdir", str(self.workdir), "--mode", "work", "--registry", str(REGISTRY),
                "--out", str(self.out), *flags]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native_context.main(argv, read_caller=caller)
        return code, stdout.getvalue(), stderr.getvalue()

    def produced(self):
        return json.loads(self.out.read_text())

    def assert_refused(self, *flags, **kwargs):
        code, stdout, stderr = self.run_main(*flags, **kwargs)
        self.assertEqual(code, 2, stderr)
        self.assertEqual(stdout, "")
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.parent.exists() and any(self.out.parent.iterdir()))
        return stderr

    def test_no_default_file_keeps_one_row(self):
        code, stdout, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        caps = self.produced()["capabilities"]
        self.assertEqual(len(caps), 1)
        self.assertEqual((caps[0]["model"], caps[0]["efforts"]), ("claude-opus-5-5", ["medium"]))
        self.assertNotIn("host rows", stderr)

    def test_rows_for_the_caller_harness_are_appended(self):
        self.write_rows({"schema_version": 1, "harnesses": {
            "claude-code": [{"model": "claude-sonnet-5", "efforts": ["low", "high"]},
                            {"model": "claude-fable-5-1", "efforts": ["xhigh"]}],
            "codex": [{"model": "gpt-not-scored", "efforts": ["high"]}]}})
        code, _, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        value = self.produced()
        caps = value["capabilities"]
        self.assertEqual([(c["model"], c["efforts"]) for c in caps],
                         [("claude-opus-5-5", ["medium"]), ("claude-fable-5-1", ["xhigh"]),
                          ("claude-sonnet-5", ["high", "low"])])
        for cap in caps[1:]:
            self.assertEqual(cap["modes"], caps[0]["modes"])
            self.assertEqual(cap["workdirs"], caps[0]["workdirs"])
            self.assertEqual((cap["tools"], cap["isolations"], cap["lifecycles"]),
                             ([], ["shared-inherited"], ["single-shot"]))
        self.assertEqual(value["current_model"], "claude-opus-5-5")
        self.assertEqual(value["current_effort"], "medium")
        self.assertIn(f" + 2 host rows ({self.home / 'native-rows.json'})", stderr)

    def test_the_callers_own_pair_is_not_duplicated(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-5-5", "efforts": ["medium", "high", "low"]},
            {"model": "claude-opus-5", "efforts": ["medium"]}]}})
        code, _, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        self.assertEqual([(c["model"], c["efforts"]) for c in self.produced()["capabilities"]],
                         [("claude-opus-5-5", ["medium"]), ("claude-opus-5", ["medium"]),
                          ("claude-opus-5-5", ["high", "low"])])

    def test_a_model_left_with_no_efforts_adds_no_row(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-5-5", "efforts": ["medium"]}]}})
        code, _, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.produced()["capabilities"]), 1)
        self.assertNotIn("host rows", stderr)

    def test_an_unscored_pair_is_refused(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-5-5", "efforts": ["high", "ultra"]}]}})
        stderr = self.assert_refused()
        self.assertIn("claude-opus-5-5 at effort ultra is not a scored configuration for claude", stderr)

    def test_a_non_reasoning_only_pair_is_refused(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-4-7", "efforts": ["high"]}]}})
        self.assertIn("claude-opus-4-7 at effort high", self.assert_refused())

    def test_malformed_files_are_refused(self):
        good = {"model": "claude-opus-5", "efforts": ["high"]}
        cases = {
            "missing schema_version": {"harnesses": {"claude-code": [good]}},
            "extra top-level key": {"schema_version": 1, "harnesses": {}, "note": "x"},
            "extra row key": {"schema_version": 1, "harnesses": {"claude-code": [dict(good, tools=[])]}},
            "empty efforts": {"schema_version": 1, "harnesses": {"claude-code": [
                {"model": "claude-opus-5", "efforts": []}]}},
            "duplicate effort": {"schema_version": 1, "harnesses": {"claude-code": [
                {"model": "claude-opus-5", "efforts": ["high", "high"]}]}},
            "bad model name": {"schema_version": 1, "harnesses": {"claude-code": [
                {"model": "claude opus", "efforts": ["high"]}]}},
            "wrong version": {"schema_version": 2, "harnesses": {}},
            "boolean version": {"schema_version": True, "harnesses": {}},
            "not json": "{not json",
            "duplicate key": '{"schema_version": 1, "schema_version": 1, "harnesses": {}}',
        }
        for label, value in cases.items():
            with self.subTest(label):
                self.write_rows(value)
                self.assertIn("invalid host rows file", self.assert_refused())

    def test_no_host_rows_ignores_the_default_file(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-5", "efforts": ["high"]}]}})
        code, _, stderr = self.run_main("--no-host-rows")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.produced()["capabilities"]), 1)

    def test_explicit_host_rows_file(self):
        path = self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-5", "efforts": ["high"]}]}}, self.base / "elsewhere.json")
        code, _, stderr = self.run_main("--host-rows", str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.produced()["capabilities"]), 2)
        self.assertIn(f"+ 1 host rows ({path})", stderr)

    def test_missing_explicit_file_is_refused(self):
        stderr = self.assert_refused("--host-rows", str(self.base / "absent.json"))
        self.assertIn("host rows file not found", stderr)

    def test_host_asserted_caller_skips_host_rows(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-opus-5", "efforts": ["high"]}]}})
        code, _, stderr = self.run_main("--vendor", "claude", "--model", "claude-opus-5-5",
                                        "--inherits-caller-runtime", caller=unreadable)
        self.assertEqual(code, 0, stderr)
        value = self.produced()
        self.assertEqual(len(value["capabilities"]), 1)
        self.assertIs(value["caller_identity_verified"], False)
        self.assertEqual(stderr.count("host rows skipped"), 1)
        self.assertNotIn("+ ", stderr)

    def test_produced_file_validates_and_routes_a_host_row_natively(self):
        self.write_rows({"schema_version": 1, "harnesses": {"claude-code": [
            {"model": "claude-sonnet-5", "efforts": ["high", "low"]}]}})
        code, stdout, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip(), str(self.out))
        spec = importlib.util.spec_from_file_location("native_under_test", ROOT / "scripts/lib/native.py")
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        ctx = native.capability_context(self.out)

        def lane(model, effort, mode="work"):
            return SimpleNamespace(executor="auto", vendor="claude", model=model, effort=effort,
                                   mode=mode, workdir=str(self.workdir.resolve()), background=False,
                                   session="auto", thread="", job_timeout="", idle_timeout="")

        self.assertEqual(native.choose(lane("claude-sonnet-5", "high"), ctx),
                         ("native", "exact-capability-match", "claude-sonnet-5"))
        self.assertEqual(native.choose(lane("claude-opus-5-5", "medium"), ctx)[0], "native")
        self.assertEqual(native.choose(lane("claude-sonnet-5", "max"), ctx),
                         ("cli", "effort-mismatch", "claude-sonnet-5"))
        self.assertEqual(native.choose(lane("claude-sonnet-5", "high", mode="advise"), ctx),
                         ("cli", "mode-mismatch", "claude-sonnet-5"))


if __name__ == "__main__":
    unittest.main()
