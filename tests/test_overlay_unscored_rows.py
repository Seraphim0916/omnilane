"""The overlay build leaves out a probed row the registry does not score instead of aborting."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import build_overlay  # noqa: E402


class UnscoredProvenTests(unittest.TestCase):
    def test_probed_row_missing_from_the_registry_is_reported_not_indexed(self):
        some = next(cid for cid in sorted(build_overlay.PROVEN) if cid in build_overlay.ROWS)
        rows = {cid: row for cid, row in build_overlay.ROWS.items() if cid != some}
        self.assertEqual(build_overlay.unscored_proven({some: {}, "not/proven": {}}, rows), [some])

    def test_scored_and_unprobed_rows_are_not_reported(self):
        some = next(cid for cid in sorted(build_overlay.PROVEN) if cid in build_overlay.ROWS)
        self.assertEqual(build_overlay.unscored_proven({some: {}}), [])
        self.assertEqual(build_overlay.unscored_proven({}), [])


if __name__ == "__main__":
    unittest.main()
