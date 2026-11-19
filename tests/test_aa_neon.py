"""Neon 評分快照：同版加列、選取器及未證明傳輸的拒絕測試。"""
from __future__ import annotations

import offline_env
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy
import aa_rebaseline as aa
import build_overlay
import probe_sweep


class NeonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, _ = aa_policy.load_registry(ROOT / "config/aa-model-policy.json")

    def test_incremental_extract_reproduces_and_preserves_all_old_fields(self):
        old = json.loads((ROOT / "docs/reports/aa-v5.0.1-extract-2026-11-18.json").read_text())
        path = ROOT / "docs/reports/aa-v5.0.1-extract-2026-11-19.json"
        new = json.loads(path.read_text())
        pages = sorted((ROOT / "docs/reports").glob("aa-v5.0.1-neon-*-extract-2026-11-19.json"))
        result = aa.append_extract(old, aa.merge_extracts(pages[::-1]))
        result["append_rule"]["base_extract"] = "docs/reports/aa-v5.0.1-extract-2026-11-18.json"
        self.assertEqual(aa.dump(result), path.read_bytes())
        for slug, row in old["records"].items():
            self.assertEqual(new["records"][slug], row, slug)
        self.assertEqual(set(new["records"]) - set(old["records"]), {
            "gemini-4-neon", "gemini-4-neon-high", "gemini-4-neon-medium", "gemini-4-neon-low",
        })

    def test_three_dispatchable_rows_aliases_and_probe_targets(self):
        rows = {r["id"]: r for r in self.registry["scored_configs"]}
        aliases = {a["catalog_model"]: a for a in self.registry["aliases"]}
        planned = {p["model"] for p in probe_sweep.plan("gemini")}
        for effort, raw, score in (("high", 59.62, 60),
                                   ("medium", 54.86, 55), ("low", 44.08, 44)):
            cid = "gemini/gemini-4-neon-" + effort
            selector = "gemini-4-neon-" + effort
            with self.subTest(effort=effort):
                row = rows[cid]
                self.assertEqual((row["effort"], row["score_raw"], row["score"], row["estimated"]),
                                 (effort, raw, score, False))
                self.assertEqual(row["transport_mapping"]["candidate_model_ids"], [selector])
                self.assertFalse(row["transport_mapping"]["runtime_verified"])
                self.assertEqual(aliases[selector]["candidate_config_ids"], [cid])
                self.assertEqual(build_overlay.PROVEN[cid][1], selector)
                self.assertIn(selector, planned)
                self.assertIn(cid, aa_policy.HUMAN_TRANSPORT_PROOF_REQUIRED)

    def test_unavailable_xhigh_stays_reference_only(self):
        refs = {r["id"]: r for r in self.registry["reference_configs"]}
        planned = {p["model"] for p in probe_sweep.plan("gemini")}
        aliases = {a["catalog_model"] for a in self.registry["aliases"]}
        chains = aa.lane_table(ROOT / "routing.yaml")
        for family, score in (("hydrogen", 54), ("neon", 61)):
            model = "gemini-4-" + family
            cid, selector = "gemini/" + model, model + "-xhigh"
            row = refs[cid]
            self.assertEqual((row["effort"], row["score"]), ("xhigh", score))
            self.assertFalse(row["authority_eligible"])
            self.assertEqual(row["transport_mapping"]["candidate_model_ids"], [])
            self.assertNotIn(cid, build_overlay.PROVEN)
            self.assertNotIn(selector, aliases | planned)
            self.assertFalse(any(v == selector for chain in chains.values() for _,v,_ in chain))
            for name, effort, target in ((selector, None, None), (model,"xhigh",None),
                                         (model,None,cid)):
                for human in (True, False):
                    result = aa_policy.decide(self.registry, "fixture", vendor="gemini",
                        model=name, effort=effort, target_config=target, caller=None,
                        caller_sha256=None, operator_asserted_human=human)
                    self.assertFalse(result["allowed"])
                    self.assertEqual(result["code"], "selector-unavailable")

    def test_human_needs_transport_proof_and_orchestrator_is_not_scored(self):
        registry = copy.deepcopy(self.registry)
        for row in registry["scored_configs"]:
            if row["model"] != "gemini-4-neon":
                continue
            selector = row["transport_mapping"]["candidate_model_ids"][0]
            args = dict(vendor="gemini", model=selector, effort=None,
                        caller=None, caller_sha256=None, operator_asserted_human=True)
            result = aa_policy.decide(registry, "fixture", **args)
            self.assertEqual(result["code"], "runtime-mapping-unverified")
            self.assertFalse(result["allowed"])
            row["transport_mapping"].update(status="verified", runtime_verified=True,
                runtime_model=selector, runtime_effort=row["effort"], selector_type="model_id_encoded_effort")
            self.assertTrue(aa_policy.decide(registry, "fixture", **args)["allowed"])
        self.assertFalse(any(r["model"] == "gemini-pro-orchestrator-neon"
                             for r in registry["scored_configs"]))
        caller_row = next(r for r in registry["scored_configs"] if r["id"] == "codex/gpt-6-2-astra")
        caller = {"schema_version": 1, "snapshot_id": registry["snapshot"]["id"], "kind": "model",
                  "caller": {key: caller_row[key] for key in aa_policy.IDENTITY_FIELDS},
                  "inherited_ceiling": caller_row["score"]}
        result = aa_policy.decide(registry, "fixture", vendor="gemini",
            model="gemini-pro-orchestrator-neon", effort=None, caller=caller,
            caller_sha256="fixture-caller", operator_asserted_human=False)
        self.assertFalse(result["allowed"])

    def test_only_high_is_a_fallback_after_hydrogen_in_all_active_lanes(self):
        lanes = aa.lane_table(ROOT / "routing.yaml")
        self.assertEqual(len(lanes), 11)
        for lane, chain in lanes.items():
            with self.subTest(lane=lane):
                hydrogen = ("gemini", "gemini-4-hydrogen-high", None)
                neon = ("gemini", "gemini-4-neon-high", None)
                self.assertEqual([r for r in chain if "neon" in r[1]], [neon])
                self.assertEqual(chain.index(neon), chain.index(hydrogen) + 1)
                self.assertGreater(chain.index(neon), 0)


if __name__ == "__main__":
    unittest.main()
