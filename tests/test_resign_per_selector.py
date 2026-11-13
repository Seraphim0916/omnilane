"""Selector-level resign contracts through real policy/CLI code and offline probes."""
from __future__ import annotations

import offline_env  # noqa: F401
import copy
from collections import UserDict
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_resign as existing
import test_doctor_diagnostics as diagnostics
import test_probe_retry as retry_fixture
import aa_policy
import build_overlay
import probe
import probe_sweep
import probe_failure
import resign
import selector_recheck
import verification_status
import transport_compat
from test_cov3_verification_status import StatusHome

ROOT = existing.ROOT
QUOTA = ("error: Individual quota reached. Please upgrade your subscription to increase "
         "your limits. Resets in 37h16m12s.")


class SelectorFixture:
    descriptor = existing.ResignTests.descriptor
    build = existing.ResignTests.build
    vendor_of = existing.ResignTests.vendor_of
    facts = existing.ResignTests.facts
    update = existing.ResignTests.update
    doctor = diagnostics.DoctorDiagnosticsTests.doctor

    def setUp(self):
        existing.ResignTests.setUp(self)
        self.entries = probe_sweep.plan("gemini")
        self.failures = {e["config_id"]: "quota" for e in self.entries if "claude" in e["model"]}
        self.assertEqual((len(self.entries), len(self.failures)), (17, 6))
        self.calls = []
        self.lines = []
        self.smoke_calls = []

    def sweep(self, vendor, root, *, only_missing=False, run_probe=None, **kwargs):
        passed, failed = [], []
        for entry in probe_sweep.plan(vendor):
            path = root / "evidence" / f"{entry['name']}.json"
            if only_missing and path.is_file():
                continue
            self.calls.append(entry["config_id"])
            self.descriptor(root, entry["name"], "pass")
            kind = self.failures.get(entry["config_id"])
            if kind:
                record = json.loads(path.read_text())
                stdout = (path.with_suffix(".stdout")).read_text()
                stderr = QUOTA if kind == "quota" else ""
                record["exit_code"] = 3 if kind == "quota" else 0
                extra = {"cli_log": "model\twrong-model\n"} if kind == "wrong-model" else {}
                if kind == "quota":
                    stdout = ""
                verdict = probe.verdict(record, stdout, stderr, "agy", probe_sweep.TOKENS[vendor], extra)
                record.update(zip(("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict))
                path.write_text(json.dumps(record))
                path.with_suffix(".stdout").write_text(stdout)
                path.with_suffix(".stderr").write_text(stderr)
                path.with_suffix(".cli_log").write_text(extra.get("cli_log", ""))
            record = json.loads(path.read_text())
            (passed if record["verdict"] == "pass" else failed).append(entry["config_id"])
        return dict(vendor=vendor, outcome="done", detail="offline fixture", passed=passed, failed=failed)

    def run_case(self, *flags, smoke=None, all_vendors=False, interactive=True, now=None):
        self.lines = []
        self.args_summary = None
        def checked_smoke(vendor, overlay, path):
            self.smoke_calls.append((vendor, copy.deepcopy(overlay), path))
            return (True, "offline smoke") if smoke is None else smoke
        output = io.StringIO()
        clock_context = patch.object(probe_failure, "datetime", wraps=datetime) if now else nullcontext()
        with patch.object(probe_sweep, "sweep", self.sweep), \
                patch.object(resign, "canary", return_value=(True, "fixture version")), \
                patch.object(resign, "smoke", side_effect=checked_smoke), \
                patch.object(sys.stdin, "isatty", return_value=interactive), \
                patch("sys.stderr", io.StringIO()) as err, redirect_stdout(output), clock_context as clock:
            if clock is not None:
                clock.now.return_value = now
            result = resign.main([*(["--vendor", "gemini"] if not all_vendors else []), "--json", *flags])
        self.lines = err.getvalue().splitlines()
        self.summary = json.loads(output.getvalue()) if output.getvalue() else {}
        return result

    def overlay(self):
        return json.loads(self.live.read_text())

    def pending(self):
        return {u["config_id"]: u for u in self.overlay().get("unproven", []) if u.get("pending_recheck")}

    def decision(self, entry):
        registry, digest = aa_policy.load_registry(ROOT / "config/aa-model-policy.json")
        caller = max(registry["scored_configs"], key=lambda r: r["score"])
        return aa_policy.decide(registry, digest, vendor="gemini", model=entry["model"], effort=None,
                                caller={"caller": aa_policy._row_identity(caller), "inherited_ceiling": 100},
                                caller_sha256="offline")

    def cli_environment(self):
        env = dict(os.environ)
        for vendor, path in self.bins.items():
            env["AGY_BIN" if vendor == "gemini" else f"{vendor.upper()}_BIN"] = str(path)
        env["PATH"] = os.pathsep.join([str(p.parent) for p in self.bins.values()] + [env["PATH"]])
        env.pop("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN", None)
        return env


class SelectorResignTests(SelectorFixture, unittest.TestCase):
    def test_previous_approved_snapshot_installs_four_current_proofs(self):
        old = self.overlay()
        old["schema_version"] = 1
        old["snapshot_id"] = "aa-v4.3.2-2026-10-28-v1"
        source = transport_compat.archive()[0]["snapshots"][old["snapshot_id"]]
        rows = {r["id"]: r for r in source["rows"]}
        fields = {"config_id", "identity", "verification", "runtime_effort", "runtime_model",
                  "selector_type", "cli_flag", "evidence_tier"}
        old["mappings"] = [{k: v for k, v in m.items() if k in fields}
                           for m in old["mappings"] if m["identity"]["vendor"] == "gemini"
                           and m["config_id"] in rows]  # Carbon and Hydrogen are absent from that snapshot.
        for mapping in old["mappings"]:
            mapping["identity"] = aa_policy._row_identity(rows[mapping["config_id"]])
        old["unproven"] = []
        old["evidence"] = [e for e in old["evidence"] if e.get("vendor") in (None, "gemini")]
        manifest = self.sweep_root / "probe-manifest.json"
        data = json.loads(manifest.read_text())
        data["probe_runs"] = {cid: files for cid, files in data["probe_runs"].items()
                              if cid.startswith("gemini/") and cid in rows}
        manifest.write_text(json.dumps(data))
        next(e for e in old["evidence"] if not e.get("vendor"))["sha256"] = resign.sha256(manifest)
        self.live.write_text(json.dumps(old))
        self.assertEqual(resign.verified(self.live)["gemini"], 10)
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        self.assertEqual(resign.verified(self.live)["gemini"], 11)
        self.assertEqual(len(self.pending()), 6)
        self.assertEqual(self.overlay()["snapshot_id"], build_overlay.REGISTRY["snapshot"]["id"])

    def test_four_pass_six_pending_install_current_pin_and_retry_without_cooldown(self):
        old = self.overlay()
        self.update("gemini")
        self.assertEqual(self.run_case(all_vendors=True), 60, self.lines)
        overlay = self.overlay()
        self.assertEqual(resign.verified(self.live)["gemini"], 11)
        self.assertEqual(set(self.pending()), set(self.failures))
        self.assertEqual(overlay["snapshot_id"], build_overlay.REGISTRY["snapshot"]["id"])
        for cid, item in self.pending().items():
            original = next(m for m in old["mappings"] if m["config_id"] == cid)
            self.assertEqual(item["pending_recheck"]["mapping"], original)
            self.assertEqual(item["pending_recheck"]["consecutive_runs"], 1)
        self.assertEqual(self.summary["partially_re_signed"]["gemini"]["pending_count"], 6)
        self.failures.clear()
        self.calls.clear()
        self.assertEqual(self.run_case(), 0, self.lines)
        self.assertEqual(set(self.calls), set(m["config_id"] for m in old["mappings"]
                                             if m["identity"]["vendor"] == "gemini" and "claude" in m["runtime_model"]))
        self.assertEqual(resign.verified(self.live)["gemini"], 17)
        self.assertEqual(self.pending(), {})

    def test_non_environment_failure_holds_vendor_and_preserves_original_proofs(self):
        cid = next(iter(self.failures))
        self.failures[cid] = "wrong-model"
        old = self.overlay()
        self.update("gemini")
        self.assertEqual(self.run_case(), 20, self.lines)
        self.assertEqual(self.summary["re_signed"], [])
        self.assertEqual([e for e in self.overlay()["evidence"] if e.get("vendor") == "gemini"],
                         [e for e in old["evidence"] if e.get("vendor") == "gemini"])
        self.assertEqual(self.smoke_calls, [])

    def test_tier_regression_holds_vendor_even_if_probe_passed(self):
        self.failures = {self.entries[0]["config_id"]: "weak-proof"}
        self.update("gemini")
        self.assertEqual(self.run_case(), 20, self.lines)
        self.assertIn("evidence-tier-regression", "\n".join(self.lines))

    def test_origin_changes_never_probe_or_install_new_proof(self):
        for signer in ("OTHERTEAM1", "unsigned", "invalid", "unverifiable"):
            with self.subTest(signer=signer):
                old = self.overlay()
                self.update("gemini")
                self.signers["gemini"] = signer
                self.assertEqual(self.run_case(), 20, self.lines)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.summary["re_signed"], [])
                self.assertEqual(self.overlay()["evidence"], old["evidence"])

    def test_allow_shrink_removes_retained_selectors(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        self.assertEqual(self.run_case("--allow-shrink"), 20, self.lines)
        self.assertEqual(self.pending(), {})
        self.assertEqual(resign.verified(self.live)["gemini"], 11)

    def test_partial_check_doctor_and_refusal_name_selector_and_reason(self):
        for path in self.bins.values():
            path.write_text('#!/bin/sh\n[ "$1" = "--version" ] && echo fixture && exit 0\nexit 97\n')
            path.chmod(0o755)
        self.assertEqual(self.run_case(), 60, self.lines)
        for entry in self.entries:
            result = self.decision(entry)
            self.assertEqual(result["allowed"], entry["config_id"] not in self.failures, result)
            if not result["allowed"]:
                self.assertEqual(result["code"], "selector-pending-recheck")
                self.assertIn(entry["config_id"], result["message"])
                self.assertIn("quota", result["message"])
                self.assertIn("Resets in 37h16m12s", result["message"])
        self.assertEqual(self.run_case("--check"), 60, self.lines)
        entry = self.summary["vendors"]["gemini"]
        self.assertFalse(entry["stale"])
        self.assertEqual(entry["pending_count"], 6)
        self.doctor_env = self.cli_environment()
        result = self.doctor("omnilane doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("6 selector(s) pending", result.stdout)
        self.assertNotIn("stale vendor(s) gemini", result.stdout)
        for cid in self.failures:
            self.assertIn(cid, result.stdout)

    def test_daily_keeps_exact_partial_counts_and_clears_after_recovery(self):
        self.update("gemini")
        self.assertEqual(self.run_case("--daily"), 60, self.lines)
        state = verification_status.load()["pending"]["gemini"]
        self.assertEqual(state["reason"], "partially-re-signed")
        self.assertEqual(set(state["config_ids"]), set(self.failures))
        self.assertEqual(state["pending_count"], 6)
        self.assertEqual(self.run_case("--daily", "--check"), 60, self.lines)
        self.assertEqual(verification_status.load()["pending"]["gemini"]["pending_count"], 6)
        self.failures.clear()
        self.assertEqual(self.run_case("--daily", now=datetime.now(timezone.utc) + timedelta(hours=38)), 0, self.lines)
        self.assertNotIn("gemini", verification_status.load()["pending"])

    def test_partial_smoke_failure_rolls_back_exact_overlay(self):
        old = self.live.read_bytes()
        self.update("gemini")
        self.assertEqual(self.run_case(smoke=(False, "fixture failure")), 30, self.lines)
        self.assertEqual(self.live.read_bytes(), old)

    def test_smoke_chooses_a_verified_selector(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        called = []
        def runner(argv, **kwargs):
            called.append(argv)
            return existing.SimpleNamespace(returncode=1, stdout="", stderr="fixture stop")
        ok, _ = resign.smoke("gemini", self.overlay(), self.live, runner=runner)
        self.assertFalse(ok)
        model = called[0][called[0].index("--model") + 1]
        selected = next(e for e in self.entries if e["model"] == model)
        self.assertNotIn(selected["config_id"], self.pending())
        self.assertTrue(self.decision(selected)["allowed"])

    def test_later_cli_drift_does_not_hide_pending_reason_or_allow_other_rows(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        self.update("gemini", version="1.2.0")
        for entry in self.entries:
            result = self.decision(entry)
            self.assertFalse(result["allowed"])
            if entry["config_id"] in self.failures:
                self.assertEqual(result["code"], "selector-pending-recheck")
                self.assertIn("quota", result["message"])

    def test_dispatch_dry_run_allows_verified_and_names_pending_reason(self):
        for path in self.bins.values():
            path.write_text('#!/bin/sh\n[ "$1" = "--version" ] && echo fixture && exit 0\nexit 97\n')
            path.chmod(0o755)
        self.assertEqual(self.run_case(), 60, self.lines)
        registry = build_overlay.REGISTRY
        row = max(registry["scored_configs"], key=lambda r: r["score"])
        caller = self.base / "caller.json"
        caller.write_text(json.dumps(dict(schema_version=1, kind="model", snapshot_id=registry["snapshot"]["id"],
                                          caller=aa_policy._row_identity(row), inherited_ceiling=row["score"])))
        for pending in (False, True):
            entry = next(e for e in self.entries if (e["config_id"] in self.failures) == pending)
            result = subprocess.run(["/bin/bash", str(ROOT / "bin/omnilane"), "route", "--dry-run",
                                     "--caller-context", str(caller), "--vendor", "gemini", "--model",
                                     entry["model"], "consult", "offline selector test"],
                                    env=self.cli_environment(), text=True, capture_output=True, timeout=30)
            output = result.stdout + result.stderr
            if "ps" in output and "sandbox" in output:
                self.skipTest(output.strip())
            if pending:
                self.assertNotEqual(result.returncode, 0, output)
                for value in ("selector-pending-recheck", entry["config_id"], "quota", "Resets in 37h16m12s"):
                    self.assertIn(value, output)
            else:
                self.assertEqual(result.returncode, 0, output)
                self.assertIn("dry_run=yes", result.stdout)

    def test_repeated_partial_run_keeps_all_selectors_and_advances_counters(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        self.calls.clear()
        self.assertEqual(self.run_case(), 60, self.lines)
        self.assertEqual(set(self.calls), set(self.failures))
        self.assertEqual(resign.verified(self.live)["gemini"], 11)
        self.assertTrue(all(u["pending_recheck"]["consecutive_runs"] == 2 for u in self.pending().values()))

    def test_all_environment_failures_remain_pending_but_are_not_partial_success(self):
        self.failures = {e["config_id"]: "quota" for e in self.entries}
        self.update("gemini")
        self.assertEqual(self.run_case("--daily"), 20, self.lines)
        self.assertEqual(self.summary["re_signed"], [])
        self.assertNotIn("gemini", self.summary.get("partially_re_signed", {}))
        self.assertEqual(len(self.pending()), 17)
        self.assertEqual(resign.verified(self.live)["gemini"], 0)
        self.assertEqual(self.smoke_calls, [])
        self.assertEqual(verification_status.load()["pending"]["gemini"]["pending_count"], 17)
        self.assertEqual(self.run_case("--check"), 10, self.lines)
        self.failures.clear()
        self.calls.clear()
        self.assertEqual(self.run_case(), 0, self.lines)
        self.assertEqual(len(self.calls), 17)
        self.assertEqual(resign.verified(self.live)["gemini"], 17)

    def test_corrupted_pending_proof_is_reported_and_cannot_dispatch(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        pending = next(e for e in self.entries if e["config_id"] in self.pending())
        root = resign.previous_root(self.overlay())
        evidence = root / "evidence" / f"{pending['name']}.stderr"
        evidence.write_text(evidence.read_text() + " changed")
        before = self.live.read_bytes()
        self.calls.clear()
        # It was already pending; intact siblings keep the existing partial
        # status, while damaged failure evidence adds a row-local diagnosis.
        self.assertEqual(self.run_case("--check"), 60, self.lines)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.live.read_bytes(), before)
        self.assertFalse(self.decision(pending)['allowed'])
        self.assertEqual(resign.verified(self.live)['gemini'], 11)
        self.assertEqual(self.summary['vendors']['gemini']['invalidated_evidence'],
                         [pending['config_id']])
        self.assertIn('1 row(s) invalidated', '\n'.join(self.lines))
        self.assertIn('omnilane resign --vendor gemini', '\n'.join(self.lines))


class SelectorFixTests(SelectorFixture, unittest.TestCase):
    def test_pending_becomes_deterministic_without_holding_recovered_siblings(self):
        self.update("gemini")
        self.assertEqual(self.run_case("--daily"), 60, self.lines)
        cid = next(iter(self.failures))
        self.failures = {cid: "wrong-model"}
        self.calls.clear()
        self.assertEqual(self.run_case(), 0, self.lines)  # An interactive retry refreshes saved daily status too.
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.summary["held"], [])
        self.assertEqual(resign.verified(self.live)["gemini"], 16)
        self.assertEqual(self.pending(), {})
        row = next(u for u in self.overlay()["unproven"] if u["config_id"] == cid)
        self.assertEqual(row["verdict_reason"], "client-record-mismatch")
        decision = self.decision(next(e for e in self.entries if e["config_id"] == cid))
        self.assertFalse(decision["allowed"])
        self.assertIn("client-record-mismatch", decision["message"])
        self.assertNotIn("quota", decision["message"])
        self.assertIn("client-record-mismatch", verification_status.pending_summary())
        self.assertEqual(verification_status.load()["pending"]["gemini"]["config_ids"], [cid])
        self.doctor_env = self.cli_environment()
        doctor = self.doctor("omnilane doctor", "--json")
        self.assertIn("client-record-mismatch", doctor.stdout)
        self.calls.clear()
        self.assertEqual(self.run_case("--daily", interactive=False), 0, self.lines)
        self.assertEqual(self.calls, [])
        self.assertIn("client-record-mismatch", verification_status.pending_summary())

    def test_pending_tier_regression_is_unproven_without_holding_vendor(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        cid = next(iter(self.failures))
        self.failures = {cid: "weak-proof"}
        self.assertEqual(self.run_case(), 0, self.lines)
        self.assertEqual(self.summary["held"], [])
        self.assertEqual(resign.verified(self.live)["gemini"], 16)
        self.assertEqual(self.pending(), {})
        self.assertFalse(self.decision(next(e for e in self.entries if e["config_id"] == cid))["allowed"])

    def test_unattended_third_pending_run_cools_down_and_interactive_always_retries(self):
        with patch(__name__ + ".QUOTA", "quota exhausted"):
            self.update("gemini")
            self.assertEqual(self.run_case(interactive=False), 60, self.lines)
            self.assertEqual(len(self.calls), 17)
            self.calls.clear()
            self.assertEqual(self.run_case(interactive=False), 60, self.lines)
            self.assertEqual(len(self.calls), 6)
            self.calls.clear()
            self.assertEqual(self.run_case(interactive=False), 60, self.lines)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.run_case("--retry-failed", interactive=False), 60, self.lines)
            self.assertEqual(self.calls, [])
            self.assertTrue(all(u["pending_recheck"]["consecutive_runs"] == 2 for u in self.pending().values()))
            with patch.object(probe_failure, "datetime", wraps=datetime) as clock:
                clock.now.return_value = datetime.now(timezone.utc) + timedelta(hours=21)
                self.assertEqual(self.run_case("--daily", interactive=False), 60, self.lines)
            self.assertEqual(len(self.calls), 6)
            self.assertTrue(all(u["pending_recheck"]["consecutive_runs"] == 3 for u in self.pending().values()))
            self.calls.clear()
            self.assertEqual(self.run_case(interactive=True), 60, self.lines)
            self.assertEqual(len(self.calls), 6)

    def test_reset_hint_blocks_unattended_but_not_interactive(self):
        self.update("gemini")
        self.assertEqual(self.run_case(interactive=False), 60, self.lines)
        self.calls.clear()
        before = {cid: row["pending_recheck"] for cid, row in self.pending().items()}
        self.assertEqual(self.run_case("--daily", interactive=True), 60, self.lines)
        self.assertEqual(self.calls, [])  # --daily remains unattended even with a tty.
        self.assertEqual(self.run_case("--daily", "--retry-failed", interactive=True), 60, self.lines)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_case("--retry-failed", interactive=False), 60, self.lines)
        self.assertEqual(self.calls, [])
        self.assertEqual({cid: row["pending_recheck"] for cid, row in self.pending().items()}, before)
        with patch.object(probe_failure, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(hours=38)
            self.assertEqual(self.run_case(interactive=False), 60, self.lines)
        self.assertEqual(len(self.calls), 6)
        self.calls.clear()
        self.assertEqual(self.run_case(interactive=True), 60, self.lines)
        self.assertEqual(len(self.calls), 6)

    def test_allow_shrink_restores_ordinary_failure_cooldown(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        self.assertEqual(self.run_case("--allow-shrink"), 20, self.lines)
        self.assertEqual(self.pending(), {})
        failures = probe_failure.failed_probes(self.overlay(), "gemini")
        self.assertEqual(set(failures), set(self.failures))
        self.assertTrue(all(probe_failure.cooldown(f["last_failed_at"])[0] for f in failures.values()))
        self.calls.clear()
        self.assertEqual(self.run_case(interactive=False), 20, self.lines)
        self.assertEqual(self.calls, [])
        for entry in self.entries:
            if entry["config_id"] in self.failures:
                self.assertNotEqual(self.decision(entry)["code"], "selector-pending-recheck")

    def test_skipped_pending_keeps_its_timestamp_and_counter_when_siblings_recover(self):
        with patch(__name__ + ".QUOTA", "quota exhausted"):
            self.update("gemini")
            self.assertEqual(self.run_case(), 60, self.lines)
            changed = self.overlay()
            item = next(u for u in changed["unproven"] if u.get("pending_recheck"))
            item["pending_recheck"]["consecutive_runs"] = 2
            cid = item["config_id"]
            before = copy.deepcopy(item)
            self.live.write_text(json.dumps(changed))
            self.failures.clear()
            self.calls.clear()
            self.assertEqual(self.run_case(interactive=False), 60, self.lines)
            self.assertEqual(len(self.calls), 5)
            self.assertNotIn(cid, self.calls)
            self.assertEqual(self.pending(), {cid: before})
            self.assertEqual(resign.verified(self.live)["gemini"], 16)

    def test_unattended_allow_shrink_drops_pending_without_bypassing_reset(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        self.calls.clear()
        self.assertEqual(self.run_case("--allow-shrink", interactive=False), 20, self.lines)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.pending(), {})
        self.assertTrue(all(probe_failure.cooldown(row["last_failed_at"])[0]
                            for row in probe_failure.failed_probes(self.overlay(), "gemini").values()))

    def test_daily_unprobed_gap_takes_priority_over_partial_success(self):
        self.update("gemini")
        original = resign.unprobed
        gap = probe_sweep.plan("grok")[0]["config_id"]
        def unprobed(overlay, vendor):
            return [gap] if vendor == "grok" else original(overlay, vendor)
        with patch.object(resign, "unprobed", side_effect=unprobed):
            self.assertEqual(self.run_case("--daily"), 20, self.lines)
        state = verification_status.load()["pending"]
        self.assertIn(gap, state["grok"]["config_ids"])
        self.assertEqual(state["gemini"]["pending_count"], 6)
        verification_status.record(60, summary={"partially_re_signed": {"gemini":
            {"verified_count": 11, "pending_count": 6, "config_ids": sorted(self.failures)}},
            "still_unprobed": {"grok": [gap]}})
        self.assertIn(gap, verification_status.load()["pending"]["grok"]["config_ids"])
        before = copy.deepcopy(verification_status.load()["pending"]["grok"])
        verification_status.record(60, summary={"partially_re_signed": {"gemini":
            {"verified_count": 11, "pending_count": 6, "config_ids": sorted(self.failures)}}})
        self.assertEqual(verification_status.load()["pending"]["grok"], before)

    def test_check_does_not_mask_non_pending_conditions(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        current = self.live.read_bytes()
        cid = next(iter(self.pending()))
        # Drop retention, keeping valid negative evidence: an ordinary environment failure.
        changed = self.overlay()
        next(u for u in changed["unproven"] if u["config_id"] == cid).pop("pending_recheck")
        self.live.write_text(json.dumps(changed))
        self.assertEqual(self.run_case("--check"), 10, self.lines)
        self.assertEqual(self.run_case(interactive=False), 20, self.lines)
        self.live.write_bytes(current)
        changed = self.overlay()
        mapping = next(m for m in changed["mappings"] if m["identity"]["vendor"] == "gemini")
        mapping["pending_recheck"] = {"reason": "regressed", "consecutive_runs": 1}
        self.live.write_text(json.dumps(changed))
        self.assertEqual(self.run_case("--check"), 10, self.lines)
        self.assertEqual(self.run_case(interactive=False), 20, self.lines)
        self.live.write_bytes(current)
        original = resign.detect
        for condition in ("expired", "unprobed"):
            def detect(overlay, anchors):
                report = original(overlay, anchors)
                report["gemini"][condition] = [cid]
                return report
            with self.subTest(condition=condition), patch.object(resign, "detect", side_effect=detect):
                self.assertEqual(self.run_case("--check"), 10, self.lines)

    def test_daily_partial_message_reports_counts_not_total_failure(self):
        self.update("gemini")
        self.assertEqual(self.run_case("--daily"), 60, self.lines)
        text = "\n".join(self.lines)
        self.assertNotIn("automatic verification did not complete", text)
        self.assertIn("11 verified", text)
        self.assertIn("6", text)
        self.assertIn("omnilane resign", text)

    def test_check_counts_cannot_prove_failures_are_retained(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        original = resign.detect
        for field in ("failed_environment", "pending_selectors"):
            for value in (1, True):
                def detect(overlay, anchors):
                    report = original(overlay, anchors)
                    report["gemini"][field] = value
                    return report
                with self.subTest(field=field, value=value), patch.object(resign, "detect", side_effect=detect):
                    self.assertEqual(self.run_case("--check"), 10, self.lines)

    def test_daily_partial_does_not_mark_healthy_vendors_or_recovered_rows_pending(self):
        self.update("gemini")
        self.assertEqual(self.run_case("--daily", all_vendors=True), 60, self.lines)
        self.assertEqual(set(verification_status.load()["pending"]), {"gemini"})
        cid = next(iter(self.failures))
        self.failures = {cid: "quota"}
        self.assertEqual(self.run_case("--daily", all_vendors=True,
                                       now=datetime.now(timezone.utc) + timedelta(hours=38)), 60, self.lines)
        state = verification_status.load()["pending"]
        self.assertEqual(set(state), {"gemini"})
        self.assertEqual(state["gemini"]["config_ids"], [cid])
        self.assertEqual(state["gemini"]["pending_config_ids"], [cid])
        self.assertEqual(state["gemini"]["pending_count"], 1)

    def test_check_and_real_run_keep_expired_and_unprobed_conditions_distinct(self):
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        baseline = self.live.read_bytes()
        entry = next(e for e in self.entries if e["config_id"] not in self.failures)
        cid = entry["config_id"]
        self.failures[cid] = "quota"
        for condition in ("expired", "unprobed"):
            with self.subTest(condition=condition):
                self.live.write_bytes(baseline)
                overlay = self.overlay()
                if condition == "expired":
                    next(m for m in overlay["mappings"] if m["config_id"] == cid)["expires_at"] = "2000-01-01T00:00:00+00:00"
                else:
                    root = resign.previous_root(overlay)
                    path = root / "evidence" / (entry["name"] + ".json")
                    path.rename(path.with_suffix(".not-probed"))
                    prior = self.pending()
                    self.build(root)
                    overlay = json.loads((root / "transport-contracts.local.json").read_text())
                    for item in overlay["unproven"]:
                        if item["config_id"] in prior:
                            item["pending_recheck"] = prior[item["config_id"]]["pending_recheck"]
                self.live.write_text(json.dumps(overlay))
                self.calls.clear()
                self.assertEqual(self.run_case("--check"), 10, self.lines)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.run_case(interactive=False), 20, self.lines)
                self.assertEqual(self.calls, [cid])
                self.assertNotIn(cid, self.pending())

    def test_refusal_reason_redacts_sensitive_fields_and_preserves_reset(self):
        for reason in probe_failure.CONCLUSIVE_REASONS:
            self.assertEqual(probe_failure.public_reason(reason), reason)
        raw = ("quota-exhausted: person@example.com account_id=account-secret organization id: tenant-secret "
               "Bearer abcdefghijklmnopqrstuvwxyz123456 token=secret-token-value\n"
               "detail " * 35 + "Resets in 37h16m12s")
        safe = probe_failure.public_reason(raw)
        self.assertLessEqual(len(safe), 160)
        self.assertEqual(safe, " ".join(safe.split()))
        self.assertIn("Resets in 37h16m12s", safe)
        for secret in ("person@example.com", "account-secret", "tenant-secret", "abcdefghijklmnopqrstuvwxyz123456", "secret-token-value"):
            self.assertNotIn(secret, safe)
        for text, secret in [('"account_id": "acc123"', "acc123"), ('"token": "short-secret"', "short-secret"),
                             ("organisation id organisation123", "organisation123"), ("account: abc123", "abc123"),
                             ("api_key=key123", "key123"), ("person@localhost", "person@localhost")]:
            with self.subTest(reason=text):
                self.assertNotIn(secret, probe_failure.public_reason(text))
        self.update("gemini")
        self.assertEqual(self.run_case(), 60, self.lines)
        changed = self.overlay()
        for row in changed["unproven"]:
            if row.get("pending_recheck"):
                row["verdict_reason"] = raw
        self.live.write_text(json.dumps(changed))
        entry = next(e for e in self.entries if e["config_id"] in self.failures)
        result = self.decision(entry)
        self.assertEqual(result["pending_recheck"][entry["config_id"]], safe)
        self.assertNotIn("person@example.com", json.dumps(result))
        from overlay_diagnostics import unproven_rows
        diagnostic = next(row for row in unproven_rows(changed, build_overlay.REGISTRY)
                          if row["config_id"] == entry["config_id"])
        self.assertEqual(diagnostic["reason"], safe)
        self.assertEqual(next(u for u in self.overlay()["unproven"] if u["config_id"] == entry["config_id"])["verdict_reason"], raw)


class SelectorSummaryShapeTests(StatusHome):
    def test_sweep_counts_fail_closed_without_row_evidence(self):
        for key in ("passed", "failed"):
            for value in (1, True):
                with self.subTest(key=key, value=value):
                    result = {"passed": [], "failed": [], key: value}
                    with self.assertRaisesRegex(ValueError, "requires config IDs"):
                        selector_recheck.regressions(self.home, {}, "codex", result)

    def test_scalar_conditions_preserve_vendor_without_inventing_rows(self):
        for key in ("still_failed_environment", "deferred_retries", "failed_retries",
                    "still_unprobed", "deterministic_failures", "selector_pending",
                    "partially_re_signed", "pending_recheck"):
            for value in (1, True, 2.5, "legacy condition"):
                with self.subTest(key=key, value=value):
                    verification_status.record(20, ["codex"], {"held": {"grok": True}, key: {"codex": value}})
                    entry = self.pending()["codex"]
                    self.assertEqual(entry["config_ids"], [])
                    self.assertIn("codex", verification_status.pending_summary())
                    verification_status.record(0, ["codex"])

    def test_container_conditions_keep_only_registry_rows(self):
        cid = next(cid for cid in build_overlay.ROWS if cid.startswith("codex/"))
        for key in ("still_failed_environment", "deferred_retries", "failed_retries",
                    "still_unprobed", "deterministic_failures", "selector_pending",
                    "partially_re_signed", "pending_recheck"):
            for value in ([cid, "unknown"], (cid, "unknown"), {cid, "unknown"},
                          {cid: "model-mismatch", "unknown": "private"},
                          UserDict({cid: "model-mismatch"})):
                with self.subTest(key=key, value=value):
                    verification_status.record(20, ["codex"], {"held": {"grok": True}, key: {"codex": value}})
                    self.assertEqual(self.pending()["codex"]["config_ids"], [cid])
                    if key == "deterministic_failures" and isinstance(value, (dict, UserDict)):
                        self.assertEqual(self.pending()["codex"][key], {cid: "model-mismatch"})
                    verification_status.record(0, ["codex"])

    def test_nested_pending_and_report_counts_are_not_iterated(self):
        for key in ("selector_pending", "partially_re_signed"):
            verification_status.record(60, ["codex"], {key: {"codex": {
                "config_ids": 2, "pending_count": 2, "verified_count": 4}}})
            entry = self.pending()["codex"]
            self.assertEqual(entry["config_ids"], [])
            self.assertEqual((entry["pending_count"], entry["verified_count"]), (2, 4))
        verification_status.record(10, ["codex"], {"vendors": {"codex": {
            "unprobed": 1, "failed_environment": True, "pending_selectors": 2}}})
        self.assertEqual(self.pending()["codex"]["config_ids"], [])

    def test_partial_summary_counts_accept_legacy_shapes(self):
        cid = next(cid for cid in build_overlay.ROWS if cid.startswith("codex/"))
        for value, count in ((2, "2"), (True, "unknown"), ([cid], "1"),
                             ({cid: "quota-exhausted"}, "1"),
                             ({"verified_count": 4, "pending_count": 2, "config_ids": 2}, "2")):
            with self.subTest(value=value):
                lines = []
                code = verification_status.run_daily(lambda: 60, ["codex"],
                    {"partially_re_signed": {"codex": value}}, log=lines.append)
                self.assertEqual(code, 60)
                self.assertIn(count + " pending", "\n".join(lines))
                self.assertIn("codex", self.pending())

    def test_partial_other_vendor_does_not_clear_scalar_conditions(self):
        for key in ("still_failed_environment", "deferred_retries", "failed_retries",
                    "still_unprobed", "deterministic_failures", "pending_recheck"):
            with self.subTest(key=key):
                verification_status.record(60, summary={"partially_re_signed": {"gemini": 1},
                    "vendors": {"codex": {"drifted": False}}, key: {"codex": 1}})
                self.assertIn("codex", self.pending())


class SelectorClassificationTests(SelectorFixture, unittest.TestCase):
    def test_wrong_model_record_beats_stderr_noise_for_all_three_vendors(self):
        for vendor in ("gemini", "grok", "codex"):
            for exit_code in (0, 1):
                with self.subTest(vendor=vendor, exit_code=exit_code):
                    entry = probe_sweep.plan(vendor)[0]
                    path = self.sweep_root / "evidence" / (entry["name"] + ".json")
                    self.descriptor(self.sweep_root, entry["name"], "pass")
                    descriptor = json.loads(path.read_text())
                    descriptor["exit_code"] = exit_code
                    stderr = "warning: MCP server unauthorized 401; update check timed out"
                    stdout = json.dumps({"result": probe_sweep.TOKENS[vendor], "modelUsage": {"wrong-model": {}}})
                    suffix = "cli_log" if vendor == "gemini" else "rollout"
                    extra = {suffix: "model\twrong-model\n"} if vendor != "grok" else {}
                    verdict = probe.verdict(descriptor, stdout, stderr, "agy" if vendor == "gemini" else vendor,
                                            probe_sweep.TOKENS[vendor], extra)
                    descriptor.update(zip(("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict))
                    path.write_text(json.dumps(descriptor))
                    path.with_suffix(".stdout").write_text(stdout)
                    path.with_suffix(".stderr").write_text(stderr)
                    for key, value in extra.items():
                        path.with_suffix("." + key).write_text(value)
                    outcome = dict(passed=[], failed=[entry["config_id"]])
                    environment, conclusive = selector_recheck.regressions(self.sweep_root, self.overlay(), vendor, outcome)
                    self.assertEqual(environment, {})
                    self.assertIn(entry["config_id"], conclusive)
                    self.assertIn("mismatch", conclusive[entry["config_id"]])

    def test_codex_mcp_401_cannot_mask_account_mismatch_or_ambiguous_exit(self):
        entry = probe_sweep.plan("codex")[0]
        for event in ("model is not supported with this account", None):
            with self.subTest(event=event):
                path = self.sweep_root / "evidence" / (entry["name"] + ".json")
                self.descriptor(self.sweep_root, entry["name"], "pass")
                descriptor = json.loads(path.read_text())
                descriptor["exit_code"] = 1
                stdout = json.dumps({"type": "turn.failed", "error": {"message": event}}) if event else ""
                stderr = "ERROR MCP server failed with 401 Unauthorized"
                path.with_suffix(".rollout").write_text("")
                verdict = probe.verdict(descriptor, stdout, stderr, "codex", probe_sweep.TOKENS["codex"])
                descriptor.update(zip(("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict))
                path.write_text(json.dumps(descriptor))
                path.with_suffix(".stdout").write_text(stdout)
                path.with_suffix(".stderr").write_text(stderr)
                environment, conclusive = selector_recheck.regressions(self.sweep_root, self.overlay(), "codex",
                                                                       dict(passed=[], failed=[entry["config_id"]]))
                self.assertEqual(environment, {})
                self.assertIn(entry["config_id"], conclusive)


class SelectorLoginTests(retry_fixture.RetryFixture, unittest.TestCase):
    def test_timeout_is_not_evidence_that_a_selector_authenticated(self):
        entries = probe_sweep.plan("claude")
        self.update("claude")
        self.behavior = {entries[0]["config_id"]: "timeout", entries[1]["config_id"]: "plain-login"}
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [entry["config_id"] for entry in entries[:2]])
        self.assertEqual(self.args.status_summary["re_signed"], [])
        self.assertEqual(self.args.status_summary["sweeps"]["claude"]["outcome"], "unprobeable")

    def test_login_stop_does_not_increment_unattempted_pending_rows(self):
        entries = probe_sweep.plan("claude")[-2:]
        self.update("claude")
        self.behavior = {entry["config_id"]: "quota" for entry in entries}
        self.assertEqual(self.run_retry(), 60, self.lines)
        self.behavior = {entry["config_id"]: "plain-login" for entry in entries}
        self.calls.clear()
        self.assertEqual(self.run_retry(), 60, self.lines)
        self.assertEqual(self.calls, [entries[0]["config_id"]])
        self.assertEqual(self.failure(entries[0])["pending_recheck"]["consecutive_runs"], 2)
        self.assertEqual(self.failure(entries[1])["pending_recheck"]["consecutive_runs"], 1)
        self.calls.clear()
        self.assertEqual(self.run_retry(), 60, self.lines)
        self.assertEqual(self.calls, [entries[1]["config_id"]])

    def test_first_login_failure_with_transient_noise_still_calls_once(self):
        entries = probe_sweep.plan("claude")
        self.update("claude")
        self.behavior = {entry["config_id"]: "plain-login" for entry in entries}
        def noisy(root, entry, record):
            (root / "evidence" / (entry["name"] + ".stderr")).write_text("not logged in; warning: update check timed out")
        self.hook = noisy
        self.assertEqual(self.run_retry(interactive=True), 20, self.lines)
        self.assertEqual(self.calls, [entries[0]["config_id"]])
        self.assertEqual(self.args.status_summary["sweeps"]["claude"]["outcome"], "unprobeable")
        self.smoke.assert_not_called()

    def test_complete_environment_failure_uses_pending_records_then_recovers(self):
        entries = probe_sweep.plan("claude")
        self.update("claude")
        self.behavior = {entry["config_id"]: "plain-login" for entry in entries}
        old = self.overlay()
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [entries[0]["config_id"]])
        self.assertEqual(self.overlay()["evidence"], old["evidence"])
        self.assertEqual(self.args.status_summary["re_signed"], [])
        self.assertEqual(resign.verified(self.live)["claude"], len(entries))  # Old pin only.
        self.smoke.assert_not_called()
        self.assertFalse(selector_recheck.pending_rows(self.overlay(), "claude"))
        verification_status.record(20, ["claude"], self.args.status_summary)
        self.assertEqual(verification_status.load()["pending"]["claude"]["reason"], "needs-login-session")
        self.assertIn("log in", "\n".join(self.lines))
        self.behavior.clear()
        self.calls.clear()
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(len(self.calls), len(entries))
        self.assertEqual(resign.verified(self.live)["claude"], len(entries))

    def test_model_login_failure_does_not_abort_other_selectors(self):
        entry = probe_sweep.plan("claude")[-1]
        self.update("claude")
        self.behavior[entry["config_id"]] = "plain-login"
        self.assertEqual(self.run_retry(), 60, self.lines)
        self.assertEqual(len(self.calls), len(probe_sweep.plan("claude")))
        self.assertTrue(self.failure(entry)["pending_recheck"])
        self.assert_unverified(entry)
        for other in probe_sweep.plan("claude")[:-1]:
            self.assertTrue(self.decision(other)["allowed"])


if __name__ == "__main__":
    unittest.main()
