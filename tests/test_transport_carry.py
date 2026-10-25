"""Offline row-carry regression and false-allow cases; no installed providers."""
from __future__ import annotations

import offline_env  # noqa: F401
import home_isolation  # noqa: F401
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy
import build_overlay
import transport_compat as compat

TARGETS = {
    "codex": "codex/gpt-6-1-sol-high",
    "claude": "claude/claude-opus-5-5-high",
    "gemini": "gemini/gemini-3-8-flash",
    "grok": "grok/grok-4-7-high",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CarryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.registry = json.loads((ROOT / "config/aa-model-policy.json").read_text())
        self.source_id = self.registry["snapshot"]["id"]
        self.registry["snapshot"]["id"] = "synthetic-next-snapshot"
        self.rows = {r["id"]: r for r in self.registry["scored_configs"]}
        self.overlay = dict(schema_version=1, snapshot_id=self.source_id,
                            host=socket.gethostname(), evidence=[], mappings=[])
        manifest = {"probe_runs": {}}
        self.probes = {}
        for vendor, cid in TARGETS.items():
            row = self.rows[cid]
            selector, model, name = build_overlay.PROVEN[cid]
            path = self.root / (vendor + ".cli")
            path.write_text(vendor + " executable identity")
            self.overlay["evidence"].append(dict(path=str(path), sha256=digest(path), vendor=vendor))
            record = self.root / (name + ".json")
            from probe_fixture import write_probe
            files = write_probe(self.root, vendor, dict(name=name, model=model,
                                effort=None if vendor == "gemini" else row["effort"]))
            self.probes[vendor] = record
            manifest["probe_runs"][cid] = files
            mapping = dict(config_id=cid, identity=aa_policy._row_identity(row),
                           runtime_model=model, runtime_effort=row["effort"],
                           selector_type=selector, verification="request-selector-contract")
            if selector == "cli_reasoning_effort":
                mapping["cli_flag"] = "--reasoning-effort"
            self.overlay["mappings"].append(mapping)
        self.manifest = self.root / "probe-manifest.json"
        self.manifest.write_text(json.dumps(manifest))
        self.overlay["evidence"].append(dict(path=str(self.manifest), sha256=digest(self.manifest)))
        self.path = self.root / "overlay.json"
        env = dict(os.environ, OMNILANE_AA_TRANSPORT_OVERLAY=str(self.path))
        env.pop("OMNILANE_AA_OVERLAY_SHA256", None)
        env.pop("OMNILANE_AA_COMPATIBILITY_SHA256", None)
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def apply(self):
        self.path.write_text(json.dumps(self.overlay))
        result = copy.deepcopy(self.registry)
        aa_policy.apply_transport_overlay(result)
        return result

    def allowed(self, registry, vendor, ceiling=100):
        cid = TARGETS[vendor]
        row = next(r for r in registry["scored_configs"] if r["id"] == cid)
        caller = max(registry["scored_configs"], key=lambda r: r["score"])
        context = dict(caller=aa_policy._row_identity(caller), inherited_ceiling=ceiling)
        mapping = next(m for m in self.overlay["mappings"] if m["config_id"] == cid)
        return aa_policy.decide(registry, "fixture", vendor=vendor, model=mapping["runtime_model"],
                                effort=mapping["runtime_effort"], caller=context,
                                caller_sha256="fixture", target_config=row["id"])

    def test_score_and_date_only_change_carries_without_writing(self):
        for row in self.registry["scored_configs"]:
            row["as_of"] = "2026-10-10"
            row["benchmark_version"] = "next-benchmark"
            row["score"] = 30
            row["estimated"] = True
            row["transport_mapping"]["reason"] = "New score source; identical request parameters"
        result = self.apply()
        before = self.path.read_bytes()
        for vendor in TARGETS:
            self.assertTrue(self.allowed(result, vendor)["allowed"])
        self.assertEqual(before, self.path.read_bytes())
        audit = result["_transport_carry_forward"]
        self.assertEqual(len(audit), 4)
        self.assertEqual(audit[0]["source_snapshot_id"], self.source_id)
        self.assertEqual(audit[0]["target_snapshot_id"], self.registry["snapshot"]["id"])

    def test_deleted_row_does_not_block_remaining_rows(self):
        self.registry["scored_configs"] = [r for r in self.registry["scored_configs"]
                                           if r["id"] != TARGETS["grok"]]
        result = self.apply()
        self.assertTrue(self.allowed(result, "codex")["allowed"])

    def test_new_row_is_unverified_while_existing_row_works(self):
        row = copy.deepcopy(self.rows[TARGETS["codex"]])
        row.update(id="codex/new-model", model="new-model")
        row["transport_mapping"]["candidate_model_ids"] = ["new-model"]
        self.registry["scored_configs"].append(row)
        result = self.apply()
        self.assertTrue(self.allowed(result, "codex")["allowed"])
        target, code, _ = aa_policy._runtime_target(result, "codex", "new-model", row["effort"], row["id"])
        self.assertIsNone(target)
        self.assertEqual(code, "runtime-mapping-unverified")

    def test_unknown_source_is_not_trusted(self):
        self.overlay["snapshot_id"] = "not-shipped-by-this-release"
        with self.assertRaises(aa_policy.PolicyError):
            self.apply()

    def test_overlay_pin_still_rejects_changed_bytes(self):
        os.environ["OMNILANE_AA_OVERLAY_SHA256"] = "0" * 64
        with self.assertRaises(aa_policy.PolicyError):
            self.apply()

    def test_repeated_upgrade_and_downgrade_never_mutates_source_authority(self):
        for snapshot, score, expected in (("next-a", 30, True), ("next-b", 90, False),
                                           (self.source_id, 30, True), ("next-a", 90, False)):
            self.registry["snapshot"]["id"] = snapshot
            self.rows[TARGETS["codex"]]["score"] = score
            self.assertEqual(self.allowed(self.apply(), "codex", ceiling=50)["allowed"], expected)
            self.assertEqual(self.overlay["snapshot_id"], self.source_id)

    def test_same_identity_under_two_row_ids_is_rejected(self):
        row = copy.deepcopy(self.rows[TARGETS["codex"]])
        row["id"] = "codex/duplicate"
        self.registry["scored_configs"].append(row)
        with self.assertRaisesRegex(aa_policy.PolicyError, "duplicate AA configuration identity"):
            self.apply()

    def test_probe_contract_update_blocks_old_proofs(self):
        with patch.object(compat, "engine_contract", return_value={"probe.py": "new-parser"}):
            result = self.apply()
        self.assertFalse(self.allowed(result, "codex")["allowed"])

    def test_proven_alias_remap_blocks_identical_selector_strings(self):
        cid = TARGETS["codex"]
        with patch.dict(build_overlay.PROVEN, {cid: ("model_and_effort", "another-model", "probe")}):
            result = self.apply()
        self.assertFalse(self.allowed(result, "codex")["allowed"])

    def test_compatibility_pin_change_is_rejected(self):
        os.environ["OMNILANE_AA_COMPATIBILITY_SHA256"] = "0" * 64
        with self.assertRaisesRegex(aa_policy.PolicyError, "compatibility changed"):
            self.apply()

    def test_manifest_duplication_is_rejected(self):
        self.overlay["evidence"].append(dict(self.overlay["evidence"][-1]))
        with self.assertRaises(aa_policy.PolicyError):
            self.apply()

    def test_current_runtime_path_must_match_the_carried_executable(self):
        result = self.apply()
        result["_runtime_executable_request"] = str(self.root / "different-codex")
        decision = self.allowed(result, "codex")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "runtime-executable-changed")
        result["_runtime_executable_request"] = str(self.root / "codex.cli")
        self.assertTrue(self.allowed(result, "codex")["allowed"])

    def test_archive_contains_the_approved_release_and_is_packaged(self):
        value, _ = compat.archive()
        current = json.loads((ROOT / "config/aa-model-policy.json").read_text())
        archived = value["snapshots"][current["snapshot"]["id"]]
        self.assertEqual(archived["registry_sha256"], digest(ROOT / "config/aa-model-policy.json"))
        self.assertEqual(archived["rows"], [compat.row_contract(row) for row in current["scored_configs"]])
        self.assertEqual(archived["engine"], compat.engine_contract())
        self.assertIn("config/transport-compatibility.json", json.loads((ROOT / "package.json").read_text())["files"])

    def test_unknown_request_field_prevents_reuse(self):
        self.rows[TARGETS["codex"]]["new_selector_semantics"] = "changed"
        self.assertFalse(self.allowed(self.apply(), "codex")["allowed"])

    def test_bad_host_and_symlinked_source_are_rejected(self):
        self.overlay["host"] = "another-host"
        with self.assertRaises(aa_policy.PolicyError):
            self.apply()
        link = self.root / "linked-overlay.json"
        link.symlink_to(self.path)
        with patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": str(link)}):
            with self.assertRaises(OSError):
                aa_policy.apply_transport_overlay(copy.deepcopy(self.registry))

    def test_format_two_provenance_is_checked_not_trusted(self):
        records = compat.proof_records(self.overlay)
        for mapping in self.overlay["mappings"]:
            cid = mapping["config_id"]
            mapping.update(compat.provenance(self.rows[cid], self.source_id, *records[cid]))
        self.overlay.update(schema_version=2, compatibility_version=compat.VERSION)
        self.assertTrue(self.allowed(self.apply(), "codex")["allowed"])
        self.overlay["mappings"][0]["source_row_digest"] = "0" * 64
        with self.assertRaisesRegex(aa_policy.PolicyError, "source row digest"):
            self.apply()

    def test_resealed_failed_verdict_never_authorizes_carry(self):
        # Unlike byte tampering, a correctly re-hashed negative descriptor reaches
        # the verdict gate. Removing that gate must make this test fail (review 08).
        path = self.probes["codex"]
        descriptor = json.loads(path.read_text())
        descriptor["verdict"] = "fail"
        path.write_text(json.dumps(descriptor))
        manifest = json.loads(self.manifest.read_text())
        manifest["probe_runs"][TARGETS["codex"]]["json"]["sha256"] = digest(path)
        self.manifest.write_text(json.dumps(manifest))
        next(e for e in self.overlay["evidence"] if e["path"] == str(self.manifest))["sha256"] = digest(self.manifest)
        with self.assertRaisesRegex(aa_policy.PolicyError, "transport proof did not pass"):
            self.apply()

    def test_snapshot_only_is_not_resign_drift(self):
        import resign
        # Exercise real validation, but provide synthetic executable/runner anchors.
        anchors = {}
        for vendor in TARGETS:
            runner = self.root / build_overlay.RUNNERS[vendor]
            runner.write_text("unchanged runner")
            anchors[vendor] = dict(cli=self.root / (vendor + ".cli"), runner=runner)
            self.overlay["evidence"].append(dict(path=str(runner), sha256=digest(runner), vendor=vendor))
        with patch.object(build_overlay, "REGISTRY", self.registry), \
                patch.object(build_overlay, "ROWS", self.rows), \
                patch.object(resign, "unprobed", return_value=[]):
            report = resign.detect(self.overlay, anchors)
        self.assertTrue(all(not row["drifted"] for row in report.values()), report)

    def test_worktree_dispatch_dry_run_accepts_old_row_and_refuses_new_row(self):
        # Only the fixture interpreter substitutes the approved synthetic policy
        # pin. The production dispatch script, policy logic, archive, and refusal
        # printer are the actual worktree files, with no production test bypass.
        from offline_env import isolated_environment
        import re
        import shlex
        env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        for key in list(env):
            if key.startswith("OMNILANE_AA_"):
                env.pop(key)
        row = copy.deepcopy(self.rows[TARGETS["codex"]])
        row.update(id="codex/new-model", model="new-model")
        row["transport_mapping"]["candidate_model_ids"] = ["new-model"]
        self.registry["scored_configs"].append(row)
        registry_path = self.root / "next-policy.json"
        registry_path.write_text(json.dumps(self.registry))
        self.path.write_text(json.dumps(self.overlay))
        fixture_bin = self.root / "bin"
        fixture_bin.mkdir()
        shim = self.root / "python-fixture.py"
        shim.write_text(
            "import sys, runpy, pathlib, re\n"
            "script, *args = sys.argv[1:]\n"
            "sys.argv = [script] + args\n"
            "if script.endswith('/aa_policy.py'):\n"
            "    text = pathlib.Path(script).read_text()\n"
            "    text = re.sub(r'APPROVED_REGISTRY_SHA256 = \\\"[a-f0-9]+\\\"', "
            + repr('APPROVED_REGISTRY_SHA256 = "' + digest(registry_path) + '"') + ", text)\n"
            "    sys.path.insert(0, str(pathlib.Path(script).parent))\n"
            "    exec(compile(text, script, 'exec'), {'__name__':'__main__', '__file__':script})\n"
            "else:\n"
            "    runpy.run_path(script, run_name='__main__')\n")
        python = fixture_bin / "python3"
        python.write_text("#!/bin/sh\ncase \"$1\" in\n"
                          " */aa_policy.py) exec " + shlex.quote(sys.executable) + " "
                          + shlex.quote(str(shim)) + ' "$@" ;;\n'
                          " *) exec " + shlex.quote(sys.executable) + ' "$@" ;;\nesac\n')
        python.chmod(0o755)
        fake_cli = fixture_bin / "codex"
        fake_cli.write_text("#!/bin/sh\nexit 0\n")
        fake_cli.chmod(0o755)
        for entry in self.overlay["evidence"]:
            if entry.get("vendor") == "codex":
                entry.update(path=str(fake_cli), sha256=digest(fake_cli))
        self.path.write_text(json.dumps(self.overlay))
        caller_row = max(self.registry["scored_configs"], key=lambda r: r["score"])
        caller = self.root / "caller.json"
        caller.write_text(json.dumps(dict(schema_version=1, snapshot_id=self.registry["snapshot"]["id"],
                                         kind="model", caller=aa_policy._row_identity(caller_row),
                                         inherited_ceiling=100)))
        env.update(PATH=str(fixture_bin) + os.pathsep + env["PATH"],
                   OMNILANE_HOME=str(self.root / "fake-home"),
                   OMNILANE_AA_POLICY_FILE=str(registry_path),
                   OMNILANE_AA_CALLER_FROM_PROCESS="0", OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="0",
                   OMNILANE_AA_TRANSPORT_OVERLAY=str(self.path))
        fake_home = Path(env["OMNILANE_HOME"])
        fake_home.mkdir()
        (fake_home / "routing.local.yaml").write_text("coding: codex gpt-6.1-sol high\n")
        output = []
        for model, expected in ((self.rows[TARGETS["codex"]]["model"], 0), ("new-model", 1)):
            command = ["bash", str(ROOT / "scripts/dispatch.sh"), "--dry-run",
                       "--vendor", "codex", "--model", model, "--effort", "high",
                       "--caller-context", str(caller), "--workdir", str(self.root), "coding", "fixture"]
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=40)
            output.append(dict(model=model, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
            if expected == 0:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("omnilane resign --vendor codex", result.stderr)
                self.assertIn("codex/new-model", result.stderr)
                self.assertIn("requires_gui_session", result.stderr)
        self.assertFalse(violations.exists())
        # Retain a short, synthetic-only proof packet for the assigned report.
        evidence = ROOT / ".sandbox-tmp/resign-ux-p1"
        if evidence.is_dir():
            (evidence / "dispatch-dry-run.json").write_text(json.dumps(output, indent=2))


# Each generated case has an explicit expected disposition and rationale.
# Four vendors times twelve attacks exercises identical strings across distinct
# transport contracts, rather than forty cosmetic variants of one input.
ATTACKS = {
    "score_ceiling": "DENY: new-table score exceeds the inherited caller ceiling",
    "model_alias": "DENY: identical CLI selector now names a different model",
    "effort": "REJECT: changed effort collides with another exact row identity",
    "vendor": "DENY: a selector digest is not authority for a different vendor",
    "executable": "DENY: executable bytes changed at the same path",
    "expired": "DENY: explicitly expired evidence is not a current proof",
    "forged_carry": "REJECT: caller-written carried-forward status grants no authority",
    "duplicate": "REJECT: duplicate overlay row is ambiguous evidence",
    "selector": "DENY: candidate selector set changed",
    "reasoning": "DENY: reasoning identity changed",
    "fallback": "DENY: fallback identity changed",
    "probe_tamper": "REJECT: raw probe descriptor differs from the anchored manifest",
}


def attack_test(vendor, attack):
    def test(self):
        cid = TARGETS[vendor]
        row = self.rows[cid]
        mapping = next(m for m in self.overlay["mappings"] if m["config_id"] == cid)
        if attack == "score_ceiling":
            row["score"] = 90
        elif attack == "model_alias":
            row["model"] += "-replacement"
        elif attack == "effort":
            row["effort"] = "low" if row["effort"] != "low" else "high"
        elif attack == "vendor":
            row["vendor"] = "claude" if vendor != "claude" else "codex"
        elif attack == "executable":
            (self.root / (vendor + ".cli")).write_text("replacement executable")
        elif attack == "expired":
            mapping["expires_at"] = "2000-01-01T00:00:00+00:00"
        elif attack == "forged_carry":
            mapping["carried_forward"] = True
        elif attack == "duplicate":
            self.overlay["mappings"].append(copy.deepcopy(mapping))
        elif attack == "selector":
            row["transport_mapping"]["candidate_model_ids"].append("another-alias")
        elif attack in ("reasoning", "fallback"):
            row[attack] = "changed"
        elif attack == "probe_tamper":
            self.probes[vendor].write_text('{"verdict":"pass","forged":true}')
        if attack in ("forged_carry", "duplicate", "probe_tamper", "effort"):
            with self.assertRaises(aa_policy.PolicyError, msg=ATTACKS[attack]):
                self.apply()
            return
        result = self.apply()
        decision = self.allowed(result, vendor, ceiling=50 if attack == "score_ceiling" else 100)
        self.assertFalse(decision["allowed"], ATTACKS[attack])
        if attack == "score_ceiling":
            self.assertEqual(decision["code"], "target-above-effective-ceiling")
    test.__doc__ = ATTACKS[attack]
    return test


for _vendor in TARGETS:
    for _attack in ATTACKS:
        setattr(CarryTests, "test_false_allow_" + _vendor + "_" + _attack, attack_test(_vendor, _attack))


if __name__ == "__main__":
    unittest.main()
