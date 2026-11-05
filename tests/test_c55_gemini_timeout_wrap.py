"""Run both non-live Gemini paths against an offline, deadline-ignoring CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class GeminiTimeoutWrapTests(unittest.TestCase):
    def run_case(self, thread, delay):
        with tempfile.TemporaryDirectory(prefix="c55-gemini-") as directory:
            base = Path(directory)
            env, violations = isolated_environment(base / "tools", os.environ["PATH"])
            (Path(env["HOME"]) / ".gemini").mkdir()
            prompt = base / "prompt"
            prompt.write_text("Return an offline fixture response.\n")
            fake = base / "agy"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys, time\n"
                "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
                "time.sleep(float(os.environ['DELAY']))\n"
                "print(json.dumps({'status': 'SUCCESS', 'response': 'OK'}) "
                "if '--output-format' in sys.argv else 'OK')\n")
            fake.chmod(0o700)
            output = base / "output"
            env.update(AGY_BIN=str(fake), OMNILANE_HOME=str(base / "home"),
                       OMNILANE_JOB_SUPERVISED="1", OMNILANE_TIMEOUT="1",
                       CAPTURE=str(base / "capture"), DELAY=str(delay))
            if thread:
                env.update(OMNILANE_THREAD_MODE="resume", OMNILANE_THREAD_ID="c55-thread")
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/runners/run-gemini.sh"), "advise",
                 str(base), "-", "-", str(prompt), str(output)],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertFalse(violations.exists())
            self.assertTrue((base / "capture").exists(), result.stderr)
            self.assertIn('--print-timeout', (base / "capture").read_text())
            self.assertEqual(142 if delay else 0, result.returncode, result.stderr)
            if not delay:
                self.assertEqual("OK\n", output.read_text())

    def test_plain_deadline(self):
        self.run_case(False, 3)

    def test_thread_deadline(self):
        self.run_case(True, 3)

    def test_plain_success(self):
        self.run_case(False, 0)

    def test_thread_success(self):
        self.run_case(True, 0)
