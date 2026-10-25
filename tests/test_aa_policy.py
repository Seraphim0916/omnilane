#!/usr/bin/env python3
"""Offline exact-AA policy tests.  No provider executable is invoked."""

from __future__ import annotations

import offline_env  # Activate suite isolation for direct file execution.
import home_isolation
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

BUILDER_SPEC = importlib.util.spec_from_file_location(
    "build_overlay", ROOT / "scripts/lib/build_overlay.py")
builder = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(builder)


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
        self.assertEqual(len(validated["scored_configs"]), 124)
        self.assertEqual(sum(row["estimated"] for row in validated["scored_configs"]), 26)
        self.assertEqual(validated["coverage"]["by_vendor"], {
            "codex": 57, "claude": 46, "gemini": 9, "grok": 12,
        })

    def test_same_score_is_allowed(self):
        result = self.decide("codex/gpt-6-astra", "claude/claude-fable-5-1-xhigh")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["code"], "same-score-allowed")
        self.assertEqual(result["target_score"], 53)

    def test_unknown_model_lists_vendor_selectors_without_resign(self):
        for stale in (False, True):
            self.registry["_stale_transport_vendors"] = ["gemini"] if stale else []
            for model in ("gemini-4-argon", "gemini-3.8-flash"):
                with self.subTest(model=model, stale=stale):
                    result = aa_policy.decide(
                        self.registry, "fixture-sha", vendor="gemini", model=model,
                        effort=None, caller=self.context("codex/gpt-6-astra"),
                        caller_sha256="caller-sha")
                    self.assertFalse(result["allowed"])
                    self.assertIsNone(result["target_config_id"])
                    self.assertEqual(result["code"], "unknown-model-name")
                    self.assertEqual(result["failed_gate"], "target-model-name")
                    self.assertEqual(result["next_command"], "omnilane list")
                    for key in ("message", "message_zh_tw"):
                        self.assertIn("gemini-4-argon-high", result[key])
                        self.assertIn("gemini-3.8-flash-high", result[key])
                        self.assertNotIn("resign", result[key])
                        self.assertNotIn("gpt-", result[key])
                    self.assertNotIn("effort None", result["message"])
                    self.assertNotIn("指定強度", result["message_zh_tw"])

    def test_known_unverified_model_keeps_transport_guidance(self):
        for stale in (False, True):
            self.registry["_stale_transport_vendors"] = ["gemini"] if stale else []
            for effort in (None, "high"):
                with self.subTest(stale=stale, effort=effort):
                    result = aa_policy.decide(
                        self.registry, "fixture-sha", vendor="gemini",
                        model="gemini-4-argon-high", effort=effort,
                        caller=self.context("codex/gpt-6-astra"), caller_sha256="caller-sha")
                    self.assertEqual(result["code"], "unknown-target-runtime" if stale
                                     else "runtime-mapping-unverified")
                    self.assertEqual(result["failed_gate"], "target-transport")
                    self.assertIn("resign", result["next_command"])

    def test_unknown_model_suggestions_follow_runtime_aliases(self):
        row = self.verify_mapping("gemini/gemini-4-argon", "fixture-argon-high")
        row["transport_mapping"]["selector_type"] = "model_id_encoded_effort"
        _, code, detail = aa_policy._runtime_target(
            self.registry, "gemini", "missing-model", None, None)
        self.assertEqual(code, "unknown-model-name")
        self.assertIn("fixture-argon-high", detail["available_models"])
        self.assertNotIn("gemini-4-argon-high", detail["available_models"])
        self.assertEqual(detail["available_models"], sorted(set(detail["available_models"])))
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="gemini", model="fixture-argon-high",
            effort=None, caller=self.context("codex/gpt-6-astra"),
            caller_sha256="caller-sha")
        self.assertTrue(result["allowed"], result)

    def test_unmatched_effort_is_a_selector_error(self):
        self.verify_mapping("codex/gpt-6-astra")
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="codex", model="gpt-6-astra",
            effort="nonexistent", caller=self.context("codex/gpt-6-astra"),
            caller_sha256="caller-sha")
        self.assertEqual(result["code"], "unknown-model-name")
        self.assertEqual(result["next_command"], "omnilane list")
        for key in ("message", "message_zh_tw"):
            self.assertIn("nonexistent", result[key])
            self.assertIn("gpt-6-astra", result[key])
            self.assertNotIn("resign", result[key])
        self.assertIn("no configuration matches", result["message"])
        self.assertIn("找不到符合", result["message_zh_tw"])

    def test_known_unverified_plain_models_keep_transport_guidance(self):
        for config_id in ("grok/grok-4-6", "codex/gpt-6-astra"):
            row = self.row(config_id)
            mapping = row["transport_mapping"]
            for partial in (False, True):
                if partial:
                    mapping["runtime_model"] = row["model"]
                for stale in (False, True):
                    self.registry["_stale_transport_vendors"] = [row["vendor"]] if stale else []
                    for effort in (None, row["effort"]):
                        with self.subTest(config_id=config_id, partial=partial,
                                          stale=stale, effort=effort):
                            result = aa_policy.decide(
                                self.registry, "fixture-sha", vendor=row["vendor"],
                                model=row["model"], effort=effort,
                                caller=self.context("codex/gpt-6-astra"),
                                caller_sha256="caller-sha", target_config=config_id)
                            self.assertFalse(result["allowed"])
                            self.assertEqual(result["code"], "unknown-target-runtime" if stale
                                             else "runtime-mapping-unverified")
                            self.assertEqual(result["failed_gate"], "target-transport")
                            self.assertIn("resign", result["next_command"])
                            if not stale:
                                self.assertEqual(result["candidate_config_ids"], [config_id])

    def test_encoded_effort_conflict_is_independent_of_verification(self):
        row = self.row("gemini/gemini-4-argon")
        for state in ("candidate", "partial", "verified"):
            if state == "partial":
                row["transport_mapping"]["runtime_model"] = "gemini-4-argon-high"
            elif state == "verified":
                self.verify_mapping(row["id"], "gemini-4-argon-high")
                row["transport_mapping"]["selector_type"] = "model_id_encoded_effort"
            with self.subTest(state=state):
                result = aa_policy.decide(
                    self.registry, "fixture-sha", vendor="gemini",
                    model="gemini-4-argon-high", effort="low",
                    caller=self.context("codex/gpt-6-astra"), caller_sha256="caller-sha")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["code"], "encoded-effort-conflict")
                self.assertEqual(result["encoded_effort"], "high")
                self.assertEqual(result["effort"], "low")

    def test_suggestions_respect_target_config_and_use_registered_aliases(self):
        config_id = "gemini/gemini-4-argon"
        for verified in (False, True):
            alias = "fixture-argon-high" if verified else "gemini-4-argon-high"
            if verified:
                row = self.verify_mapping(config_id, alias)
                row["transport_mapping"]["selector_type"] = "model_id_encoded_effort"
            with self.subTest(verified=verified):
                result = aa_policy.decide(
                    self.registry, "fixture-sha", vendor="gemini", model="missing-model",
                    effort=None, caller=self.context("codex/gpt-6-astra"),
                    caller_sha256="caller-sha", target_config=config_id)
                self.assertEqual(result["code"], "unknown-model-name")
                self.assertEqual(result["available_models"], [alias])
                for key in ("message", "message_zh_tw"):
                    self.assertIn(alias, result[key])
                    self.assertNotIn("gemini-3.8-flash-high", result[key])

    def test_suggestions_do_not_claim_transport_or_ceiling_approval(self):
        for verified in (False, True):
            if verified:
                row = self.verify_mapping("gemini/gemini-4-argon", "gemini-4-argon-high")
                row["transport_mapping"]["selector_type"] = "model_id_encoded_effort"
            with self.subTest(verified=verified):
                result = aa_policy.decide(
                    self.registry, "fixture-sha", vendor="gemini", model="missing-model",
                    effort=None, caller=self.context("codex/gpt-6-astra", ceiling=1),
                    caller_sha256="caller-sha")
                self.assertIn("gemini-4-argon-high", result["available_models"])
                self.assertIn("Registered", result["message"])
                self.assertIn("transport verification", result["message"])
                self.assertIn("caller ceiling", result["message"])
                self.assertIn("已登錄", result["message_zh_tw"])
                self.assertIn("傳輸驗證", result["message_zh_tw"])
                self.assertIn("呼叫端上限", result["message_zh_tw"])
                attempted = aa_policy.decide(
                    self.registry, "fixture-sha", vendor="gemini",
                    model="gemini-4-argon-high", effort=None,
                    caller=self.context("codex/gpt-6-astra", ceiling=1),
                    caller_sha256="caller-sha")
                self.assertFalse(attempted["allowed"])
                self.assertEqual(attempted["code"], "target-above-effective-ceiling" if verified
                                 else "runtime-mapping-unverified")

    def test_unknown_vendor_has_localized_empty_suggestions(self):
        for config_id in (None, "gemini/gemini-4-argon"):
            with self.subTest(target_config=config_id):
                result = aa_policy.decide(
                    self.registry, "fixture-sha", vendor="unknown-vendor", model="missing-model",
                    effort=None, caller=self.context("codex/gpt-6-astra"),
                    caller_sha256="caller-sha", target_config=config_id)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["code"], "unknown-model-name")
                self.assertEqual(result["available_models"], [])
                self.assertEqual(result["next_command"], "omnilane list")
                self.assertIn("No registered model selectors", result["message"])
                self.assertIn("沒有已登錄型號", result["message_zh_tw"])
                for key in ("message", "message_zh_tw"):
                    self.assertIn("unknown-vendor", result[key])
                    self.assertNotIn("(none)", result[key])
                    self.assertNotIn("resign", result[key])

    def test_unknown_selector_preserves_base_decision_fields(self):
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="gemini", model="missing-model", effort=None,
            caller=self.context("codex/gpt-6-astra"), caller_sha256="caller-sha")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["target_request"], {
            "vendor": "gemini", "model": "missing-model", "effort": None})
        self.assertEqual(result["caller_score"], 53)
        self.assertEqual(result["effective_ceiling"], 53)
        self.assertEqual(result["caller_context_sha256"], "caller-sha")
        self.assertIsNone(result["target_score"])
        self.assertIsNone(result["child_context"])
        self.assertFalse(result["allowed"])

    def test_within_display_grade_upward_is_denied(self):
        result = self.decide("claude/claude-opus-5-xhigh", "codex/gpt-6-astra-xhigh")
        self.assertFalse(result["allowed"])
        self.assertEqual((result["caller_score"], result["target_score"]), (50, 52))
        self.assertEqual(result["code"], "target-above-effective-ceiling")

    def test_cross_vendor_down_allowed_and_up_denied(self):
        down = self.decide("codex/gpt-6-astra", "gemini/gemini-3-8-flash",
                           runtime_model="gemini-3.8-flash-high", runtime_effort=None)
        up = self.decide("gemini/gemini-3-8-flash", "codex/gpt-6-astra")
        self.assertTrue(down["allowed"])
        self.assertEqual(down["code"], "downward-allowed")
        self.assertFalse(up["allowed"])

    def test_estimated_row_keeps_score_and_estimated_flag(self):
        result = self.decide("codex/gpt-6-astra", "codex/gpt-5-5-low")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["target_score"], 31)
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
            "codex/gpt-6-astra", "codex/gpt-5-6-sol-high", ceiling=43
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["effective_ceiling"], 43)
        self.assertEqual(result["child_context"]["inherited_ceiling"], 43)
        narrowed = self.decide(
            "codex/gpt-6-astra", "codex/gpt-5-6-sol-xhigh", ceiling=43
        )
        self.assertFalse(narrowed["allowed"])
        self.assertEqual((narrowed["target_score"], narrowed["effective_ceiling"]), (44, 43))

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

    def test_grok_guard_rejects_verified_mapping_that_discards_effort(self):
        # This malformed mapping passes generic matching, so only the Grok
        # selector guard can deny it. The public overlay loader rejects it too.
        row = self.verify_mapping("grok/grok-4-6", runtime_effort=None)
        row["transport_mapping"].update(
            selector_type="cli_reasoning_effort", cli_flag="--reasoning-effort")
        target, code, _ = aa_policy._runtime_target(
            self.registry, "grok", "grok-4.6", None, None)
        self.assertIsNone(target)
        self.assertEqual(code, "runtime-effort-discarded")

    def test_operator_assertion_is_explicit_exemption_not_authentication(self):
        result = aa_policy.decide(
            self.registry, "fixture-sha", vendor="fixture", model="unknown",
            effort=None, caller=None, caller_sha256=None, operator_asserted_human=True
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["code"], "operator-asserted-human-exemption")
        self.assertIn("not authentication", result["evidence_limit"])


REGISTRY_PATH = ROOT / "config" / "aa-model-policy.json"
IDENTITY_FIELDS = ("vendor", "model", "effort", "reasoning", "fallback")
TARGETS = {
    "codex": ("codex/gpt-5-6-luna-low", "gpt-5.6-luna", "low", "model_and_effort"),
    "claude": ("claude/claude-4-5-haiku-reasoning", "claude-haiku-4-5", None, "model_and_effort"),
    "grok": ("grok/grok-4-5", "grok-4.5", "high", "cli_reasoning_effort"),
    "gemini": ("gemini/gemini-3-8-flash", "gemini-3.8-flash-high", "high", "model_id_encoded_effort"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TransportOverlayEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work = Path(self.temp_dir.name)
        self.overlay_path = self.work / "transport-overlay.json"

        with patch.dict(os.environ, self._environment(None), clear=True):
            self.base_registry, self.registry_sha256 = aa_policy.load_registry(REGISTRY_PATH)

        rows = {row["id"]: row for row in self.base_registry["scored_configs"]}
        evidence = []
        mappings = []
        for vendor, (config_id, runtime_model, runtime_effort, selector_type) in TARGETS.items():
            evidence_path = self.work / f"{vendor}.evidence"
            evidence_path.write_text(f"{vendor} selector evidence\n", encoding="utf-8")
            evidence.append({"path": str(evidence_path), "sha256": sha256(evidence_path), "vendor": vendor})

            row = rows[config_id]
            mapping = {
                "config_id": config_id,
                "identity": {key: row[key] for key in IDENTITY_FIELDS},
                "runtime_model": runtime_model,
                "runtime_effort": runtime_effort,
                "selector_type": selector_type,
                "verification": "request-selector-contract",
            }
            if selector_type == "cli_reasoning_effort":
                mapping["cli_flag"] = "--reasoning-effort"
            mappings.append(mapping)

        manifest_path = self.work / "probe-manifest.json"
        manifest_path.write_text('{"probe_runs": {}}\n', encoding="utf-8")
        evidence.append({"path": str(manifest_path), "sha256": sha256(manifest_path)})
        self.overlay = {
            "schema_version": 1,
            "snapshot_id": self.base_registry["snapshot"]["id"],
            "host": socket.gethostname(),
            "evidence": evidence,
            "mappings": mappings,
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _environment(overlay_path: Path | None) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("OMNILANE_AA_OVERLAY_SHA256", None)
        if overlay_path is None:
            env.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
        else:
            env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(overlay_path)
        return env

    def _load_overlay(self) -> tuple[dict, str]:
        self.overlay_path.write_text(json.dumps(self.overlay), encoding="utf-8")
        with patch.dict(os.environ, self._environment(self.overlay_path), clear=True):
            return aa_policy.load_registry(REGISTRY_PATH)

    def _decision(self, registry: dict, vendor: str) -> dict:
        caller_row = max(registry["scored_configs"], key=lambda row: row["score"])
        caller = {
            "caller": {key: caller_row[key] for key in IDENTITY_FIELDS},
            "inherited_ceiling": caller_row["score"],
        }
        _, model, effort, _ = TARGETS[vendor]
        return aa_policy.decide(
            registry,
            self.registry_sha256,
            vendor=vendor,
            model=model,
            effort=effort,
            caller=caller,
            caller_sha256="fixture",
        )

    def assert_only_vendor_is_stale(self, registry: dict, stale_vendor: str) -> None:
        for vendor in TARGETS:
            decision = self._decision(registry, vendor)
            if vendor == stale_vendor:
                self.assertFalse(decision["allowed"])
                self.assertEqual(decision["code"], "unknown-target-runtime")
            else:
                self.assertTrue(decision["allowed"], decision)

    def test_hash_drift_degrades_only_tagged_vendor(self) -> None:
        codex_evidence = next(item for item in self.overlay["evidence"] if item.get("vendor") == "codex")
        codex_evidence["sha256"] = "0" * 64

        registry, _ = self._load_overlay()

        self.assert_only_vendor_is_stale(registry, "codex")

    def test_missing_file_degrades_only_tagged_vendor(self) -> None:
        claude_evidence = next(item for item in self.overlay["evidence"] if item.get("vendor") == "claude")
        claude_evidence["path"] = str(self.work / "missing-claude-version")

        registry, _ = self._load_overlay()

        self.assert_only_vendor_is_stale(registry, "claude")

    def test_untagged_manifest_hash_drift_remains_fail_closed(self) -> None:
        manifest = next(item for item in self.overlay["evidence"] if "vendor" not in item)
        manifest["sha256"] = "0" * 64

        with self.assertRaises(aa_policy.PolicyError):
            self._load_overlay()

    def test_stale_vendor_does_not_bypass_mapping_validation(self) -> None:
        codex_evidence = next(item for item in self.overlay["evidence"] if item.get("vendor") == "codex")
        codex_evidence["sha256"] = "0" * 64
        codex_mapping = next(item for item in self.overlay["mappings"] if item["identity"]["vendor"] == "codex")
        codex_mapping["identity"]["model"] = "structurally-invalid"

        with self.assertRaises(aa_policy.PolicyError):
            self._load_overlay()

    def test_evidence_vendor_must_be_a_supported_string(self) -> None:
        codex_evidence = next(item for item in self.overlay["evidence"] if item.get("vendor") == "codex")
        codex_evidence["vendor"] = None

        with self.assertRaises(aa_policy.PolicyError):
            self._load_overlay()

    def test_evidence_tier_never_changes_a_decision(self) -> None:
        registry, _ = self._load_overlay()
        baseline = {vendor: self._decision(registry, vendor) for vendor in TARGETS}

        for tier in ("billed-model", "client-echo", "selector-only"):
            with self.subTest(tier=tier):
                for mapping in self.overlay["mappings"]:
                    mapping["evidence_tier"] = tier
                registry, _ = self._load_overlay()
                for vendor in TARGETS:
                    self.assertEqual(self._decision(registry, vendor), baseline[vendor])
                self.assertEqual(
                    set(registry["_transport_evidence_tiers"].values()), {tier})

    def test_unknown_evidence_tier_is_refused(self) -> None:
        self.overlay["mappings"][0]["evidence_tier"] = "upstream-attested"

        with self.assertRaises(aa_policy.PolicyError):
            self._load_overlay()

    def test_stale_vendor_reports_no_tier(self) -> None:
        codex_evidence = next(item for item in self.overlay["evidence"] if item.get("vendor") == "codex")
        codex_evidence["sha256"] = "0" * 64
        for mapping in self.overlay["mappings"]:
            mapping["evidence_tier"] = "billed-model"

        registry, _ = self._load_overlay()

        reported = registry["_transport_evidence_tiers"]
        for mapping in self.overlay["mappings"]:
            present = mapping["config_id"] in reported
            self.assertEqual(present, mapping["identity"]["vendor"] != "codex")

    def test_live_overlay_default_skip_explains_host_opt_in(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("OMNILANE_TEST_LIVE_OVERLAY", None)
            result = unittest.TestResult()
            case = type(self)("test_live_overlay_loads_and_verifies_every_unstale_mapping")
            case.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors + result.failures, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0][1],
                         "Set OMNILANE_TEST_LIVE_OVERLAY=1 to run this test; "
                         "it reads the host's real transport overlay and referenced evidence.")

    def test_live_overlay_loads_and_verifies_every_unstale_mapping(self) -> None:
        """Assert the signed file, and only the host state omnilane controls.

        A fixed live count would go red whenever a vendor CLI updates itself,
        which agy and grok do in the background on invocation. The file's own
        mapping count is deterministic; how many of them a given minute's
        binaries still match is not, so staleness is reported, not failed.
        """
        if os.environ.get("OMNILANE_TEST_LIVE_OVERLAY") != "1":
            self.skipTest(
                "Set OMNILANE_TEST_LIVE_OVERLAY=1 to run this test; "
                "it reads the host's real transport overlay and referenced evidence.")
        with home_isolation.allow_real_omnilane_reads() as real_omnilane:
            live_overlay = real_omnilane / "transport-contracts.local.json"
            if not live_overlay.is_file():
                self.skipTest("host-local transport overlay is unavailable")
            overlay = json.loads(live_overlay.read_text())
            # Every probed configuration is either signed or recorded as unproven.
            # A count of signed mappings alone would drop silently when a model goes
            # away upstream, which is how the gate stops noticing.
            self.assertEqual(len(overlay["mappings"]) + len(overlay["unproven"]),
                             len(builder.PROVEN))

            with patch.dict(os.environ, self._environment(live_overlay), clear=True):
                registry, _ = aa_policy.load_registry(REGISTRY_PATH)

            stale = registry["_stale_transport_vendors"]
            expected = sum(1 for mapping in overlay["mappings"]
                           if mapping["identity"]["vendor"] not in stale)
            verified = sum(
                1
                for row in registry["scored_configs"]
                if row["transport_mapping"].get("runtime_verified") is True
            )
            self.assertEqual(verified, expected,
                             f"stale vendors {stale} should degrade only themselves")
            if stale:
                print(f"\nnote: {', '.join(stale)} evidence has drifted since the last "
                      f"sweep; {verified}/49 mappings still dispatch")


if __name__ == "__main__":
    unittest.main()
