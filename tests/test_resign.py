#!/usr/bin/env python3
"""Offline tests for the signer check, the probe plan and the re-sign flow.

Nothing here runs a vendor CLI: codesign, launchctl, the probe and the smoke
dispatch are all replaced, and every file lives under .sandbox-tmp.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import build_overlay  # noqa: E402
import cli_provenance  # noqa: E402
import probe_sweep  # noqa: E402
import resign  # noqa: E402

SCRATCH = ROOT / ".sandbox-tmp"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def codesign(described: str, verify_rc: int = 0):
    def runner(argv, **_):
        if "--verify" in argv:
            return SimpleNamespace(returncode=verify_rc, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr=described)
    return runner


class ProvenanceTests(unittest.TestCase):
    TEAM = "Identifier=codex\nAuthority=Developer ID Application: X (TEAM123456)\nTeamIdentifier=TEAM123456\n"
    ADHOC = "Identifier=2.1.276.patchtmp\nSignature=adhoc\nTeamIdentifier=not set\n"

    def test_reads_the_signing_team(self):
        self.assertEqual(cli_provenance.facts("/x", codesign(self.TEAM)),
                         {"signer": "TEAM123456", "identifier": "codex", "valid": True})

    def test_adhoc_and_invalid_signatures_are_not_a_team(self):
        self.assertEqual(cli_provenance.facts("/x", codesign(self.ADHOC))["signer"], "adhoc")
        self.assertEqual(cli_provenance.facts("/x", codesign(self.TEAM, 1))["signer"], "unsigned")

    def test_a_trusted_adhoc_update_passes_only_in_its_install_location(self):
        adhoc = {"signer": "adhoc", "identifier": "x", "valid": True}
        trusted = {**adhoc, "operator_trust": cli_provenance.TRUST_ADHOC}
        old = "/u/.local/share/claude/versions/2.1.276"
        self.assertTrue(cli_provenance.verdict(trusted, old, adhoc, "/u/.local/share/claude/versions/2.1.278")[0])
        allowed, reason = cli_provenance.verdict(trusted, old, adhoc, "/tmp/elsewhere/2.1.278")
        self.assertFalse(allowed); self.assertIn("outside the location", reason)
        allowed, reason = cli_provenance.verdict(adhoc, old, adhoc, "/u/.local/share/claude/versions/2.1.278")
        self.assertFalse(allowed); self.assertIn("--trust-adhoc", reason)
        unsigned = {"signer": "unsigned", "identifier": None, "valid": False}
        self.assertFalse(cli_provenance.verdict(trusted, old, unsigned, "/u/.local/share/claude/versions/2.1.278")[0])

    def test_a_version_bump_stays_in_the_family_and_a_new_directory_does_not(self):
        old = "/Users/u/.grok/downloads/grok-1.0.25-macos-aarch64"
        self.assertEqual(cli_provenance.family(old),
                         cli_provenance.family("/Users/u/.grok/downloads/grok-1.0.30-macos-aarch64"))
        self.assertNotEqual(cli_provenance.family(old),
                            cli_provenance.family("/tmp/grok-1.0.30-macos-aarch64"))

    def test_the_verdict(self):
        team = {"signer": "TEAM123456"}
        path = "/opt/cli/1.2.3/bin/cli"
        cases = [
            (team, path, team, "/opt/cli/1.3.0/bin/cli", True),
            (team, path, {"signer": "OTHERTEAM1"}, "/opt/cli/1.3.0/bin/cli", False),
            (team, path, team, "/tmp/cli/1.3.0/bin/cli", False),
            (team, path, {"signer": "adhoc"}, path, False),
            ({"signer": "adhoc"}, path, {"signer": "adhoc"}, path, False),
            (None, path, team, path, False),
        ]
        for recorded, recorded_path, current, current_path, expected in cases:
            with self.subTest(current=current, path=current_path, recorded=recorded):
                allowed, reason = cli_provenance.verdict(recorded, recorded_path, current, current_path)
                self.assertIs(allowed, expected, reason)
                self.assertTrue(reason)


class ProbePlanTests(unittest.TestCase):
    def test_every_proven_row_has_exactly_one_planned_probe(self):
        planned = {entry["name"] for vendor in probe_sweep.VENDORS for entry in probe_sweep.plan(vendor)}
        self.assertEqual(planned, {evidence for _, _, evidence in build_overlay.PROVEN.values()})

    def test_commands_carry_the_selector_under_test(self):
        self.assertIn('model_reasoning_effort="none"',
                      probe_sweep.command("codex", "gpt-5.6-sol", None))
        self.assertNotIn("--effort", probe_sweep.command("claude", "claude-haiku-4-5", None))
        self.assertEqual(probe_sweep.command("gemini", "gemini-3.8-flash-low", None, "../app")[1],
                         "--app_data_dir=../app")
        grok = probe_sweep.command("grok", "grok-4.6", "xhigh")
        self.assertEqual(grok[grok.index("--reasoning-effort") + 1], "xhigh")


class SweepTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "sweep"

    def prober(self, verdicts):
        calls = []

        def run(name, argv, *, root, vendor, expected_token, app_root=None):
            calls.append(name)
            evidence = root / "evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            verdict, reason, err = verdicts(name, calls.count(name))
            (evidence / f"{name}.stderr").write_text(err)
            (evidence / f"{name}.stdout").write_text("")
            record = {"verdict": verdict, "verdict_reason": reason, "evidence_tier": "billed-model",
                      "stdout": str(evidence / f"{name}.stdout"),
                      "stderr": str(evidence / f"{name}.stderr")}
            (evidence / f"{name}.json").write_text(json.dumps(record))
            return record
        return run, calls

    def test_a_background_session_is_unprobeable_for_keychain_vendors(self):
        run, calls = self.prober(lambda *_: ("pass", "", ""))
        with patch.object(sys, "platform", "darwin"):
            report = probe_sweep.sweep("grok", self.root, run_probe=run,
                                       manager=lambda: "Background", log=lambda _: None)
        self.assertEqual(report["outcome"], "unprobeable")
        self.assertIn("keychain", report["detail"])
        self.assertEqual(calls, [])

    def test_codex_is_probed_from_any_session(self):
        run, calls = self.prober(lambda *_: ("pass", "", ""))
        report = probe_sweep.sweep("codex", self.root, run_probe=run,
                                   manager=lambda: "Background", log=lambda _: None)
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(len(calls), len(probe_sweep.plan("codex")))

    def test_an_expired_login_is_not_logged_in(self):
        # claude 2.1.278 after an update: every probe fails with this, and waiting will not fix it.
        for text in ("Failed to authenticate: OAuth session expired and could not be refreshed",
                     "Error: Invalid API key", "HTTP 401 Unauthorized"):
            with self.subTest(text=text):
                run, calls = self.prober(lambda *_, t=text: ("fail", "result-error: " + t, ""))
                report = probe_sweep.sweep("claude", self.root, run_probe=run,
                                           manager=lambda: "Aqua", log=lambda _: None)
                self.assertEqual(report["outcome"], "unprobeable")
                self.assertIn("log in", report["detail"])
                self.assertEqual(len(calls), 1)

    def test_not_logged_in_aborts_and_leaves_no_descriptor(self):
        run, calls = self.prober(lambda *_: ("fail", "exit-code: 1", "Error: Not signed in.\n"))
        report = probe_sweep.sweep("grok", self.root, run_probe=run,
                                   manager=lambda: "Aqua", log=lambda _: None)
        self.assertEqual(report["outcome"], "unprobeable")
        self.assertEqual(len(calls), 1)
        self.assertEqual(list((self.root / "evidence").glob("gk-*.json")), [])

    def test_a_transient_failure_is_retried_once(self):
        def verdicts(name, attempt):
            if name == "gk-grok-4_6-high" and attempt == 1:
                return "fail", "API error (status 403 Forbidden): permission-denied", ""
            return "pass", "", ""
        run, calls = self.prober(verdicts)
        report = probe_sweep.sweep("grok", self.root, run_probe=run,
                                   manager=lambda: "Aqua", log=lambda _: None)
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(report["failed"], [])
        self.assertEqual(calls.count("gk-grok-4_6-high"), 2)

    def test_a_real_selector_failure_is_recorded_not_retried(self):
        def verdicts(name, _attempt):
            if "5_4-mini" in name:
                return "fail", "upstream 400: model is not supported", ""
            return "pass", "", ""
        run, calls = self.prober(verdicts)
        report = probe_sweep.sweep("codex", self.root, run_probe=run,
                                   manager=lambda: "Aqua", log=lambda _: None)
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(len(report["failed"]), 3)
        self.assertEqual(calls.count("cx-gpt-5_4-mini-none"), 1)
        self.assertTrue((self.root / "evidence/cx-gpt-5_4-mini-none.json").exists())


class ResignTests(unittest.TestCase):
    """A host with four fake CLIs, a signed overlay, and then an update."""

    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.bins = {}
        for vendor in resign.VENDORS:
            path = self.base / "opt" / vendor / "1.0.0" / build_overlay.CLI_NAMES[vendor]
            path.parent.mkdir(parents=True)
            path.write_text(f"{vendor} 1.0.0\n")
            self.bins[vendor] = path
        self.signers = {vendor: f"TEAM{index}" for index, vendor in enumerate(resign.VENDORS)}
        self.sweep_root = self.home / "transport-evidence" / "first"
        (self.sweep_root / "evidence").mkdir(parents=True)
        for vendor in resign.VENDORS:
            for entry in probe_sweep.plan(vendor):
                self.descriptor(self.sweep_root, entry["name"], "pass")
        self.live = self.home / "transport-contracts.local.json"
        env = patch.dict(os.environ, {"OMNILANE_HOME": str(self.home),
                                      "OMNILANE_AA_TRANSPORT_OVERLAY": str(self.live)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OMNILANE_AA_OVERLAY_SHA256", None)
        for target, replacement in (
                (build_overlay, {"cli_path": lambda name: self.bins[self.vendor_of(name)]}),
                (cli_provenance, {"facts": self.facts})):
            for attribute, value in replacement.items():
                patcher = patch.object(target, attribute, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        which = patch.object(resign.shutil, "which",
                             lambda name: str(self.bins[self.vendor_of(name)]))
        which.start()
        self.addCleanup(which.stop)
        self.sweeps = []
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())

    def vendor_of(self, cli_name):
        return next(v for v, name in build_overlay.CLI_NAMES.items() if name == cli_name)

    def facts(self, path, runner=None):
        vendor = self.vendor_of(Path(path).name)
        return {"signer": self.signers[vendor], "identifier": vendor, "valid": True}

    def descriptor(self, root, name, verdict):
        evidence = root / "evidence"
        for suffix in ("stdout", "stderr"):
            (evidence / f"{name}.{suffix}").write_text("")
        (evidence / f"{name}.json").write_text(json.dumps(
            {"verdict": verdict, "verdict_reason": "", "evidence_tier": "billed-model",
             "probed_at": "2026-01-01T00:00:00+00:00"}))

    def build(self, root):
        with patch("builtins.print"):
            build_overlay.main(["--root", str(root), "--source", "test"])

    def update(self, vendor, version="1.1.0", directory=None):
        path = (directory or self.base / "opt" / vendor / version) / build_overlay.CLI_NAMES[vendor]
        path.parent.mkdir(parents=True)
        path.write_text(f"{vendor} {version}\n")
        self.bins[vendor] = path
        return path

    def run_resign(self, *, outcome="done", smoke=(True, "ok"), failing=(), **flags):
        def sweep(vendor, root, log=print, **_):
            self.sweeps.append(vendor)
            entries = probe_sweep.plan(vendor)
            if outcome == "done":
                for entry in entries:
                    self.descriptor(root, entry["name"],
                                    "fail" if entry["config_id"] in failing else "pass")
            return {"vendor": vendor, "outcome": outcome, "detail": "stub",
                    "passed": [e["config_id"] for e in entries if e["config_id"] not in failing],
                    "failed": [e["config_id"] for e in entries if e["config_id"] in failing]}
        args = Namespace(check=False, vendor=None, approve=None, trust_adhoc=None, no_smoke=False, json=False,
                         record_signers=False, allow_shrink=False)
        for key, value in flags.items():
            setattr(args, key, value)
        self.lines = []
        with patch.object(probe_sweep, "sweep", sweep), \
                patch.object(resign, "canary", lambda cli: (True, "v")), \
                patch.object(resign, "smoke", lambda *a, **k: smoke), \
                patch("builtins.print"):
            return resign.resign(args, log=self.lines.append)

    def pinned(self, vendor):
        overlay = json.loads(self.live.read_text())
        return next(e["path"] for e in overlay["evidence"] if e.get("vendor") == vendor
                    and Path(e["path"]).name == build_overlay.CLI_NAMES[vendor])

    def test_the_overlay_records_each_signer(self):
        overlay = json.loads(self.live.read_text())
        self.assertEqual(overlay["host"], socket.gethostname())
        recorded = {e["vendor"]: e["codesign"]["signer"] for e in overlay["evidence"] if "codesign" in e}
        self.assertEqual(recorded, self.signers)

    def test_record_signers_adopts_only_unchanged_executables(self):
        overlay = json.loads(self.live.read_text())
        for entry in overlay["evidence"]:
            entry.pop("codesign", None)
        self.live.write_text(json.dumps(overlay))
        self.update("grok")
        self.assertEqual(self.run_resign(record_signers=True), resign.EXIT_OK, self.lines)
        recorded = {e["vendor"] for e in json.loads(self.live.read_text())["evidence"] if "codesign" in e}
        self.assertEqual(recorded, {"claude", "codex", "gemini"})
        self.assertEqual(self.sweeps, [])

    def test_nothing_drifted_is_a_no_op(self):
        before = digest(self.live)
        self.assertEqual(self.run_resign(), resign.EXIT_OK)
        self.assertEqual(self.sweeps, [])
        self.assertEqual(digest(self.live), before)

    def test_check_reports_drift_and_changes_nothing(self):
        self.update("grok")
        before = digest(self.live)
        self.assertEqual(self.run_resign(check=True), resign.EXIT_DRIFT)
        self.assertEqual((self.sweeps, digest(self.live)), ([], before))
        self.assertTrue(any("grok drifted: runs" in line for line in self.lines), self.lines)

    def test_a_same_signer_update_is_re_signed_and_only_that_vendor_is_probed(self):
        new = self.update("grok")
        self.assertEqual(self.run_resign(), resign.EXIT_OK, self.lines)
        self.assertEqual(self.sweeps, ["grok"])
        self.assertEqual(self.pinned("grok"), str(new))
        self.assertEqual(len(list(self.home.glob("transport-contracts.local.json.before-*"))), 1)

    def test_a_new_signer_is_held_for_the_operator(self):
        old = self.pinned("codex")
        self.update("codex")
        self.signers["codex"] = "SOMEONEELSE"
        before = digest(self.live)
        self.assertEqual(self.run_resign(), resign.EXIT_OPERATOR)
        self.assertEqual((self.sweeps, digest(self.live), self.pinned("codex")), ([], before, old))
        self.assertTrue(any("--approve codex" in line for line in self.lines), self.lines)

    def test_an_adhoc_binary_is_held_until_approved(self):
        new = self.update("claude")
        self.signers["claude"] = "adhoc"
        self.assertEqual(self.run_resign(), resign.EXIT_OPERATOR)
        self.assertEqual(self.sweeps, [])
        self.assertEqual(self.run_resign(approve=["claude"]), resign.EXIT_OK, self.lines)
        self.assertEqual((self.sweeps, self.pinned("claude")), (["claude"], str(new)))

    def test_trust_adhoc_lets_a_local_patch_step_update_unattended(self):
        self.assertEqual(self.run_resign(trust_adhoc=["claude"]), resign.EXIT_OK, self.lines)
        entry = next(e for e in json.loads(self.live.read_text())["evidence"]
                     if e.get("vendor") == "claude" and "codesign" in e)
        self.assertEqual(entry["operator_trust"], cli_provenance.TRUST_ADHOC)
        self.assertEqual(len(list(self.home.glob("transport-contracts.local.json.before-trust-adhoc-*"))), 1)
        new = self.update("claude")
        self.signers["claude"] = "adhoc"
        self.assertEqual(self.run_resign(), resign.EXIT_OK, self.lines)
        self.assertEqual((self.sweeps, self.pinned("claude")), (["claude"], str(new)))
        # The waiver outlives the re-sign, so the next adhoc update is unattended too.
        entry = next(e for e in json.loads(self.live.read_text())["evidence"]
                     if e.get("vendor") == "claude" and "codesign" in e)
        self.assertEqual(entry["operator_trust"], cli_provenance.TRUST_ADHOC)
        self.sweeps.clear()
        self.update("claude", version="1.2.0")
        self.assertEqual(self.run_resign(), resign.EXIT_OK, self.lines)
        self.assertEqual(self.sweeps, ["claude"])

    def test_trust_adhoc_does_not_cover_a_new_directory_or_an_unsigned_binary(self):
        self.run_resign(trust_adhoc=["claude"])
        self.update("claude", directory=self.base / "tmp" / "elsewhere" / "1.1.0")
        self.signers["claude"] = "adhoc"
        self.assertEqual(self.run_resign(), resign.EXIT_OPERATOR)
        self.assertEqual(self.sweeps, [])
        self.update("claude", version="1.2.0")
        self.signers["claude"] = "unsigned"
        self.assertEqual(self.run_resign(), resign.EXIT_OPERATOR)
        self.assertEqual(self.sweeps, [])

    def test_trust_on_an_untouched_vendor_survives_another_vendors_re_sign(self):
        self.run_resign(trust_adhoc=["claude"])
        self.update("codex")
        self.assertEqual(self.run_resign(), resign.EXIT_OK, self.lines)
        self.assertEqual(self.sweeps, ["codex"])
        entry = next(e for e in json.loads(self.live.read_text())["evidence"]
                     if e.get("vendor") == "claude" and "codesign" in e)
        self.assertEqual(entry["operator_trust"], cli_provenance.TRUST_ADHOC)
        self.sweeps.clear()
        self.update("claude")
        self.signers["claude"] = "adhoc"
        self.assertEqual(self.run_resign(), resign.EXIT_OK, self.lines)
        self.assertEqual(self.sweeps, ["claude"])

    def test_trust_adhoc_needs_a_recorded_signer(self):
        overlay = json.loads(self.live.read_text())
        for entry in overlay["evidence"]:
            entry.pop("codesign", None)
        self.live.write_text(json.dumps(overlay))
        before = digest(self.live)
        self.assertEqual(self.run_resign(trust_adhoc=["claude"]), resign.EXIT_OK)
        self.assertEqual(digest(self.live), before)
        self.assertTrue(any("--record-signers first" in l for l in self.lines), self.lines)

    def test_trust_adhoc_is_per_vendor_and_repeatable(self):
        self.assertEqual(self.run_resign(trust_adhoc=["grok", "codex"]), resign.EXIT_OK, self.lines)
        trusted = {e["vendor"] for e in json.loads(self.live.read_text())["evidence"] if e.get("operator_trust")}
        self.assertEqual(trusted, {"grok", "codex"})
        self.assertEqual(self.run_resign(trust_adhoc=["grok"]), resign.EXIT_OK)
        self.assertTrue(any("nothing to record" in l for l in self.lines), self.lines)

    def test_an_install_outside_the_recorded_location_is_held(self):
        self.update("grok", directory=self.base / "tmp" / "elsewhere" / "1.1.0")
        self.assertEqual(self.run_resign(), resign.EXIT_OPERATOR)
        self.assertEqual(self.sweeps, [])

    def test_a_held_vendor_keeps_its_old_pin_while_another_is_re_signed(self):
        old_codex = self.pinned("codex")
        self.update("codex")
        self.signers["codex"] = "SOMEONEELSE"
        new_grok = self.update("grok")
        self.assertEqual(self.run_resign(), resign.EXIT_OPERATOR)
        self.assertEqual(self.sweeps, ["grok"])
        self.assertEqual((self.pinned("grok"), self.pinned("codex")), (str(new_grok), old_codex))

    def test_a_provider_bad_hour_does_not_shrink_the_overlay(self):
        old = self.pinned("grok")
        self.update("grok")
        before = digest(self.live)
        self.assertEqual(self.run_resign(failing=("grok/grok-4-6",)), resign.EXIT_OPERATOR)
        self.assertEqual((digest(self.live), self.pinned("grok")), (before, old))
        self.assertTrue(any("--allow-shrink" in line for line in self.lines), self.lines)
        self.assertFalse(any("--approve grok" in line for line in self.lines), self.lines)

    def test_allow_shrink_installs_the_smaller_overlay(self):
        new = self.update("grok")
        self.assertEqual(self.run_resign(failing=("grok/grok-4-6",), allow_shrink=True),
                         resign.EXIT_OK, self.lines)
        overlay = json.loads(self.live.read_text())
        self.assertEqual(self.pinned("grok"), str(new))
        self.assertNotIn("grok/grok-4-6", {m["config_id"] for m in overlay["mappings"]})
        self.assertIn("grok/grok-4-6", {u["config_id"] for u in overlay["unproven"]})

    def test_an_unprobeable_vendor_is_not_installed(self):
        self.update("grok")
        before = digest(self.live)
        self.assertEqual(self.run_resign(outcome="unprobeable"), resign.EXIT_OPERATOR)
        self.assertEqual(digest(self.live), before)
        # Not "retry later": nothing changes until the CLI is logged in again.
        held = [l for l in self.lines if "grok was not re-signed" in l]
        self.assertEqual(len(held), 1, self.lines)
        self.assertNotIn("Retry later", held[0])
        self.assertIn("omnilane resign --vendor grok", held[0])

    def test_a_failed_smoke_restores_the_previous_overlay(self):
        self.update("grok")
        before = digest(self.live)
        self.assertEqual(self.run_resign(smoke=(False, "no token")), resign.EXIT_ROLLED_BACK)
        self.assertEqual(digest(self.live), before)
        self.assertTrue(any("restored" in line for line in self.lines), self.lines)

    def test_no_configured_overlay_is_not_an_error_to_retry(self):
        with patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": ""}):
            self.assertEqual(self.run_resign(), resign.EXIT_UNCONFIGURED)


if __name__ == "__main__":
    unittest.main()
