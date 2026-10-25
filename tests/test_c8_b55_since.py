import offline_env  # Activate suite isolation for direct file execution.
import datetime
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import self_exec


class SinceTests(unittest.TestCase):
    def test_normal_periods_and_date(self):
        now = datetime.datetime(2026, 10, 26, tzinfo=datetime.timezone.utc)
        for text, expected in (("7d", now - datetime.timedelta(days=7)),
                               ("24h", now - datetime.timedelta(hours=24)),
                               ("2026-10-01", now.replace(day=1))):
            with self.subTest(text=text):
                self.assertEqual(self_exec.since_cutoff(text, now), expected)

    def test_overflows_are_cli_parameter_errors(self):
        for text in ("999999999999999999999d", "999999999d", "9" * 5000 + "h"):
            with self.subTest(length=len(text)):
                result = subprocess.run(
                    ["bash", str(ROOT / "bin/omnilane"), "stats", "self-exec",
                     "--since", text, "--json"], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("omnilane: --since", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
