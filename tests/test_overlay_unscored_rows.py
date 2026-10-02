"""The overlay build leaves out a probed row the registry does not score instead of aborting."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import build_overlay  # noqa: E402
import probe_sweep  # noqa: E402
import resign  # noqa: E402


class UnscoredProvenTests(unittest.TestCase):
    def test_probed_row_missing_from_the_registry_is_reported_not_indexed(self):
        some = next(cid for cid in sorted(build_overlay.PROVEN) if cid in build_overlay.ROWS)
        rows = {cid: row for cid, row in build_overlay.ROWS.items() if cid != some}
        self.assertEqual(build_overlay.unscored_proven({some: {}, "not/proven": {}}, rows), [some])

    def test_scored_and_unprobed_rows_are_not_reported(self):
        some = next(cid for cid in sorted(build_overlay.PROVEN) if cid in build_overlay.ROWS)
        self.assertEqual(build_overlay.unscored_proven({some: {}}), [])
        self.assertEqual(build_overlay.unscored_proven({}), [])


class UnscoredProvenProbePlanTests(unittest.TestCase):
    """A PROVEN id the registry does not score must not abort the probe plan or the re-sign check."""

    def setUp(self):
        self.added = "grok/not-in-the-registry"
        build_overlay.PROVEN[self.added] = ("cli_reasoning_effort", "grok-0", "gk-not-in-the-registry")
        self.addCleanup(build_overlay.PROVEN.pop, self.added, None)

    def test_probe_plan_skips_it(self):
        names = {entry["name"] for entry in probe_sweep.plan("grok")}
        self.assertNotIn("gk-not-in-the-registry", names)
        self.assertTrue(names)

    def test_never_probed_list_skips_it(self):
        scored = next(cid for cid in sorted(build_overlay.PROVEN)
                      if cid in build_overlay.ROWS and build_overlay.ROWS[cid]["vendor"] == "grok")
        overlay = {"mappings": [{"config_id": scored}], "unproven": []}
        self.assertNotIn(self.added, resign.unprobed(overlay, "grok"))


if __name__ == "__main__":
    unittest.main()
