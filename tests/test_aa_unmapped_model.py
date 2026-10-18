"""Known Argon selectors remain candidates until each host proves transport."""

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
    def test_build_adds_measured_row_with_selector_but_without_transport_proof(self):
        base = json.loads(aa.REGISTRY.read_text())
        cid = "gemini/gemini-4-argon"
        legacy = next(r.copy() for r in base["aliases"]
                      if r["catalog_model"] in ("gemini-4-argon", "gemini-4-argon-high"))
        legacy.update(catalog_model="gemini-4-argon", catalog_source="scripts/aa_rebaseline.py",
                      source_kind="aa_model_not_runtime_identity")
        base["scored_configs"] = [r for r in base["scored_configs"] if r["id"] != cid]
        base["aliases"] = [r for r in base["aliases"] if not r["catalog_model"].startswith("gemini-4-argon")]
        base["aliases"].append(legacy)
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
        self.assertEqual(row["transport_mapping"]["candidate_model_ids"], ["gemini-4-argon-high"])
        self.assertEqual(row["transport_mapping"]["status"], "unknown")
        self.assertFalse(row["transport_mapping"]["runtime_verified"])
        self.assertIn(row, aa.value_pool(result["scored_configs"]))
        alias = next(r for r in result["aliases"] if r["catalog_model"] == "gemini-4-argon-high")
        self.assertFalse(any(r["catalog_model"] == "gemini-4-argon" for r in result["aliases"]))
        self.assertEqual(alias["candidate_config_ids"], [cid])
        self.assertEqual(alias["catalog_source"], "scripts/configure.sh")
        self.assertEqual(alias["status"], "unknown")
        self.assertIsNone(alias["resolved_config_id"])
        self.assertFalse(alias["runtime_verified"])
        self.assertEqual([r["effort"] for r in result["scored_configs"]
                          if r["model"] == "gemini-4-argon"], ["high"])
        unscored = [r for r in result["unknown_configs"] if r["model"] == "gemini-4-argon"]
        self.assertEqual({r["effort"] for r in unscored}, {"medium", "low"})
        self.assertTrue(all(r["score"] is None and not r["authority_eligible"] for r in unscored))


if __name__ == "__main__":
    unittest.main()
