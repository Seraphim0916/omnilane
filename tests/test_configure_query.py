"""Read-only configure get/list queries with isolated inspection failures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]


class ConfigureQueryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-configure-query-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "config"
        self.home.mkdir()
        self.routing = self.home / "routing.local.yaml"
        self.routing.write_text("# fixture note\ntriage: off\nhard-judgment: off\n")
        self.local = self.home / "local.sh"
        self.local.write_text("# ordinary local configuration\nOMNILANE_TIMEOUT=60\n")
        self.bins = self.root / "fake-bin"
        self.bins.mkdir()
        self.utilities = Path(self.env["OMNILANE_TEST_UTIL_PATH"])
        self.env["OMNILANE_HOME"] = str(self.home)
        self.env["PATH"] = str(self.bins) + ":" + self.env["PATH"]
        self.inspections = self.root / "inspection-calls"
        self.presence = self.root / "presence-calls"
        self.unexpected = self.root / "unexpected-calls"
        self.addCleanup(lambda: self.assertFalse(self.unexpected.exists()))
        # All discoverable provider/network tools are explicit no-call guards.
        for name in ("claude", "codex", "gemini", "grok", "kimi", "qwen",
                     "opencode", "openrouter", "deepseek", "zai", "mistral",
                     "groq", "cerebras", "curl", "wget"):
            self.executable(name,
                "import pathlib, sys\n"
                f"with pathlib.Path({str(self.unexpected)!r}).open('a') as handle:\n"
                f"    handle.write({name!r} + ' ' + repr(sys.argv[1:]) + '\\n')\n"
                "sys.exit(91)\n")
        self.inspection_fixture()

    def executable(self, name, body):
        path = self.bins / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def home_snapshot(self):
        result = {}
        for path in self.home.rglob("*"):
            stat = path.stat()
            result[str(path.relative_to(self.home))] = (
                path.read_bytes() if path.is_file() else None,
                stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
        return result

    def command(self, *args):
        before = self.home_snapshot()
        self.inspections.write_text("")
        self.presence.write_text("")
        result = subprocess.run([str(self.utilities / "bash"),
                                 str(ROOT / "scripts/configure.sh"), *args],
                                cwd=self.root, env=self.env, text=True,
                                capture_output=True, timeout=20)
        self.assertEqual(self.home_snapshot(), before, "query changed configuration")
        self.assertFalse((self.home / "jobs").exists(), "query created job state")
        self.assertFalse(self.unexpected.exists(), "query called an unexpected child")
        return result

    def inspection_fixture(self, status=None, output=""):
        # None delegates only the ordinary --list child to allowlisted Bash.
        self.executable("bash",
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"if args != {[str(ROOT / 'scripts/dispatch.sh'), '--list']!r}:\n"
            f"    pathlib.Path({str(self.unexpected)!r}).write_text(repr(args))\n"
            "    sys.exit(91)\n"
            f"with pathlib.Path({str(self.inspections)!r}).open('a') as handle:\n"
            "    handle.write(json.dumps(args) + '\\n')\n"
            f"status = {status!r}\n"
            "if status is not None:\n"
            f"    sys.stdout.write({output!r})\n"
            "    sys.stderr.write('fixture inspection detail\\n')\n"
            "    sys.exit(status)\n"
            f"os.execv({str(self.utilities / 'bash')!r}, "
            f"[{str(self.utilities / 'bash')!r}, *args])\n")

    def presence_fixture(self, status):
        self.executable("grep",
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"if args == {['-qE', '^[a-z]', str(self.routing)]!r}:\n"
            f"    with pathlib.Path({str(self.presence)!r}).open('a') as handle:\n"
            "        handle.write(json.dumps(args) + '\\n')\n"
            "    sys.stderr.write('fixture presence detail\\n')\n"
            f"    sys.exit({status})\n"
            f"os.execv({str(self.utilities / 'grep')!r}, "
            f"[{str(self.utilities / 'grep')!r}, *args])\n")

    def assert_inspections(self, expected):
        calls = [json.loads(line) for line in self.inspections.read_text().splitlines()]
        self.assertEqual(calls, [[str(ROOT / "scripts/dispatch.sh"), "--list"]] * expected)

    def test_get_inspection_failure_preserves_status_and_hides_partial_output(self):
        for status in (1, 2, 37, 126, 130):
            for output in ("", "hard-judgment: off\n", "triage: off\n"):
                with self.subTest(status=status, output=output):
                    self.inspection_fixture(status, output)
                    result = self.command("get", "triage")
                    self.assert_inspections(1)
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr,
                                     f"omnilane: configure get: failed to inspect effective routing table (exit {status})\n")

    def test_list_presence_error_is_not_successful_no_overrides(self):
        for status in (2, 37, 126, 130):
            with self.subTest(status=status):
                self.presence_fixture(status)
                result = self.command("list")
                self.assert_inspections(0)
                self.assertEqual(len(self.presence.read_text().splitlines()), 1)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr,
                                 f"omnilane: configure list: failed to inspect existing routing content (exit {status})\n")

    def test_get_returns_first_exact_lane_line_with_formatting_preserved(self):
        first = 'triage:   claude "fixture model" high | off  # fixture annotation  '
        output = ("triage-other: off\n  triage: ignored\n\n" + first +
                  "\ntriage: later duplicate\n")
        for table in (output, first):
            with self.subTest(table=table):
                self.inspection_fixture(0, table)
                result = self.command("get", "triage")
                self.assert_inspections(1)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, first + "\n", ""))

    def test_get_successful_empty_or_unmatched_table_is_unknown_lane(self):
        for output in ("", "hard-judgment: off\ntriage-other: off\n  triage: off\n"):
            with self.subTest(output=output):
                self.inspection_fixture(0, output)
                result = self.command("get", "triage")
                self.assert_inspections(1)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, "", "omnilane: unknown lane 'triage' (see: omnilane list)\n"))

    def test_get_real_inspection_preserves_success_and_unknown_lane(self):
        result = self.command("get", "triage")
        self.assert_inspections(1)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "triage:          off\n", ""))
        result = self.command("get", "no-such-lane")
        self.assert_inspections(1)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (2, "", "omnilane: unknown lane 'no-such-lane' (see: omnilane list)\n"))

    def test_get_invalid_lane_rejects_before_inspection(self):
        for lane in ("", "Bad_Lane", "two words"):
            with self.subTest(lane=lane):
                result = self.command("get", lane)
                self.assert_inspections(0)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (2, "", f"omnilane: invalid lane '{lane}'\n"))

    def test_list_returns_populated_file_verbatim(self):
        for content in ("# user note\ntriage: off\n\nhard-judgment: off\n",
                        "# user note\ntriage: off"):
            with self.subTest(content=content):
                self.routing.write_text(content)
                result = self.command("list")
                self.assert_inspections(0)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, content, ""))

    def test_list_absent_empty_and_comment_only_files_have_no_overrides(self):
        for content in (None, "", "# user note\n\n"):
            with self.subTest(content=content):
                if content is None:
                    self.routing.unlink()
                else:
                    self.routing.write_text(content)
                result = self.command("list")
                self.assert_inspections(0)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, f"no local overrides in {self.routing}\n", ""))

    def test_list_later_cat_failure_keeps_existing_streaming_semantics(self):
        self.executable("cat",
            "import os, sys\n"
            f"if sys.argv[1:] == {[str(self.routing)]!r}:\n"
            "    sys.stdout.write('# streamed prefix\\n')\n"
            "    sys.stderr.write('fixture cat detail\\n')\n"
            "    sys.exit(37)\n"
            f"os.execv({str(self.utilities / 'cat')!r}, "
            f"[{str(self.utilities / 'cat')!r}, *sys.argv[1:]])\n")
        result = self.command("list")
        self.assert_inspections(0)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (37, "# streamed prefix\n", "fixture cat detail\n"))


if __name__ == "__main__":
    unittest.main()
