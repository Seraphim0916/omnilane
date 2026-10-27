import offline_env  # Activate suite isolation for direct file execution.

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as quota


class QuotaResetBoundaryTests(unittest.TestCase):
    def test_reset_microsecond_boundary_with_fresh_capture(self):
        now = dt.datetime(2026, 10, 27, 12, 0, 0, 123456, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            quota_dir = home / "quota"
            quota_dir.mkdir()
            environment = {
                "OMNILANE_QUOTA_DIR": str(quota_dir),
                "OMNILANE_QUOTA_THRESHOLD_PERCENT": "90",
                "OMNILANE_QUOTA_MAX_AGE_SECONDS": "1800",
            }
            for microseconds, fresh in ((-1, False), (0, False), (1, True)):
                with self.subTest(microseconds=microseconds):
                    value = dict(vendor="codex", window="weekly", used_percent=95,
                                 captured_at=(now - dt.timedelta(seconds=60)).isoformat(),
                                 resets_at=(now + dt.timedelta(microseconds=microseconds)).isoformat(),
                                 source="boundary-fixture")
                    (quota_dir / "codex.json").write_text(json.dumps(value), encoding="utf-8")
                    with patch.dict(os.environ, environment):
                        result = quota.snapshot("codex", home, now=now)
                    self.assertEqual(result["age_seconds"], 60)
                    self.assertLess(result["age_seconds"], result["max_age_seconds"])
                    self.assertEqual(result["resets_at"], value["resets_at"])
                    self.assertEqual(result["used_percent"], 95)
                    self.assertEqual(result["status"], "fresh" if fresh else "unknown")
                    self.assertEqual(result["freshness"], "fresh" if fresh else "stale")
                    self.assertEqual(result["reason"], "weekly-snapshot" if fresh else "stale-snapshot")
                    self.assertIs(result["near_limit"], fresh)


if __name__ == "__main__":
    unittest.main()
