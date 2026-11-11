"""Offline evidence-loss isolation; damaged proof never grants transport authority."""
from __future__ import annotations

import offline_env  # noqa: F401
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_transport_carry as carry
import test_resign as resign_tests
import test_overlay_health as health
import aa_policy
import build_overlay
import transport_compat as compat
import resign
from refusal_evidence import human_line
from test_probe_retry import RetryFixture


class EvidenceRadiusTests(unittest.TestCase):
    apply = carry.CarryTests.apply
    allowed = carry.CarryTests.allowed

    def setUp(self):
        carry.CarryTests.setUp(self)
        self.registry["snapshot"]["id"] = self.source_id
        self.files = json.loads(self.manifest.read_text())["probe_runs"]
        for mapping in self.overlay["mappings"]:
            cid = mapping["config_id"]
            descriptor = json.loads(Path(self.files[cid]["json"]["path"]).read_text())
            mapping.update(compat.provenance(self.rows[cid], self.source_id, self.files[cid], descriptor))
        self.overlay.update(schema_version=2, compatibility_version=compat.VERSION)

    def damage(self, vendor="codex", suffix="stdout", missing=False):
        path = Path(self.files[carry.TARGETS[vendor]][suffix]["path"])
        if missing:
            path.rename(path.with_name(path.name + ".saved"))
        else:
            path.write_text("altered but still present")

    def assert_isolated(self, vendors):
        registry = self.apply()
        for vendor in carry.TARGETS:
            decision = self.allowed(registry, vendor)
            self.assertEqual(decision["allowed"], vendor not in vendors, decision)
            if vendor in vendors:
                self.assertEqual(decision["code"], "runtime-mapping-unverified")
                self.assertIn("1 row(s) invalidated", human_line(decision))
                self.assertIn("omnilane resign --vendor " + vendor, human_line(decision))
        self.assertEqual(set(registry["_transport_invalidated_evidence"]),
                         {carry.TARGETS[v] for v in vendors})
        return registry

    def test_missing_raw_proof_only_invalidates_its_row(self):
        self.damage(missing=True)
        self.assert_isolated({"codex"})

    def test_altered_descriptor_and_streams_never_pass(self):
        for suffix in ("json", "stdout", "stderr", "rollout"):
            with self.subTest(suffix=suffix):
                path = Path(self.files[carry.TARGETS["codex"]][suffix]["path"])
                original = path.read_bytes()
                self.damage(suffix=suffix)
                self.assert_isolated({"codex"})
                path.write_bytes(original)

    def test_multiple_vendors_lose_only_their_damaged_rows(self):
        self.damage()
        self.damage("claude", missing=True)
        self.assert_isolated({"codex", "claude"})

    def test_repeat_load_does_not_reuse_prior_authority(self):
        registry = self.apply()
        self.assertTrue(self.allowed(registry, "codex")["allowed"])
        self.damage()
        registry = self.apply()
        self.assertFalse(self.allowed(registry, "codex")["allowed"])
        self.assertTrue(self.allowed(registry, "claude")["allowed"])

    def test_same_registry_reverification_revokes_prior_authority(self):
        self.registry["snapshot"]["id"] = "synthetic-next-snapshot"
        registry = self.apply()
        self.assertTrue(self.allowed(registry, "codex")["allowed"])
        self.damage()
        aa_policy.apply_transport_overlay(registry)
        decision = self.allowed(registry, "codex")
        self.assertFalse(decision["allowed"], decision)
        self.assertEqual(decision["code"], "runtime-mapping-unverified")
        self.assertTrue(self.allowed(registry, "claude")["allowed"])
        row = next(r for r in registry["scored_configs"] if r["id"] == carry.TARGETS["codex"])
        row["transport_mapping"].update(runtime_verified=True, status="verified")
        self.assertFalse(self.allowed(registry, "codex")["allowed"])

    def test_failed_validation_cannot_leave_partially_verified_rows(self):
        registry = self.apply()
        self.overlay["mappings"][-1]["runtime_effort"] = "invalid"
        self.path.write_text(json.dumps(self.overlay))
        with self.assertRaises(aa_policy.PolicyError):
            aa_policy.apply_transport_overlay(registry)
        self.assertFalse(any(r["transport_mapping"].get("runtime_verified")
                             for r in registry["scored_configs"]))

    def test_same_registry_can_be_revalidated_without_contract_drift(self):
        for snapshot in (self.source_id, "synthetic-next-snapshot"):
            with self.subTest(snapshot=snapshot):
                self.registry["snapshot"]["id"] = snapshot
                registry = self.apply()
                aa_policy.apply_transport_overlay(registry)
                self.assertTrue(all(self.allowed(registry, v)["allowed"] for v in carry.TARGETS))

    def test_direct_validation_revokes_prior_and_partial_authority(self):
        self.registry["snapshot"]["id"] = "synthetic-next-snapshot"
        registry = self.apply()
        self.damage()
        aa_policy.validate_transport_overlay(registry, self.overlay, "offline")
        self.assertFalse(self.allowed(registry, "codex")["allowed"])
        self.assertTrue(self.allowed(registry, "claude")["allowed"])
        self.overlay["mappings"][-1]["runtime_effort"] = "invalid"
        with self.assertRaises(aa_policy.PolicyError):
            aa_policy.validate_transport_overlay(registry, self.overlay, "offline")
        self.assertFalse(any(r["transport_mapping"].get("runtime_verified")
                             for r in registry["scored_configs"]))

    def test_reverification_resets_every_skipped_mapping(self):
        for cause in ("expired", "executable", "removed", "contract", "pin"):
            with self.subTest(cause=cause):
                registry = self.apply()
                self.assertTrue(self.allowed(registry, "codex")["allowed"])
                overlay = copy.deepcopy(self.overlay)
                cid = carry.TARGETS["codex"]
                if cause == "expired":
                    overlay["mappings"][0]["expires_at"] = "2000-01-01T00:00:00Z"
                elif cause == "executable":
                    overlay["evidence"][0]["sha256"] = "0" * 64
                elif cause == "removed":
                    overlay["mappings"] = [m for m in overlay["mappings"] if m["config_id"] != cid]
                elif cause == "contract":
                    registry["snapshot"]["id"] = "synthetic-next-snapshot"
                    next(r for r in registry["scored_configs"] if r["id"] == cid)["new_contract"] = True
                self.path.write_text(json.dumps(overlay))
                if cause == "pin":
                    with patch.dict(os.environ, OMNILANE_AA_OVERLAY_SHA256="0" * 64):
                        with self.assertRaises(aa_policy.PolicyError):
                            aa_policy.apply_transport_overlay(registry)
                else:
                    aa_policy.apply_transport_overlay(registry)
                    self.assertTrue(self.allowed(registry, "claude")["allowed"])
                self.assertFalse(self.allowed(registry, "codex")["allowed"])

    def test_whole_raw_directory_from_another_sweep_is_rejected(self):
        original = self.root / "evidence"
        original.mkdir()
        for files in self.files.values():
            for info in files.values():
                old = Path(info["path"])
                dest = original / old.name
                old.rename(dest)
                info["path"] = str(dest)
        for mapping in self.overlay["mappings"]:
            mapping["evidence_refs"] = self.files[mapping["config_id"]]
        self.manifest.write_text(json.dumps({"probe_runs": self.files}))
        self.overlay["evidence"][-1]["sha256"] = carry.digest(self.manifest)
        self.assert_isolated(set())
        replacement = self.root / "another-sweep"
        shutil.copytree(original, replacement)
        for files in self.files.values():
            descriptor = replacement / Path(files["json"]["path"]).name
            data = json.loads(descriptor.read_text())
            data["probed_at"] = "2099-01-01T00:00:00Z"
            descriptor.write_text(json.dumps(data))
        original.rename(self.root / "original-sweep.saved")
        replacement.rename(original)
        with self.assertRaisesRegex(aa_policy.PolicyError, "directory.*overlay"):
            self.apply()
        with self.assertRaises(aa_policy.EvidenceDirectoryError):
            resign.detect(self.overlay, {})
        self.overlay["schema_version"] = 1
        for mapping in self.overlay["mappings"]:
            for key in ("source_snapshot_id", "source_row_digest", "transport_contract_digest",
                        "evidence_refs", "verified_at"):
                mapping.pop(key)
        with self.assertRaises(aa_policy.EvidenceDirectoryError):
            self.apply()

    def test_one_complete_foreign_descriptor_is_only_row_local(self):
        path = Path(self.files[carry.TARGETS["codex"]]["json"]["path"])
        data = json.loads(path.read_text())
        data["probed_at"] = "2099-01-01T00:00:00Z"
        path.write_text(json.dumps(data))
        self.assert_isolated({"codex"})

    def test_all_truncated_descriptors_are_loss_not_foreign_directory(self):
        for files in self.files.values():
            Path(files["json"]["path"]).write_text('{"command":')
        self.assert_isolated(set(carry.TARGETS))

    def test_legacy_cross_snapshot_proof_is_also_isolated(self):
        self.overlay["schema_version"] = 1
        for mapping in self.overlay["mappings"]:
            for key in ("source_snapshot_id", "source_row_digest", "transport_contract_digest",
                        "evidence_refs", "verified_at"):
                mapping.pop(key)
        self.registry["snapshot"]["id"] = "synthetic-next-snapshot"
        self.damage()
        self.assert_isolated({"codex"})
        self.registry["snapshot"]["id"] = self.source_id
        self.assert_isolated({"codex"})

    def test_shared_raw_file_invalidates_every_referencing_row(self):
        # Empty stderr is legitimately shared; all rows that cite it must lose proof.
        stderr = self.files[carry.TARGETS["codex"]]["stderr"]
        self.files[carry.TARGETS["claude"]]["stderr"] = stderr
        for mapping in self.overlay["mappings"]:
            mapping["evidence_refs"] = self.files[mapping["config_id"]]
        self.manifest.write_text(json.dumps({"probe_runs": self.files}))
        self.overlay["evidence"][-1]["sha256"] = carry.digest(self.manifest)
        self.assert_isolated(set())
        self.damage(suffix="stderr")
        self.assert_isolated({"codex", "claude"})

    def test_symlinked_proof_is_a_global_failure(self):
        path = Path(self.files[carry.TARGETS["codex"]]["stdout"]["path"])
        saved = path.with_name(path.name + ".saved")
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(OSError):
            self.apply()

    def test_missing_shared_manifest_is_not_treated_as_an_individual_probe(self):
        self.manifest.rename(self.manifest.with_name("manifest.saved"))
        with self.assertRaises(FileNotFoundError):
            self.apply()

    def test_structural_damage_still_rejects_every_vendor(self):
        cases = ("snapshot", "engine", "schema", "refs", "identity", "overlay-pin", "manifest", "expiry")
        original = copy.deepcopy(self.overlay)
        manifest = self.manifest.read_bytes()
        for case in cases:
            with self.subTest(case=case):
                self.overlay = copy.deepcopy(original)
                self.manifest.write_bytes(manifest)
                os.environ.pop("OMNILANE_AA_OVERLAY_SHA256", None)
                if case == "snapshot":
                    self.overlay["snapshot_id"] = "unknown-sweep"
                elif case == "engine":
                    self.overlay["mappings"][0]["transport_contract_digest"] = "0" * 64
                elif case == "schema":
                    self.overlay["schema_version"] = 99
                elif case == "refs":
                    self.overlay["mappings"][0]["evidence_refs"] = self.files[carry.TARGETS["claude"]]
                elif case == "identity":
                    self.overlay["mappings"][0]["identity"]["model"] = "another-model"
                elif case == "overlay-pin":
                    os.environ["OMNILANE_AA_OVERLAY_SHA256"] = "0" * 64
                elif case == "expiry":
                    self.overlay["mappings"][0]["expires_at"] = "not-a-timestamp"
                else:
                    # A parseable manifest from another sweep is a shared trust failure.
                    self.manifest.write_text(json.dumps({"probe_runs": {}}))
                self.damage()
                with self.assertRaises(aa_policy.PolicyError):
                    self.apply()

    def test_unscored_caller_and_ceiling_remain_refused(self):
        self.damage()
        registry = self.assert_isolated({"codex"})
        self.assertFalse(self.allowed(registry, "claude", ceiling=0)["allowed"])
        caller = dict(caller=dict(self.overlay["mappings"][1]["identity"], model="unscored"),
                      inherited_ceiling=100)
        decision = aa_policy.decide(registry, "fixture", vendor="claude",
                                   model=self.rows[carry.TARGETS["claude"]]["model"], effort="high",
                                   caller=caller, caller_sha256="fixture")
        self.assertEqual(decision["code"], "unknown-caller-config")
        self.assertFalse(decision["allowed"])

    def test_health_reports_row_count_and_vendor_reprobe(self):
        self.damage()
        self.path.write_text(json.dumps(self.overlay))
        rc, level, message = health.run(self.path)
        self.assertEqual((rc, level), (0, "WARN"), message)
        self.assertIn("1 row(s) invalidated", message)
        self.assertIn("omnilane resign --vendor codex", message)
        self.assertNotIn("every dispatch is refused", message)


class FailedEvidenceRadiusTests(RetryFixture, unittest.TestCase):
    def test_retired_failed_evidence_does_not_block_other_vendors(self):
        entry = self.fail_row("api")
        cid = entry["config_id"]
        registry = copy.deepcopy(build_overlay.REGISTRY)
        registry["snapshot"]["id"] = "synthetic-next-snapshot"
        registry["scored_configs"] = [row for row in registry["scored_configs"] if row["id"] != cid]
        rows = {row["id"]: row for row in registry["scored_configs"]}
        proven = {key: value for key, value in build_overlay.PROVEN.items() if key != cid}
        original = self.live.read_bytes()
        with patch.object(build_overlay, "REGISTRY", registry), \
                patch.object(build_overlay, "ROWS", rows), \
                patch.object(build_overlay, "PROVEN", proven):
            overlay = self.overlay()
            clean = resign.detect(overlay, resign.current_anchors())
            path = self.sweep_root / "evidence" / (entry["name"] + ".json")
            path.write_text(path.read_text() + " ")
            report = resign.detect(overlay, resign.current_anchors())
            self.assertEqual(set(report), set(resign.VENDORS))
            self.assertEqual(report, clean)
            self.assertEqual(self.run_retry(vendor=["codex"], check=True), 0, self.lines)
            self.assertEqual(self.run_retry(vendor=["codex"]), 0, self.lines)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.live.read_bytes(), original)

    def test_current_failed_evidence_damaged_after_validation_is_refused(self):
        entry = self.fail_row("api")
        path = self.sweep_root / "evidence" / (entry["name"] + ".json")
        original = self.live.read_bytes()
        validate = aa_policy.validate_transport_overlay

        def validate_then_damage(*args):
            validate(*args)
            path.write_text(path.read_text() + " ")

        with patch.object(aa_policy, "validate_transport_overlay", side_effect=validate_then_damage):
            with self.assertRaisesRegex(ValueError, "failed probe evidence changed after validation"):
                resign.detect(self.overlay(), resign.current_anchors())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.live.read_bytes(), original)

    def test_failed_probe_damage_preserves_intact_rows_and_blocks_automatic_retry(self):
        entry = self.fail_row("api")
        before, _ = aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
        count = lambda r: sum(x["vendor"] == "codex" and x["transport_mapping"].get("runtime_verified")
                              for x in r["scored_configs"])
        self.assertGreater(count(before), 0)
        path = self.sweep_root / "evidence" / (entry["name"] + ".json")
        path.write_text(path.read_text() + " ")
        after, _ = aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
        self.assertEqual(count(before), count(after))
        self.assertIn(entry["config_id"], after["_transport_invalidated_evidence"])
        self.assertFalse(self.decision(entry)["allowed"])
        report = resign.detect(self.overlay(), resign.current_anchors())
        self.assertIn(entry["config_id"], report["claude"]["invalidated_evidence"])
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [])

    def test_failed_evidence_cooldown_survives_another_vendor_repair(self):
        entry = self.fail_row("api")
        path = self.sweep_root / "evidence" / (entry["name"] + ".json")
        path.write_text(path.read_text() + " ")
        codex = next(m for m in self.overlay()["mappings"] if m["identity"]["vendor"] == "codex")
        Path(codex["evidence_refs"]["stdout"]["path"]).write_text("damaged")
        self.assertEqual(self.run_retry(vendor=["codex"]), 0, self.lines)
        self.calls.clear()
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [])
        self.assertIn(entry["config_id"], resign.detect(self.overlay(), resign.current_anchors())
                      ["claude"]["invalidated_evidence"])
        self.assertFalse(self.decision(entry)["allowed"])

    def test_explicit_retry_repairs_only_the_invalidated_failed_selector(self):
        entry = self.fail_row("api")
        path = self.sweep_root / "evidence" / (entry["name"] + ".json")
        path.rename(path.with_suffix(".saved"))
        self.assertEqual(self.run_retry(retry_failed=True), 0, self.lines)
        self.assertEqual(self.calls, [entry["config_id"]])
        self.assertTrue(self.decision(entry)["allowed"])

    def test_same_vendor_repair_does_not_retry_a_damaged_failed_selector(self):
        entry = self.fail_row("api")
        path = self.sweep_root / "evidence" / (entry["name"] + ".json")
        path.write_text(path.read_text() + " ")
        other = next(m for m in self.overlay()["mappings"] if m["identity"]["vendor"] == "claude")
        Path(other["evidence_refs"]["stdout"]["path"]).write_text("damaged")
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [other["config_id"]])
        self.assertFalse(self.decision(entry)["allowed"])
        self.calls.clear()
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [])

    def test_missing_mixed_success_and_failure_directory_is_row_local(self):
        entry = self.fail_row("api")
        raw = self.sweep_root / "evidence"
        raw.rename(self.sweep_root / "evidence.saved")
        registry, _ = aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
        self.assertIn(entry["config_id"], registry["_transport_invalidated_evidence"])
        self.assertFalse(any(r["transport_mapping"].get("runtime_verified") for r in registry["scored_configs"]))

    def test_replaced_mixed_success_and_failure_directory_is_global(self):
        self.fail_row("api")
        raw = self.sweep_root / "evidence"
        replacement = self.sweep_root / "another-sweep"
        shutil.copytree(raw, replacement)
        manifest = json.loads((self.sweep_root / "probe-manifest.json").read_text())
        runs = dict(manifest["probe_runs"], **manifest["failed_probe_runs"])
        for files in runs.values():
            path = replacement / Path(files["json"]["path"]).name
            descriptor = json.loads(path.read_text())
            descriptor["probed_at"] = "2099-01-01T00:00:00Z"
            path.write_text(json.dumps(descriptor))
        raw.rename(self.sweep_root / "evidence.saved")
        replacement.rename(raw)
        with self.assertRaises(aa_policy.EvidenceDirectoryError):
            aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
        top = max(build_overlay.ROWS.values(), key=lambda row: row["score"])
        caller = self.base / "caller.json"
        caller.write_text(json.dumps(dict(schema_version=1, kind="model",
            snapshot_id=build_overlay.REGISTRY["snapshot"]["id"],
            caller=aa_policy._row_identity(top), inherited_ceiling=100)))
        mapping = next(m for m in self.overlay()["mappings"] if m["identity"]["vendor"] == "codex")
        result = subprocess.run([sys.executable, str(build_overlay.REPO / "scripts/lib/aa_policy.py"),
            "--registry", str(build_overlay.REPO / "config/aa-model-policy.json"),
            "--caller-context", str(caller), "--vendor", "codex",
            "--model", mapping["runtime_model"], "--effort", mapping["runtime_effort"] or "none"],
            text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        decision = json.loads(result.stdout)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "invalid-policy-input")
        self.assertIn("directory does not belong", decision["message"])


class EvidenceResignTests(unittest.TestCase):
    setUp = resign_tests.ResignTests.setUp
    descriptor = resign_tests.ResignTests.descriptor
    build = resign_tests.ResignTests.build
    vendor_of = resign_tests.ResignTests.vendor_of
    facts = resign_tests.ResignTests.facts
    update = resign_tests.ResignTests.update
    run_resign = resign_tests.ResignTests.run_resign

    def damage(self, vendor="codex", suffix="stdout", missing=False):
        mapping = next(m for m in json.loads(self.live.read_text())["mappings"]
                       if m["identity"]["vendor"] == vendor)
        path = Path(mapping["evidence_refs"][suffix]["path"])
        if missing:
            path.rename(path.with_name(path.name + ".saved"))
        else:
            path.write_text("altered")
        return mapping["config_id"]

    def test_resign_reprobes_only_the_invalidated_selector(self):
        original_count = resign.verified(self.live)["codex"]
        cid = self.damage()
        self.assertEqual(self.run_resign(vendor=["codex"]), 0, self.lines)
        self.assertTrue(self.only_missing["codex"])
        self.assertEqual(self.probed["codex"], [cid])
        self.assertEqual(resign.verified(self.live)["codex"], original_count)

    def test_missing_descriptor_and_missing_raw_directory_can_be_reprobed(self):
        cid = self.damage(suffix="json", missing=True)
        self.assertEqual(self.run_resign(vendor=["codex"]), 0, self.lines)
        self.assertEqual(self.probed["codex"], [cid])
        source = resign.previous_root(json.loads(self.live.read_text()))
        (source / "evidence").rename(source / "saved-evidence")
        self.assertEqual(self.run_resign(vendor=["codex"]), 0, self.lines)
        self.assertGreater(len(self.probed["codex"]), 1)
        overlay = json.loads(self.live.read_text())
        self.assertEqual({m["identity"]["vendor"] for m in overlay["mappings"]}, {"codex"})
        # A later vendor-specific repair must still find a vendor with zero intact rows.
        self.assertEqual(self.run_resign(vendor=["claude"]), 0, self.lines)
        self.assertGreater(len(self.probed["claude"]), 1)
        self.assertGreater(resign.verified(self.live)["claude"], 0)

    def test_invalid_row_cannot_become_pending_old_authority_when_reprobe_fails(self):
        cid = self.damage()
        self.assertEqual(self.run_resign(vendor=["codex"], failing=[cid]), 0, self.lines)
        overlay = json.loads(self.live.read_text())
        self.assertNotIn(cid, {m["config_id"] for m in overlay["mappings"]})
        item = next(u for u in overlay["unproven"] if u["config_id"] == cid)
        self.assertNotIn("pending_recheck", item)

    def test_dry_run_refusal_and_doctor_use_real_cli_entrypoints(self):
        for path in self.bins.values():
            path.write_text('#!/bin/sh\n[ "$1" = "--version" ] && echo fixture && exit 0\nexit 97\n')
            path.chmod(0o755)
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        cid = self.damage()
        env = dict(os.environ)
        env.update({"AGY_BIN" if vendor == "gemini" else vendor.upper() + "_BIN": str(path)
                    for vendor, path in self.bins.items()})
        env["PATH"] = os.pathsep.join([str(p.parent) for p in self.bins.values()] + [env["PATH"]])
        env.pop("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN", None)
        caller = self.base / "caller.json"
        top = max(build_overlay.ROWS.values(), key=lambda r: r["score"])
        caller.write_text(json.dumps(dict(schema_version=1, snapshot_id=build_overlay.REGISTRY["snapshot"]["id"],
                                         kind="model", caller=aa_policy._row_identity(top), inherited_ceiling=100)))
        row = build_overlay.ROWS[cid]
        command = ["/bin/bash", str(carry.ROOT / "bin/omnilane"), "dispatch", "--dry-run", "--caller-context", str(caller),
                   "--vendor", "codex", "--model", row["model"], "--effort", row["effort"] or "none",
                   "--workdir", str(self.base), "hardest-coding", "offline evidence-radius smoke"]
        refused = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(refused.returncode, 3, refused.stdout + refused.stderr)
        self.assertIn("1 row(s) invalidated", refused.stderr)
        self.assertIn("omnilane resign --vendor codex", refused.stderr)
        doctor = subprocess.run(["/bin/bash", str(carry.ROOT / "bin/omnilane"), "doctor", "--json"],
                                env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        checks = json.loads(doctor.stdout)["checks"]
        entry = next(c for c in checks if c["check"] == "transport-overlay")
        self.assertEqual(entry["level"], "WARN")
        self.assertIn("1 row(s) invalidated", entry["message"])

    def test_repairing_one_vendor_cannot_reseal_another_damaged_row(self):
        codex = self.damage()
        claude = self.damage("claude", suffix="json")
        self.assertEqual(self.run_resign(vendor=["codex"]), 0, self.lines)
        self.assertEqual(self.probed["codex"], [codex])
        overlay = json.loads(self.live.read_text())
        self.assertNotIn(claude, {m["config_id"] for m in overlay["mappings"]})
        self.assertIn(claude, {u["config_id"] for u in overlay["unproven"]})

    def test_invalid_or_changed_signer_still_refuses_before_any_probe(self):
        self.damage()
        self.update("codex")
        for signer in ("OTHERTEAM1", "unsigned", "invalid", "unverifiable"):
            with self.subTest(signer=signer):
                self.signers["codex"] = signer
                self.assertEqual(self.run_resign(vendor=["codex"]), 20, self.lines)
                self.assertEqual(self.sweeps, [])

    def test_missing_descriptor_with_refused_signer_keeps_intact_rows_available(self):
        cid = self.damage(suffix="json", missing=True)
        self.update("codex")
        self.signers["codex"] = "OTHERTEAM1"
        self.assertEqual(self.run_resign(vendor=["codex"]), 20, self.lines)
        self.assertEqual(self.sweeps, [])
        overlay = json.loads(self.live.read_text())
        self.assertNotIn(cid, {m["config_id"] for m in overlay["mappings"]})
        self.assertGreater(resign.verified(self.live)["claude"], 0)
        registry, _ = aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
        self.assertIn(cid, registry["_transport_invalidated_evidence"])


if __name__ == "__main__":
    unittest.main()
