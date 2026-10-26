import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import caller_identity
import native_context


class TomllibRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tomllib-refusal-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.thread = "11111111-1111-4111-8111-111111111111"
        self.environment = {
            "OMNILANE_HOME": str(self.base / "omnilane"),
            "CODEX_HOME": str(self.base / "codex"),
            "CODEX_THREAD_ID": self.thread,
            "OMNILANE_AA_CALLER_FROM_PROCESS": "1",
        }
        for patcher in (patch.object(caller_identity, "tomllib", None),
                        patch.dict(os.environ, self.environment)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def lookup_for(self, argv):
        # Inject only the process table; keep selector parsing and errors real.
        tree = {os.getpid(): (20, ["python3"]), 20: (1, argv)}
        return tree.get

    def run_command(self, command, argv):
        lookup = self.lookup_for(argv)
        stdout, stderr = io.StringIO(), io.StringIO()
        registry = ["--registry", str(ROOT / "config" / "aa-model-policy.json")]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if command == "whoami":
                code = caller_identity.main(
                    registry, environment=self.environment, lookup=lookup,
                    process_environment=lambda pid: self.environment)
            else:
                code = native_context.main(
                    registry + ["--workdir", str(self.base), "--no-host-rows",
                                "--out", str(self.base / "native.json")],
                    read_caller=lambda pid: caller_identity.read_caller(
                        pid, lookup, lambda child: self.environment, self.environment))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_model_override_requires_python_311_without_tomllib(self):
        forms = (["-c", 'model="gpt-5.6-sol"'],
                 ["--config", 'model="gpt-5.6-sol"'],
                 ['--config=model="gpt-5.6-sol"'],
                 ['-cmodel="gpt-5.6-sol"'], ['-c=model="gpt-5.6-sol"'])
        for flags in forms:
            with self.subTest(flags=flags):
                with self.assertRaisesRegex(ValueError, r"Python 3\.11\+.*tomllib"):
                    caller_identity.read_selector(["codex", "exec", *flags])

    def test_commands_refuse_model_override_with_exit_3(self):
        for command in ("whoami", "native-context"):
            with self.subTest(command=command):
                code, stdout, stderr = self.run_command(
                    command, ["codex", "exec", "-c", 'model="gpt-5.6-sol"'])
                self.assertEqual(code, 3, stderr)
                self.assertEqual(stdout, "")
                self.assertIn("cannot read the caller identity", stderr)
                self.assertRegex(stderr, r"Python 3\.11\+.*tomllib")
                self.assertFalse((self.base / "native.json").exists())
                self.assertFalse((self.base / "omnilane").exists())

    def test_model_flag_and_effort_remain_readable(self):
        for override in ([], ["-c", 'model="ignored-model"']):
            argv = ["codex", "exec", "-m", "gpt-5.6-sol", *override,
                    "-c", 'model_reasoning_effort="high"']
            with self.subTest(override=override):
                self.assertEqual(caller_identity.read_selector(argv),
                                 ("codex", "gpt-5.6-sol", "high"))
                for command in ("whoami", "native-context"):
                    with self.subTest(command=command):
                        code, stdout, stderr = self.run_command(command, argv)
                        self.assertEqual(code, 0, stderr)
                        self.assertTrue(Path(stdout.strip()).is_file())
                        self.assertNotIn("tomllib", stderr)

    def test_app_server_ignores_overrides_and_reads_current_turn(self):
        sessions = Path(self.environment["CODEX_HOME"]) / "sessions" / "2026/10/26"
        sessions.mkdir(parents=True)
        records = [
            {"type": "session_meta", "payload": {"id": self.thread}},
            {"type": "turn_context", "payload": {
                "turn_id": "synthetic-turn", "model": "gpt-5.6-sol", "effort": "high"}},
        ]
        (sessions / ("rollout-synthetic-" + self.thread + ".jsonl")).write_text(
            "".join(json.dumps(record) + "\n" for record in records))
        argv = ["codex", "-c", 'model="ignored-model"',
                "-c", 'model_reasoning_effort="low"', "app-server"]
        self.assertEqual(caller_identity.read_selector(argv), ("codex", None, None))
        identity = caller_identity.read_caller(
            os.getpid(), self.lookup_for(argv), lambda pid: self.environment, self.environment)
        self.assertEqual(identity[1], ("codex", "gpt-5.6-sol", "high"))
        for command in ("whoami", "native-context"):
            with self.subTest(command=command):
                code, stdout, stderr = self.run_command(command, argv)
                self.assertEqual(code, 0, stderr)
                self.assertTrue(Path(stdout.strip()).is_file())
                self.assertNotIn("tomllib", stderr)


if __name__ == "__main__":
    unittest.main()
