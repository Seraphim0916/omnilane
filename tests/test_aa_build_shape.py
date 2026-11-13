"""build copies a new row's transport shape from a named base row, or from a sibling of its vendor.

A new row names the base row whose shape it copies. When that row lost its score, build takes
any scored row of the same vendor; when the base scores no row of that vendor, it stops.
"""
import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
import aa_rebaseline  # noqa: E402

EXTRACT = REPO / "docs/reports/aa-v5.0.1-extract-2026-11-13.json"
TARGET = "claude/claude-fable-5-2-low"


class BuildShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(aa_rebaseline.REGISTRY.read_bytes())

    def build(self, base, new_rows):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.json"
            base_path.write_text(json.dumps(base))
            output = Path(tmp) / "policy.json"
            args = SimpleNamespace(extract=str(EXTRACT), base=str(base_path), as_of="2026-11-10",
                                   revision=1, approval="proposed")
            with patch.object(aa_rebaseline, "REGISTRY", output), \
                    patch.object(aa_rebaseline, "NEW_ROWS", new_rows), \
                    contextlib.redirect_stdout(io.StringIO()):
                aa_rebaseline.cmd_build(args)
            return output.read_bytes()

    def without(self, keep):
        return dict(self.registry, scored_configs=[r for r in self.registry["scored_configs"] if keep(r)])

    def test_a_missing_shape_falls_back_to_a_scored_row_of_the_vendor(self):
        # Without the Fable 5.2 rows in the base, build adds them again from NEW_ROWS.
        base = self.without(lambda r: not r["id"].startswith("claude/claude-fable-5-2"))
        shape = next(r[-1] for r in aa_rebaseline.NEW_ROWS if r[0] == TARGET)
        self.assertIn(shape, {r["id"] for r in base["scored_configs"]})
        missing = [tuple(row[:-1]) + ("claude/no-such-row",) if row[0] == TARGET else row
                   for row in aa_rebaseline.NEW_ROWS]
        self.assertNotIn("claude/no-such-row", {r["id"] for r in base["scored_configs"]})
        rebuilt = self.build(base, missing)
        self.assertIn(TARGET, {r["id"] for r in json.loads(rebuilt)["scored_configs"]})
        self.assertEqual(rebuilt, self.build(base, aa_rebaseline.NEW_ROWS))

    def test_a_vendor_with_no_scored_base_row_stops_the_build(self):
        base = self.without(lambda r: r["vendor"] != "claude")
        with self.assertRaisesRegex(SystemExit, "which the base does not score, "
                                                "and the base scores no other claude row"):
            self.build(base, aa_rebaseline.NEW_ROWS)


if __name__ == "__main__":
    unittest.main()
