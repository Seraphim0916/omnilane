#!/usr/bin/env python3
"""Offline exact-AA policy tests.  No provider executable is invoked."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("aa_policy", ROOT / "scripts/lib/aa_policy.py")
aa_policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(aa_policy)


class ExactAAPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = json.loads((ROOT / "config/aa-model-policy.json").read_text())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aa-policy-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.registry = copy.deepcopy(self.real)

    def row(self, config_id):
        return next(row for row in self.registry["scored_configs"] if row["id"] == config_id)

    def verify_mapping(self, config_id, runtime_model=None, runtime_effort="__row__"):
        row = self.row(config_id)
        mapping = row["transport_mapping"]
        mapping.update(status="verified", runtime_verified=True)
        mapping["runtime_model"] = runtime_model or row["model"]
        mapping["runtime_effort"] = row["effort"] if runtime_effort == "__row__" else runtime_effort
        return row

    def context(self, config_id, ceiling=None):
        row = self.row(config_id)
        return {
            "schema_version": 1,
            "snapshot_id": self.registry["snapshot"]["id"],
            "kind": "model",
            "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
            "inherited_ceiling": row["score"] if ceiling is None else ceiling,
        }

    def decide(self, caller_id, target_id, *, ceiling=None, runtime_model=None,
               runtime_effort="__row__"):
        target = self.verify_mapping(target_id, runtime_model, runtime_effort)
        return aa_policy.decide(
            self.registry,
            "fixture-sha",
            vendor=target["vendor"],
            model=runtime_model or target["model"],
            effort=target["effort"] if runtime_effort == "__row__" else runtime_effort,
            caller=self.context(caller_id, ceiling),
            caller_sha256="caller-sha",
        )

    def test_frozen_registry_coverage_and_estimates(self):
        validated = aa_policy._validate_registry(copy.deepcopy(self.real))
        self.assertEqual(len(validated["scored_configs"]), 78)
        self.assertEqual(sum(row["estimated"] for row in validated["scored_configs"]), 48)
        self.assertEqual(validated["coverage"]["by_vendor"], {
            "codex": 35, "claude": 27, "gemini": 7, "grok": 9,
        })

    def test_same_score_is_allowed(self):
        result = self.decide("codex/gpt-6-astra-xhigh", "claude/claude-fable-5-1-xhigh")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["code"], "same-score-allowed")
        self.assertEqual(result["target_score"], 54)

    def test_within_display_grade_upward_is_denied(self):
        result = self.decide("claude/claude-opus-5-xhigh", "codex/gpt-6-astra-xhigh")
        self.assertFalse(result["allowed"])
        self.assertEqual((result["caller_score"], result["target_score"]), (53, 54))
        self.assertEqual(result["code"], "target-above-effective-ceiling")

    def test_cross_vendor_down_allowed_and_up_denied(self):
        down = self.decide("codex/gpt-6-astra", "gemini/gemini-3-8-flash",
                           runtime_model="gemini-3.8-flash-high", runtime_effort=None)
        up = self.decide("gemini/gemini-3-8-flash", "codex/gpt-6-astra")
        self.assertTrue(down["allowed"])
        self.assertEqual(down["code"], "downward-allowed")
        self.assertFalse(up["allowed"])

    def test_estimated_row_keeps_score_and_estimated_flag(self):
        result = self.decide("codex/gpt-6-astra", "codex/gpt-5-6-terra-medium")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["target_score"], 37)
        self.assertTrue(result["target_estimated"])

    def test_unknown_caller_model_effort_reasoning_and_fallback_deny(self):
        base = self.context("codex/gpt-6-astra")
        mutations = {
            "model": "gpt-6-astra-family",
            "effort": "ultra",
            "reasoning": "displayed-grade",
            "fallback": "unverified-fallback",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                caller = copy.deepcopy(base)
                caller["caller"][field] = value
                result = aa_policy.decide(
                    self.registry, "fixture-sha", vendor="codex", model="gpt-5.6-sol",
                    effort="high", caller=caller, caller_sha256="caller-sha"
                )
                self.assertFalse(result["allowed"])
                self.assertEqual(result["code"], "unknown-caller-config")

    def test_caller_ceiling_propagates_and_inherited_ceiling_narrows(self):
        result = self.decide(
            "codex/gpt-6-astra", "codex/gpt-5-6-sol-high", ceiling=49
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["effective_ceiling"], 49)
        self.assertEqual(result["child_context"]["inherited_ceiling"], 49)
        narrowed = self.decide(
            "codex/gpt-6-astra", "codex/gpt-5-6-sol-xhigh", ceiling=49
        )
        self.assertFalse(narrowed["allowed"])
        self.assertEqual((narrowed["target_score"], narrowed["effective_ceiling"]), (50, 49))

    def test_missing_caller_context_has_actionable_structured_diagnosis(self):
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="codex", model="gpt-5.6-sol",
            effort="high", caller=None, caller_sha256=None
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["code"], "missing-caller-context")
        self.assertIn("--caller-context", result["message"])

    def test_unverified_transport_and_grok_discarded_effort_deny(self):
        caller = self.context("codex/gpt-6-astra")
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="codex", model="gpt-5.6-sol",
            effort="high", caller=caller, caller_sha256="caller-sha"
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["code"], "runtime-mapping-unverified")
        self.verify_mapping("grok/grok-4-6")
        grok = aa_policy.decide(
            self.registry, "fixture-sha", vendor="grok", model="grok-4.6",
            effort="high", caller=caller, caller_sha256="caller-sha"
        )
        self.assertFalse(grok["allowed"])
        self.assertEqual(grok["code"], "runtime-effort-discarded")

    def test_grok_overlay_requires_exact_selector_evidence(self):
        row = self.row("grok/grok-4-6")
        evidence = self.base / "selector-proof.txt"
        evidence.write_text("fixture CLI selector evidence")
        mapping = dict(config_id=row["id"], identity=aa_policy._row_identity(row),
                       verification="request-selector-contract", runtime_model=row["model"],
                       runtime_effort=row["effort"], selector_type="cli_reasoning_effort",
                       cli_flag="--reasoning-effort")
        overlay = dict(schema_version=1, snapshot_id=self.registry["snapshot"]["id"],
                       host=socket.gethostname(), mappings=[mapping],
                       evidence=[dict(path=str(evidence), sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())])
        path = self.base / "overlay.json"
        for kind in ("valid", "wrong-flag", "wrong-effort", "wrong-vendor"):
            with self.subTest(kind=kind):
                candidate = copy.deepcopy(overlay)
                entry = candidate["mappings"][0]
                if kind == "wrong-flag":
                    entry["cli_flag"] = "--model"
                elif kind == "wrong-effort":
                    entry["runtime_effort"] = "invalid-value"
                elif kind == "wrong-vendor":
                    other = self.row("codex/gpt-6-astra")
                    entry.update(config_id=other["id"], identity=aa_policy._row_identity(other),
                                 runtime_model=other["model"], runtime_effort=other["effort"])
                path.write_text(json.dumps(candidate))
                registry = copy.deepcopy(self.registry)
                with patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": str(path),
                                             "OMNILANE_AA_OVERLAY_SHA256": ""}):
                    if kind == "valid":
                        aa_policy.apply_transport_overlay(registry)
                        target, code, _ = aa_policy._runtime_target(registry, "grok", row["model"], row["effort"], None)
                        self.assertEqual(code, "runtime-mapping-verified")
                        self.assertEqual(target["transport_mapping"]["cli_flag"], "--reasoning-effort")
                    else:
                        with self.assertRaises(ValueError):
                            aa_policy.apply_transport_overlay(registry)

    def test_grok_requires_verified_cli_effort_contract(self):
        row = self.verify_mapping("grok/grok-4-6")
        mapping = row["transport_mapping"]
        mapping.update(selector_type="cli_reasoning_effort", cli_flag="--reasoning-effort")
        target, code, _ = aa_policy._runtime_target(self.registry, "grok", "grok-4.6", "high", None)
        self.assertEqual(code, "runtime-mapping-verified")
        self.assertEqual(target["id"], row["id"])
        for field, value, expected in (
            ("cli_flag", "--model", "runtime-effort-discarded"),
            ("selector_type", "model_id_encoded_effort", "runtime-effort-discarded"),
            ("runtime_verified", False, "runtime-mapping-unverified"),
        ):
            with self.subTest(field=field):
                original = mapping[field]
                mapping[field] = value
                target, code, _ = aa_policy._runtime_target(self.registry, "grok", "grok-4.6", "high", None)
                self.assertIsNone(target)
                self.assertEqual(code, expected)
                mapping[field] = original
        target, code, _ = aa_policy._runtime_target(self.registry, "grok", "grok-4.6", None, None)
        self.assertIsNone(target)
        self.assertEqual(code, "unknown-target-runtime")

    def test_operator_assertion_is_explicit_exemption_not_authentication(self):
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="fixture", model="unknown",
            effort=None, caller=None, caller_sha256=None, operator_asserted_human=True
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["code"], "operator-asserted-human-exemption")
        self.assertIn("not authentication", result["evidence_limit"])


if __name__ == "__main__":
    unittest.main()
