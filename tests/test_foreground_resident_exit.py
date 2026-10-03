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

# Resident (stream-json input) mode answers the first message and then keeps
# reading until stdin closes, like the real CLIs; one-shot mode answers and exits.
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

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, sys
if "--input-format" in sys.argv[1:]:
    sys.stdin.readline()
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK"}), flush=True)
    for _ in sys.stdin:
        pass
    sys.exit(0)
print("OK")
'''

LANES = {"gemini": "fg-gemini", "claude": "fg-claude"}


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
        agy, claude = bins / "agy", bins / "claude"
        agy.write_text(FAKE_AGY)
        claude.write_text(FAKE_CLAUDE)
        for fake in (agy, claude):
            fake.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(
            "fg-gemini: gemini gemini-3.7-flash-medium -\n"
            "fg-claude: claude claude-sonnet-5 -\n")
        for key in list(self.env):
            if key.startswith("OMNILANE_"):
                self.env.pop(key)
        self.env.update(OMNILANE_HOME=str(self.home), OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
                        AGY_BIN=str(agy), GEMINI_BIN=str(agy), CLAUDE_BIN=str(claude),
                        PYTHONDONTWRITEBYTECODE="1", PATH=str(bins) + os.pathsep + self.env["PATH"])

    def run_dispatch(self, vendor, *flags, timeout=90):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--workdir", str(self.work), *flags,
             "--vendor", vendor, LANES[vendor], "Reply OK"],
            env=self.env, capture_output=True, text=True, timeout=timeout)

    def check_foreground(self, vendor):
        jobs = self.home / "jobs"
        before = set(jobs.iterdir()) if jobs.exists() else set()
        started = time.monotonic()
        result = self.run_dispatch(vendor, "--timeout", "20")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, f"{vendor}: {result.stderr}")
        self.assertEqual(result.stdout.strip(), "OK", vendor)
        self.assertLess(elapsed, 15, f"{vendor}: foreground dispatch waited for the call timeout")
        (job,) = set(jobs.iterdir()) - before
        self.assertEqual(json.loads((job / "meta.json").read_text())["session_mode"], "single-shot", vendor)
        self.assertEqual((job / "exit").read_text(), "0\n", vendor)

    def test_foreground_gemini_exits_after_answer(self):
        self.check_foreground("gemini")

    def test_foreground_claude_exits_after_answer(self):
        self.check_foreground("claude")

    def test_background_still_resident_by_default(self):
        for vendor in LANES:
            result = self.run_dispatch(vendor, "--dry-run", "--background", timeout=30)
            self.assertEqual(result.returncode, 0, f"{vendor}: {result.stderr}")
            self.assertIn("session_mode=live", result.stdout.splitlines(), vendor)


if __name__ == "__main__":
    unittest.main()
