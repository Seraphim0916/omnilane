"""A lane whose whole chain is "off" is reported as disabled, not as an unprovable target."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts" / "dispatch.sh"


class LaneDisabledTests(unittest.TestCase):
    def run_lane(self, table: str, lane: str) -> subprocess.CompletedProcess:
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-lane-disabled-")
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name) / "home"
        home.mkdir()
        (home / "routing.local.yaml").write_text(table, encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if not key.startswith("OMNILANE_")}
        env.update(OMNILANE_HOME=str(home), OMNILANE_AA_CALLER_FROM_PROCESS="0",
                   CODEX_BIN=str(home / "missing-codex"))
        return subprocess.run(
            ["/bin/bash", str(DISPATCH), "--operator-asserted-human", "--dry-run", lane, "probe"],
            capture_output=True, text=True, env=env, timeout=60)

    def test_all_off_chain_is_lane_disabled(self):
        for table in ("probe: off - -\n", "probe: off\n", "probe: off - - | off\n"):
            result = self.run_lane(table, "probe")
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("is disabled in routing config", result.stderr)
            self.assertIn('"code":"lane-disabled"', result.stderr)
            self.assertNotIn("unknown-target-runtime", result.stderr)

    def test_chain_with_a_real_candidate_is_not_lane_disabled(self):
        result = self.run_lane("probe: codex unavailable-model low | off\n", "probe")
        self.assertNotIn('"code":"lane-disabled"', result.stderr)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
