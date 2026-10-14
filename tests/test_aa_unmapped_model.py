"""AA-only coverage must not manufacture a CLI selector or transport proof."""

import offline_env
import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("aa_unmapped", ROOT / "scripts/aa_rebaseline.py")
aa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aa)


class UnmappedModelTests(unittest.TestCase):
    def test_build_adds_measured_row_without_inheriting_a_transport(self):
        base = json.loads(aa.REGISTRY.read_text())
        cid = "gemini/gemini-4-argon"
        base["scored_configs"] = [r for r in base["scored_configs"] if r["id"] != cid]
        base["aliases"] = [r for r in base["aliases"] if r["catalog_model"] != "gemini-4-argon"]
        with tempfile.TemporaryDirectory(prefix="aa-unmapped-") as temp:
            registry = Path(temp) / "policy.json"
            registry.write_text(json.dumps(base))
            args = argparse.Namespace(
                base=str(registry),
                extract=str(ROOT / "docs/reports/aa-v4.3.2-extract-2026-10-14.json"),
                as_of="2026-10-14", revision=1, approval="proposed",
            )
            with patch.object(aa, "REGISTRY", registry), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aa.cmd_build(args), 0)
                first = registry.read_bytes()
                self.assertEqual(aa.cmd_build(args), 0)
                self.assertEqual(first, registry.read_bytes())
            result = json.loads(first)
        row = next(r for r in result["scored_configs"] if r["id"] == cid)
        self.assertEqual((row["score_raw"], row["score"], row["estimated"]), (52.56, 53, False))
        self.assertEqual(row["transport_mapping"]["candidate_model_ids"], [])
        self.assertEqual(row["transport_mapping"]["status"], "unknown")
        self.assertFalse(row["transport_mapping"]["runtime_verified"])
        self.assertIn(row, aa.value_pool(result["scored_configs"]))
        alias = next(r for r in result["aliases"] if r["catalog_model"] == "gemini-4-argon")
        self.assertEqual(alias["candidate_config_ids"], [cid])
        self.assertNotEqual(alias["catalog_source"], "scripts/configure.sh")
        self.assertEqual(alias["status"], "unknown")
        self.assertIsNone(alias["resolved_config_id"])
        self.assertFalse(alias["runtime_verified"])


if __name__ == "__main__":
    unittest.main()
