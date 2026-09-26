#!/usr/bin/env python3
"""aa_rebaseline.py fill: a value AA withdrew keeps its earlier figure, and nothing else moves."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "aa_rebaseline.py"


class FillTests(unittest.TestCase):
    def test_fills_only_withdrawn_fields_and_lists_them(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            base = Path(tmp)
            earlier = base / "earlier.json"
            fresh = base / "fresh.json"
            out = base / "out.json"
            earlier.write_text(json.dumps({"records": {
                "model-a": {"intelligenceIndex": 40.0, "intelligenceIndexTimePerTask": 60.0, "gpqa": None},
                "model-b": {"intelligenceIndex": 30.0, "intelligenceIndexTimePerTask": 50.0},
            }}))
            fresh.write_text(json.dumps({"fetched_at": "t", "records": {
                "model-a": {"intelligenceIndex": 41.0, "intelligenceIndexTimePerTask": None, "gpqa": None},
                "model-b": {"intelligenceIndex": 30.0, "intelligenceIndexTimePerTask": 55.0},
                "model-c": {"intelligenceIndex": 20.0},
            }}))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "fill", "--extract", str(fresh), "--earlier", str(earlier),
                 "--out", str(out)],
                cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            filled = json.loads(out.read_text())
            records = filled["records"]
            self.assertEqual(60.0, records["model-a"]["intelligenceIndexTimePerTask"])
            self.assertEqual(41.0, records["model-a"]["intelligenceIndex"])
            self.assertIsNone(records["model-a"]["gpqa"])
            self.assertEqual(55.0, records["model-b"]["intelligenceIndexTimePerTask"])
            self.assertEqual({"intelligenceIndex": 20.0}, records["model-c"])
            self.assertEqual([{"slug": "model-a", "field": "intelligenceIndexTimePerTask"}],
                             filled["filled_from_earlier_capture"]["fields"])


if __name__ == "__main__":
    unittest.main()
