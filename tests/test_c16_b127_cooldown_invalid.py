import offline_env  # Activate suite isolation for direct file execution.

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from probe_failure import cooldown


class CooldownInvalidTests(unittest.TestCase):
    NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

    def test_invalid_timestamps_fail_closed(self):
        cases = (True, 1, {}, [], 1.5, "", "not-a-date",
                 "2026-02-30T00:00:00+00:00", "2026-10-25T00:00:00",
                 "9999-12-31T23:59:59+00:00")
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(cooldown(value, now=self.NOW),
                                 (True, "invalid failure timestamp"))

    def test_future_timestamp_remains_ineligible(self):
        self.assertEqual(cooldown("2026-10-27T12:00:00Z", now=self.NOW),
                         (True, "2026-10-28T08:00:00+00:00"))

    def test_missing_legacy_timestamp_can_retry(self):
        self.assertEqual(cooldown(None, now=self.NOW), (False, None))


if __name__ == "__main__":
    unittest.main()
