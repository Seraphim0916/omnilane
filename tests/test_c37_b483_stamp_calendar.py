"""C2-B483: parse_stamp accepts only a real calendar moment written as YYYY-MM-DDTHH:MM:SSZ, and answers in UTC."""

from __future__ import annotations
import offline_env  # noqa: F401
import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import self_exec


class StampCalendarTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"OMNILANE_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)

    def test_leap_day_parses_in_utc(self):
        value = self_exec.parse_stamp("2024-02-29T23:59:58Z")
        self.assertEqual(value, datetime.datetime(2024, 2, 29, 23, 59, 58, tzinfo=datetime.timezone.utc))
        self.assertIs(value.tzinfo, datetime.timezone.utc)
        self.assertEqual(value.utcoffset(), datetime.timedelta(0))

    def test_round_trip_with_stamp(self):
        moment = datetime.datetime(2024, 2, 29, 0, 0, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(self_exec.parse_stamp(self_exec.stamp(moment)), moment)

    def test_impossible_dates_are_none(self):
        for text in ("2026-02-29T12:00:00Z", "2100-02-29T00:00:00Z", "2026-04-31T00:00:00Z",
                     "2026-13-01T00:00:00Z", "2026-01-01T24:00:00Z"):
            with self.subTest(text=text):
                self.assertIsNone(self_exec.parse_stamp(text))

    def test_other_shapes_are_none(self):
        for text in ("2024-02-29T12:00:00", "2024-02-29T12:00:00+00:00", "2024-02-29T12:00:00+08:00",
                     "2024-02-29 12:00:00Z", "2024-02-29T12:00:00.5Z", "2024-02-29", ""):
            with self.subTest(text=text):
                self.assertIsNone(self_exec.parse_stamp(text))

    def test_non_strings_are_none(self):
        for value in (None, 0, 20240229, 1.5):
            with self.subTest(value=value):
                self.assertIsNone(self_exec.parse_stamp(value))


if __name__ == "__main__":
    unittest.main()
