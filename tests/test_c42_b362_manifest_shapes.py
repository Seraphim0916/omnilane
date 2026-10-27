import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy
import overlay_diagnostics as diagnostics


class ManifestShapesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.manifest = self.base / "probe-manifest.json"
        self.overlay = {"unproven": [{"config_id": "unknown/model"}],
                        "evidence": [{"path": str(self.manifest)}]}
        self.registry = {"scored_configs": []}

    def rows(self, manifest):
        self.manifest.write_text(json.dumps(manifest))
        return diagnostics.unproven_rows(self.overlay, self.registry)

    def test_rejects_invalid_mapping_shapes(self):
        cases = [None, [], "bad", 1,
                 {"unknown/model": None}, {"unknown/model": []},
                 {"unknown/model": {"json": None}},
                 {"unknown/model": {"json": []}},
                 {"unknown/model": {"stderr": "bad"}},
                 {"unknown/model": {"json": {"path": []}}},
                 {"unknown/model": {"stderr": {"path": 1}}}]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "failed_probe_runs"):
                    self.rows({"failed_probe_runs": value})

    def test_legacy_missing_and_valid_mappings(self):
        for manifest in ({}, {"failed_probe_runs": {}},
                         {"failed_probe_runs": {"unknown/model": {}}}):
            self.assertEqual(self.rows(manifest)[0]["evidence_paths"], [])
        value = {"failed_probe_runs": {"unknown/model": {
            "stderr": {"path": str(self.base / "stderr.txt")}}}}
        self.assertEqual(self.rows(value)[0]["evidence_paths"],
                         [str(self.base / "stderr.txt")])

    def test_health_entry_emits_controlled_failure(self):
        self.manifest.write_text('{"failed_probe_runs": null}')
        overlay = self.base / "overlay.json"
        overlay.write_text(json.dumps(self.overlay))
        stdout = io.StringIO()
        with patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": str(overlay)}), \
                patch.object(sys, "argv", ["overlay_health.py", str(ROOT)]), \
                patch.object(aa_policy, "load_registry", return_value=(self.registry, "")), \
                contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(ROOT / "scripts/lib/overlay_health.py"), run_name="__main__")
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("FAIL\tinvalid overlay diagnostics:", stdout.getvalue())
        self.assertNotIn("PASS\t", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
