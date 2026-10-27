import offline_env  # Activate suite isolation for direct file execution.

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class SummaryRejectionTests(unittest.TestCase):
    def test_invalid_entries_are_rejected_without_echoing_input(self):
        marker = "B417_PRIVATE_INPUT"
        cases = [
            (marker, {"reason": "verification-busy"}, "invalid local verification status"),
            ("codex", marker, "invalid local verification status"),
            ("codex", [marker], "invalid local verification status"),
            ("codex", None, "invalid local verification status"),
            ("codex", 17, "invalid local verification status"),
            ("codex", {"extra": marker}, "invalid local verification status reason"),
            ("codex", {"reason": marker}, "invalid local verification status reason"),
        ]
        for vendor, entry, message in cases:
            with self.subTest(vendor=vendor, entry=entry):
                state = {"schema_version": 1, "pending": {vendor: entry}}
                with patch.object(status, "load", return_value=state) as loader:
                    with self.assertRaises(ValueError) as caught:
                        status.pending_summary()
                loader.assert_called_once_with()
                self.assertEqual(str(caught.exception), message)
                self.assertNotIn(marker, str(caught.exception))

    def test_valid_entry_is_accepted_by_the_same_summary_entrypoint(self):
        state = {"schema_version": 1, "pending": {"codex": {"reason": "verification-busy"}}}
        with patch.object(status, "load", return_value=state):
            self.assertEqual(status.pending_summary(),
                             "codex: verification-busy; next: wait for the running verification, then retry")


if __name__ == "__main__":
    unittest.main()
