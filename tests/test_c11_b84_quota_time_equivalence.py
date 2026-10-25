import offline_env  # Activate suite isolation for direct file execution.

import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from quota_preflight import timestamp


class QuotaTimeEquivalenceTests(unittest.TestCase):
    def test_equivalent_instants_and_standard_round_trip(self):
        expected = dt.datetime(2026, 10, 26, 0, 0, 0, 123456, tzinfo=dt.timezone.utc)
        forms = (
            "2026-10-26T00:00:00.123456Z",
            "2026-10-26T00:00:00.123456z",
            "2026-10-26T08:00:00.123456+0800",
            "2026-10-26T08:00:00.123456+08:00",
            "2026-10-25T19:00:00.123456-0500",
            "2026-10-25T19:00:00.123456-05:00",
            "2026-10-26T08:00:00,123456+08:00",
            "2026-10-26T00:00:00.123456999Z",
            "2026-10-26T08:00:00,123456999+0800",
        )
        for text in forms:
            with self.subTest(text=text):
                parsed = timestamp(text)
                self.assertEqual(parsed, expected)
                self.assertEqual(parsed.microsecond, 123456)
                canonical = parsed.astimezone(dt.timezone.utc).isoformat()
                self.assertEqual(canonical, "2026-10-26T00:00:00.123456+00:00")
                self.assertEqual(timestamp(canonical), parsed)
                self.assertEqual(timestamp(parsed.isoformat()), parsed)

    def test_fraction_padding_and_truncation_never_round(self):
        for fraction, micros in (("1", 100000), ("123", 123000), ("000001", 1),
                                 ("123456999", 123456), ("999999999", 999999),
                                 ("000000999", 0)):
            for separator in (".", ","):
                with self.subTest(fraction=fraction, separator=separator):
                    parsed = timestamp(f"2026-10-26T00:00:00{separator}{fraction}Z")
                    expected = dt.datetime(2026, 10, 26, microsecond=micros,
                                           tzinfo=dt.timezone.utc)
                    self.assertEqual(parsed, expected)
                    self.assertEqual(parsed.microsecond, micros)
                    self.assertEqual(timestamp(parsed.isoformat()), expected)


if __name__ == "__main__":
    unittest.main()
