import offline_env  # Activate suite isolation for direct file execution.

import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import self_exec


class SinceOverflowTests(unittest.TestCase):
    moment = dt.datetime(2026, 11, 3, 15, 30, tzinfo=dt.timezone.utc)
    error = "omnilane: --since takes Nd, Nh or YYYY-MM-DD"

    def assert_invalid(self, text, moment=None):
        with self.assertRaises(SystemExit) as raised:
            self_exec.since_cutoff(text, moment or self.moment)
        self.assertEqual(raised.exception.code, self.error)

    def test_huge_days(self):
        self.assert_invalid("999999999999d")

    def test_huge_hours(self):
        self.assert_invalid("999999999999999h")

    def test_subtraction_out_of_range(self):
        self.assert_invalid("999999999h")

    def test_timedelta_value_error(self):
        with patch.object(self_exec.datetime, "timedelta", side_effect=ValueError):
            self.assert_invalid("7d")

    def test_subtraction_value_error(self):
        class InvalidMoment:
            def __sub__(self, other):
                raise ValueError("date out of range")

        self.assert_invalid("12h", InvalidMoment())

    def test_valid_windows_unchanged(self):
        for text, expected in (
            ("7d", dt.datetime(2026, 10, 27, 15, 30, tzinfo=dt.timezone.utc)),
            ("12h", dt.datetime(2026, 11, 3, 3, 30, tzinfo=dt.timezone.utc)),
            ("2026-11-01", dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)),
        ):
            with self.subTest(text=text):
                self.assertEqual(self_exec.since_cutoff(text, self.moment), expected)


if __name__ == "__main__":
    unittest.main()
