import offline_env  # Activate suite isolation for direct file execution.

import datetime as dt
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as quota


class HugeQuotaNumbersTests(unittest.TestCase):
    def snapshot(self, used):
        now = dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc)
        raw = json.dumps(dict(vendor="codex", window="weekly", used_percent=used,
                              captured_at=now.isoformat(),
                              resets_at=(now + dt.timedelta(days=1)).isoformat(),
                              source="unit-test"))
        home = Path(__file__).resolve().parent
        with patch.dict(os.environ, {
            "OMNILANE_QUOTA_DIR": str(home / "quota"),
            "OMNILANE_QUOTA_THRESHOLD_PERCENT": "90",
            "OMNILANE_QUOTA_MAX_AGE_SECONDS": "1800",
        }), patch.object(quota, "read_snapshot", return_value=raw) as reader:
            result = quota.snapshot("codex", home, now=now)
        reader.assert_called_once_with(home / "quota")
        return result

    def test_huge_integer_overflow_is_unknown(self):
        for used in (10**400, -(10**400)):
            with self.subTest(used=used):
                result = self.snapshot(used)
                self.assertEqual((result["status"], result["reason"]),
                                 ("unknown", "invalid-snapshot"))
                self.assertFalse(result["near_limit"])
                self.assertNotIn("used_percent", result)

    def test_other_invalid_numbers_are_unknown(self):
        for used in (float("nan"), float("inf"), -float("inf"), True, False, -1, 101):
            with self.subTest(used=used):
                result = self.snapshot(used)
                self.assertEqual((result["status"], result["reason"]),
                                 ("unknown", "invalid-snapshot"))

    def test_valid_endpoints_remain_fresh(self):
        for used, near_limit in ((0, False), (100, True)):
            with self.subTest(used=used):
                result = self.snapshot(used)
                self.assertEqual((result["status"], result["reason"]),
                                 ("fresh", "weekly-snapshot"))
                self.assertEqual(result["used_percent"], used)
                self.assertIs(result["near_limit"], near_limit)


if __name__ == "__main__":
    unittest.main()
