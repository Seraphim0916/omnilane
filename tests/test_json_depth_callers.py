"""Deep JSON gets the same answer on 3.9-3.14 at every remaining json_depth caller.

3.9-3.13 raised RecursionError on 10000 levels while 3.14 decodes them, so each case
below would have differed by version before the explicit depth check.
"""
import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy  # noqa: E402
import caller_identity as ci  # noqa: E402
import json_depth  # noqa: E402
import native  # noqa: E402
import quota_preflight as quota  # noqa: E402

DEPTH = 10000
DEEP_ARRAY = "[" * DEPTH + "0" + "]" * DEPTH
THREAD = "0199a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"


def deep_record(**fields):
    """A JSON object whose only problem is one deeply nested value."""
    head = json.dumps(fields)[:-1] + (", " if fields else "")
    return head + '"data": ' + DEEP_ARRAY + "}"


class TempDir(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="json-depth-callers-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class AAPolicyDepthTests(TempDir):
    def test_deep_policy_file_is_invalid_json_input(self):
        path = self.root / "registry.json"
        path.write_text(deep_record(schema_version=1))
        with self.assertRaisesRegex(aa_policy.PolicyError, "^invalid JSON input$"):
            aa_policy._read_json(path)

    def test_private_check_matches_json_depth(self):
        self.assertEqual(aa_policy.JSON_MAX_DEPTH, json_depth.MAX_DEPTH)
        for text in ("[" * 256 + "]" * 256, "[" * 257 + "]" * 257,
                     json.dumps({"s": "[" * 400}), '{"a":' * 257 + "{}" + "}" * 257):
            with self.subTest(text=text[:20]):
                try:
                    json_depth.check(text)
                    expected = True
                except json_depth.JSONDepthError:
                    expected = False
                self.assertEqual(aa_policy._json_depth_within_limit(text), expected)


class LoadedByPathTests(TempDir):
    """Callers may load these files by path without scripts/lib on sys.path."""

    LIB = Path(__file__).resolve().parents[1] / "scripts/lib"

    def load(self, path):
        code = ("import importlib.util, sys\n"
                "sys.path[:] = [p for p in sys.path if 'scripts/lib' not in p]\n"
                f"spec = importlib.util.spec_from_file_location('m', {str(path)!r})\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(module)\n")
        return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)

    def test_modules_load_by_path(self):
        for name in ("native", "caller_identity", "quota_preflight", "aa_policy"):
            with self.subTest(module=name):
                result = self.load(self.LIB / f"{name}.py")
                self.assertEqual(result.returncode, 0, result.stderr[-500:])

    def test_aa_policy_still_loads_when_copied_alone(self):
        copy = self.root / "aa_policy.py"
        copy.write_bytes((self.LIB / "aa_policy.py").read_bytes())
        result = self.load(copy)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])


class CodexRolloutDepthTests(TempDir):
    def test_deep_rollout_record_is_incomplete_or_invalid_json(self):
        codex = self.root / "codex"
        day = codex / "sessions" / "2026" / "10" / "01"
        day.mkdir(parents=True)
        meta = json.dumps({"type": "session_meta", "payload": {"id": THREAD}})
        (day / f"rollout-2026-10-01T00-00-00-{THREAD}.jsonl").write_text(
            meta + "\n" + deep_record(type="turn_context") + "\n")
        with self.assertRaisesRegex(ValueError, "^rollout contains incomplete or invalid JSON$"):
            ci._rollout_selector(THREAD, {"CODEX_HOME": str(codex)})


class ClaudeTailDepthTests(TempDir):
    def test_deep_line_is_skipped_like_any_unreadable_line(self):
        path = self.root / "t.jsonl"
        proven = json.dumps({"type": "assistant", "message": {"model": "claude-x-20261001"}, "effort": "high"})
        path.write_text(proven + "\n")
        expected = ci._claude_tail_selector(path)
        path.write_text(proven + "\n" + deep_record(type="assistant") + "\n")
        self.assertEqual(ci._claude_tail_selector(path), expected)


class ClaudeSessionDepthTests(TempDir):
    START = "Tue Sep 29 19:22:47 2026"
    SID = "11111111-1111-4111-8111-111111111111"
    ARGV = ("claude", "claude-opus-5-5", "xhigh")

    def test_deep_sessions_file_falls_back_to_launch_flags(self):
        session = self.root / "sessions" / "20.json"
        session.parent.mkdir(parents=True)
        session.write_text(deep_record(pid=20, sessionId=self.SID, procStart=self.START))
        # A readable transcript would move the selector to "medium"; only refusing the
        # deep sessions file keeps the launch flags.
        transcript = self.root / "projects" / "encoded-cwd" / f"{self.SID}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"type": "assistant", "isSidechain": False,
                                          "message": {"model": self.ARGV[1]},
                                          "effort": "medium", "perTurnEffort": "medium"}) + "\n")
        tree ={40: (30, ["python3"]), 30: (20, ["bash"]),
                20: (10, ["claude", "--model", self.ARGV[1], "--effort", "xhigh"]),
                10: (1, ["claude", "--model", "outer-model", "--effort", "low"])}
        environment = {"CLAUDE_CODE_SESSION_ID": self.SID, "CLAUDE_CONFIG_DIR": str(self.root)}
        result = ci.read_caller(40, tree.get, current_environment=environment,
                                claude_dir=self.root, start_time=lambda pid: self.START)
        self.assertEqual(result, (20, self.ARGV, ""))


class NativeDepthTests(TempDir):
    def test_deep_state_file_is_refused_by_the_depth_check(self):
        path = self.root / "state.json"
        path.write_text(deep_record(schema_version=1))
        with self.assertRaises(json_depth.JSONDepthError):
            native.read_json(str(path))

    def test_depth_error_is_reported_without_echoing_input(self):
        for json_mode in (False, True):
            with self.subTest(json_mode=json_mode):
                stdout, stderr = io.StringIO(), io.StringIO()
                argv = ["native.py", "job", "--home", str(self.root)]
                if json_mode:
                    argv.append("--json")
                argv.extend(["status", "20261001-120000-1-2"])
                error = json_depth.JSONDepthError("JSON nesting deeper than 256 levels")
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.object(native, "job_command", side_effect=error), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(native.main(), 2)
                output = stdout.getvalue() + stderr.getvalue()
                self.assertIn("invalid or inaccessible native protocol input/state", output)
                self.assertNotIn("256", output)


class QuotaSnapshotDepthTests(TempDir):
    def test_deep_snapshot_is_invalid_snapshot(self):
        environment = {"OMNILANE_QUOTA_DIR": str(self.root / "quota"),
                       "OMNILANE_QUOTA_THRESHOLD_PERCENT": "90",
                       "OMNILANE_QUOTA_MAX_AGE_SECONDS": "1800"}
        now = dt.datetime(2026, 10, 26, 0, 0, tzinfo=dt.timezone.utc)
        raw = deep_record(vendor="codex", window="weekly")
        with mock.patch.dict(os.environ, environment), \
                mock.patch.object(quota, "read_snapshot", return_value=raw):
            result = quota.snapshot("codex", self.root, now=now)
        self.assertEqual(result["reason"], "invalid-snapshot")

    def test_deep_decision_and_persist_arguments_are_refused(self):
        with self.assertRaises(json_depth.JSONDepthError):
            quota.decision(mock.Mock(snapshot=deep_record(vendor="codex")))


if __name__ == "__main__":
    unittest.main()
