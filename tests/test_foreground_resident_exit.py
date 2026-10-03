"""A foreground Claude/Gemini dispatch returns as soon as the answer arrives.

Foreground dispatch cannot take follow-up messages, so it must not open a
resident session that only closes on the idle cap or the call timeout (which
recorded a correct answer as exit 124). Fake CLIs only; no provider is called.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]

# Resident (stream-json) mode answers the first message and then keeps reading
# until stdin closes, like the real CLIs; one-shot mode answers and exits.
FAKE_AGY = r'''#!/usr/bin/env python3
import json, sys
if "--input-format" in sys.argv[1:]:
    sys.stdin.readline()
    print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "OK"}}), flush=True)
    for _ in sys.stdin:
        pass
    sys.exit(0)
print("OK")
'''


class ForegroundResidentExitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-fg-resident-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.env, violations = isolated_environment(base / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home, bins, self.work = base / "home", base / "bin", base / "work"
        for directory in (self.home, bins, self.work):
            directory.mkdir()
        (Path(self.env["HOME"]) / ".gemini").mkdir(parents=True, exist_ok=True)
        agy = bins / "agy"
        agy.write_text(FAKE_AGY)
        agy.chmod(0o755)
        (self.home / "routing.local.yaml").write_text("fg-gemini: gemini gemini-3.7-flash-medium -\n")
        for key in list(self.env):
            if key.startswith("OMNILANE_"):
                self.env.pop(key)
        self.env.update(OMNILANE_HOME=str(self.home), OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
                        AGY_BIN=str(agy), GEMINI_BIN=str(agy), PYTHONDONTWRITEBYTECODE="1",
                        PATH=str(bins) + os.pathsep + self.env["PATH"])

    def dispatch(self, *flags):
        started = time.monotonic()
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--timeout", "20", "--workdir", str(self.work),
             *flags, "--vendor", "gemini", "fg-gemini", "Reply OK"],
            env=self.env, capture_output=True, text=True, timeout=90)
        return result, time.monotonic() - started

    def test_foreground_gemini_exits_after_answer(self):
        result, elapsed = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "OK")
        self.assertLess(elapsed, 15, "foreground dispatch waited for the call timeout")
        (job,) = (self.home / "jobs").iterdir()
        self.assertEqual(json.loads((job / "meta.json").read_text())["session_mode"], "single-shot")
        self.assertEqual((job / "exit").read_text(), "0\n")

    def test_background_gemini_still_resident_by_default(self):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--dry-run", "--background", "--workdir", str(self.work),
             "--vendor", "gemini", "fg-gemini", "Reply OK"],
            env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("session_mode=live", result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
