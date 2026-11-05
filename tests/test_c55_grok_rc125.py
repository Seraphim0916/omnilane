"""Exercise Grok retry boundaries through the real runner with an offline CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class GrokRC125Tests(unittest.TestCase):
    def run_case(self, codes, expected_attempts, expected_rc, error="", output=""):
        with tempfile.TemporaryDirectory(prefix="c55-grok-") as directory:
            base = Path(directory)
            env, violations = isolated_environment(base / "tools", os.environ["PATH"])
            prompt = base / "prompt"
            prompt.write_text("Return an offline fixture response.\n")
            fake = base / "grok"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys\n"
                "counter = pathlib.Path(os.environ['COUNTER'])\n"
                "n = int(counter.read_text()) if counter.exists() else 0\n"
                "counter.write_text(str(n + 1))\n"
                "codes = os.environ['CODES'].split(',')\n"
                "sys.stdout.write(os.environ['OUTPUT'])\n"
                "sys.stderr.write(os.environ['ERROR'])\n"
                "sys.exit(int(codes[min(n, len(codes) - 1)]))\n")
            fake.chmod(0o700)
            destination = base / "output"
            env.update(GROK_BIN=str(fake), OMNILANE_HOME=str(base / "home"),
                       OMNILANE_JOB_SUPERVISED="1", OMNILANE_TIMEOUT="5",
                       OMNILANE_GROK_MAX_ATTEMPTS="3", COUNTER=str(base / "counter"),
                       CODES=codes, ERROR=error, OUTPUT=output)
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/runners/run-grok.sh"), "advise",
                 str(base), "grok-test", "-", str(prompt), str(destination)],
                env=env, capture_output=True, text=True, timeout=15)
            self.assertFalse(violations.exists())
            self.assertEqual(expected_rc, result.returncode, result.stderr)
            self.assertEqual(expected_attempts, int((base / "counter").read_text()))
            log = Path(str(destination) + ".stderr.log")
            diagnostic = log.read_text() if log.exists() else ""
            if not output:
                self.assertIn("after {} attempts".format(expected_attempts), diagnostic)
            if expected_rc == 125:
                self.assertIn("exit 125 is not retryable", diagnostic)
            self.assertEqual(output, destination.read_text())

    def test_125_stops_first_attempt(self):
        self.run_case("125", 1, 125)

    def test_125_stops_after_retry(self):
        self.run_case("1,125", 2, 125)

    def test_125_preserves_partial_output(self):
        self.run_case("125", 1, 125, output="partial\n")

    def test_auth_error_reports_actual_attempts(self):
        self.run_case("1", 1, 1, error="401 unauthorized\n")

    def test_empty_success_exhausts_exact_limit(self):
        self.run_case("0", 3, 1)

    def test_retryable_failure_exhausts_exact_limit(self):
        self.run_case("7", 3, 7)

    def test_nonempty_success_stops(self):
        self.run_case("0", 1, 0, output="OK\n")
