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


class QuotaEdgeTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 26, 0, 0, tzinfo=dt.timezone.utc)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.environment = {
            "OMNILANE_QUOTA_DIR": str(self.home / "quota"),
            "OMNILANE_QUOTA_THRESHOLD_PERCENT": "90",
            "OMNILANE_QUOTA_MAX_AGE_SECONDS": "1800",
        }

    def snapshot(self, age, used=90, reset_delta=3600):
        value = dict(vendor="codex", window="weekly", used_percent=used,
                     captured_at=(self.now - dt.timedelta(seconds=age)).isoformat(),
                     resets_at=(self.now + dt.timedelta(seconds=reset_delta)).isoformat(),
                     source="memory-fixture")
        with patch.dict(os.environ, self.environment), patch.object(
                quota, "read_snapshot", return_value=json.dumps(value)) as read:
            result = quota.snapshot("codex", self.home, now=self.now)
        read.assert_called_once_with(self.home / "quota")
        self.assertEqual(result["source"], value["source"])
        self.assertEqual(result["captured_at"], value["captured_at"])
        self.assertEqual(result["used_percent"], used)
        return result

    def assert_state(self, result, fresh, near_limit):
        self.assertEqual(result["status"], "fresh" if fresh else "unknown")
        self.assertEqual(result["freshness"], "fresh" if fresh else "stale")
        self.assertEqual(result["reason"], "weekly-snapshot" if fresh else "stale-snapshot")
        self.assertIs(result["near_limit"], near_limit)

    def test_maximum_age_one_microsecond_edges(self):
        for age, fresh in ((1799.999999, True), (1800, True), (1800.000001, False)):
            with self.subTest(age=age):
                self.assert_state(self.snapshot(age), fresh, fresh)

    def test_threshold_equal_and_just_below(self):
        for used, near_limit in ((90, True), (89.999999, False)):
            with self.subTest(used=used):
                self.assert_state(self.snapshot(0, used=used), True, near_limit)

    def test_reset_one_microsecond_edges(self):
        for delta, fresh in ((0.000001, True), (0, False), (-0.000001, False)):
            with self.subTest(reset_delta=delta):
                self.assert_state(self.snapshot(60, reset_delta=delta), fresh, fresh)


if __name__ == "__main__":
    unittest.main()
