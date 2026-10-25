import offline_env  # Activate suite isolation for direct file execution.

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import transport_compat


class ExpiryBoundaryTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(transport_compat, "datetime", wraps=datetime)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.clock.now.return_value = datetime(2026, 10, 26, tzinfo=timezone.utc)

    def test_equal_instants_in_different_timezones_expire(self):
        for stamp in ("2026-10-26T00:00:00Z", "2026-10-26T08:00:00+08:00",
                      "2026-10-25T19:00:00-05:00"):
            with self.subTest(stamp=stamp):
                self.assertIs(transport_compat.expired(stamp), True)
        self.clock.now.assert_called_with(timezone.utc)

    def test_microsecond_boundaries(self):
        self.assertIs(transport_compat.expired("2026-10-25T23:59:59.999999Z"), True)
        self.assertIs(transport_compat.expired("2026-10-26T00:00:00.000001Z"), False)

    def test_none_does_not_expire(self):
        self.assertIs(transport_compat.expired(None), False)
        self.clock.now.assert_not_called()

    def test_naive_and_invalid_values_are_rejected(self):
        for value in ("2026-10-26T00:00:00", "not-a-date", "", 42, 1.5,
                      True, [], {}, b"2026-10-26T00:00:00Z"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(aa_policy.PolicyError,
                                            "^invalid transport evidence expiry$"):
                    transport_compat.expired(value)


if __name__ == "__main__":
    unittest.main()
