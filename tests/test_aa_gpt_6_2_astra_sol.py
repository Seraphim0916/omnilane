#!/usr/bin/env python3
"""Offline contract tests for GPT-6.2 Astra and GPT-6.2 Sol in AA v5.0.1 (2026-11-18)."""

from __future__ import annotations

import offline_env  # noqa: F401
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/lib"))

import aa_policy  # noqa: E402
import aa_rebaseline  # noqa: E402
import build_overlay  # noqa: E402
import caller_identity  # noqa: E402
import probe_sweep  # noqa: E402


EXPECTED_ASTRA_62 = {
    "codex/gpt-6-2-astra": ("max", 62.14, 62),
    "codex/gpt-6-2-astra-xhigh": ("xhigh", 61.56, 62),
    "codex/gpt-6-2-astra-high": ("high", 60.09, 60),
    "codex/gpt-6-2-astra-medium": ("medium", 57.0, 57),
    "codex/gpt-6-2-astra-low": ("low", 45.65, 46),
}

EXPECTED_SOL_62 = {
    "codex/gpt-6-2-sol": ("max", 57.26, 57),
    "codex/gpt-6-2-sol-xhigh": ("xhigh", 55.74, 56),
    "codex/gpt-6-2-sol-high": ("high", 54.89, 55),
    "codex/gpt-6-2-sol-medium": ("medium", 52.68, 53),
    "codex/gpt-6-2-sol-low": ("low", 40.55, 41),
}


class GPT62AstraSolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry, cls.digest = aa_policy.load_registry(ROOT / "config/aa-model-policy.json")
        cls.rows = {row["id"]: row for row in cls.registry["scored_configs"]}
        cls.lanes = {}
        for line in (ROOT / "routing.yaml").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([a-z-]+):\s*([^#]+)", line)
            if match:
                cls.lanes[match.group(1)] = [item.strip() for item in match.group(2).split("|")]

    def test_snapshot_identity_and_counts(self) -> None:
        self.assertEqual(self.registry["snapshot"]["id"], "aa-v5.0.1-2026-11-19-v1")
        self.assertEqual(self.registry["snapshot"]["benchmark_version"], "5.0.1")
        self.assertEqual(self.registry["snapshot"]["as_of"], "2026-11-19")
        self.assertEqual(len(self.registry["scored_configs"]), 112)
        self.assertEqual(self.registry["coverage"]["by_vendor"], {
            "codex": 49, "claude": 31, "gemini": 25, "grok": 7,
        })
        self.assertEqual(self.registry["coverage"]["aliases"], 70)

    def test_ten_measured_gpt_6_2_rows_and_no_non_reasoning_rows(self) -> None:
        for model, expected in (("gpt-6.2-astra", EXPECTED_ASTRA_62), ("gpt-6.2-sol", EXPECTED_SOL_62)):
            for cid, (effort, raw, score) in expected.items():
                with self.subTest(config_id=cid):
                    self.assertIn(cid, self.rows)
                    row = self.rows[cid]
                    self.assertEqual(row["vendor"], "codex")
                    self.assertEqual(row["model"], model)
                    self.assertEqual(row["effort"], effort)
                    self.assertEqual(row["reasoning"], "reasoning")
                    self.assertEqual((row["score_raw"], row["score"], row["estimated"]), (raw, score, False))
                    self.assertTrue(aa_policy.authority_eligible(row))
                    self.assertEqual(row["transport_mapping"]["candidate_model_ids"], [model])
                    self.assertFalse(row["transport_mapping"]["runtime_verified"])
            self.assertFalse(any(
                r["model"] == model and r["reasoning"] == "non-reasoning"
                for r in self.registry["scored_configs"]
            ))

    def test_aliases_proven_and_probe_plan_include_gpt_6_2(self) -> None:
        aliases = {(a["catalog_vendor"], a["catalog_model"]): a for a in self.registry["aliases"]}
        for model, expected in (("gpt-6.2-astra", EXPECTED_ASTRA_62), ("gpt-6.2-sol", EXPECTED_SOL_62)):
            self.assertIn(("codex", model), aliases)
            self.assertEqual(set(aliases[("codex", model)]["candidate_config_ids"]), set(expected))
            for cid in expected:
                self.assertIn(cid, build_overlay.PROVEN)
        planned = {(item["model"], item["effort"]) for item in probe_sweep.plan("codex")}
        for model in ("gpt-6.2-astra", "gpt-6.2-sol"):
            for effort in ("max", "xhigh", "high", "medium", "low"):
                self.assertIn((model, effort), planned)
            self.assertNotIn((model, "none"), planned)

    def test_caller_identity_resolution_and_floors(self) -> None:
        for model, expected_floor in (("gpt-6.2-astra", 46), ("gpt-6.2-sol", 41)):
            floor = caller_identity.floor_row(self.registry, "codex", model)
            self.assertIsNotNone(floor)
            self.assertEqual((floor["effort"], floor["score"]), ("low", expected_floor))
            for effort in ("low", "medium", "high", "xhigh", "max"):
                row, err = caller_identity.resolve(self.registry, "codex", model, effort)
                self.assertIsNotNone(row, err)
                self.assertEqual(err, "")
            for unsupported in ("none", "ultra", None):
                row, err = caller_identity.resolve(self.registry, "codex", model, unsupported)
                self.assertIsNone(row)
                self.assertTrue(err)

    def test_legacy_gpt_6_rows_remain_intact(self) -> None:
        for cid, score in (
            ("codex/gpt-6-astra-xhigh", 55),
            ("codex/gpt-6-astra-medium", 52),
            ("codex/gpt-6-1-sol-xhigh", 52),
            ("codex/gpt-6-1-sol-medium", 46),
            ("codex/gpt-6-1-terra-high", 42),
            ("codex/gpt-6-luna-high", 21),
        ):
            self.assertEqual(self.rows[cid]["score"], score)

    def test_updated_lanes_and_no_max_effort_in_default_chains(self) -> None:
        self.assertEqual(self.lanes["hardest-coding"][:4], [
            "codex gpt-6.2-astra xhigh",
            "gemini gemini-4-carbon-high -",
            "gemini gemini-4-carbon-low -",
            "claude claude-opus-5-5 high",
        ])
        self.assertEqual(self.lanes["bulk-mechanical"][0], "codex gpt-6.2-sol medium")
        self.assertEqual(self.lanes["long-context"][:3], [
            "codex gpt-6.2-astra xhigh",
            "codex gpt-6-astra medium",
            "codex gpt-6.2-sol low",
        ])
        self.assertEqual(self.lanes["consult"][:3], [
            "codex gpt-6.2-astra xhigh",
            "codex gpt-6.2-astra high",
            "codex gpt-6-astra xhigh",
        ])
        self.assertEqual(self.lanes["fast-agentic"][0], "codex gpt-6.1-sol medium")
        for lane, chain in self.lanes.items():
            for item in chain:
                parts = item.split()
                if len(parts) >= 3:
                    self.assertNotEqual(parts[2], "max", f"{lane} contains max effort: {item}")


if __name__ == "__main__":
    unittest.main()
