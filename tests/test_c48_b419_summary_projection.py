import offline_env  # Activate suite isolation for direct file execution.

import copy
import itertools
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class SummaryProjectionTests(unittest.TestCase):
    def test_order_and_projection_are_stable_without_mutation(self):
        vendors = ("claude", "codex", "grok")
        expected = "; ".join(
            vendor + ": verification-busy; next: wait for the running verification, then retry"
            for vendor in vendors)
        marker = "B419_PRIVATE_EXTRA"
        extras = (
            {"diagnostic": marker},
            {"vendor": marker, "config_ids": [marker], "attempts": marker},
            {"nested": {"output": [marker]}, "last_seen": marker, "origin_reason": marker},
        )
        for order in itertools.permutations(vendors):
            for extra in extras:
                with self.subTest(order=order, extra=extra):
                    state = {"schema_version": 1, "extra": {"output": marker}, "pending": {
                        vendor: dict(copy.deepcopy(extra), reason="verification-busy")
                        for vendor in order}}
                    before = copy.deepcopy(state)
                    with patch.object(status, "load", return_value=state):
                        summary = status.pending_summary()
                        self.assertEqual(status.pending_summary(), summary)
                    self.assertEqual(summary, expected)
                    self.assertNotIn(marker, summary)
                    self.assertEqual(state, before)
                    self.assertEqual(list(state["pending"]), list(order))


if __name__ == "__main__":
    unittest.main()
