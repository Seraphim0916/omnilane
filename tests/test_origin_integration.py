"""Operator commands, source gate and transport proofs with fake vendor tools."""
import offline_env  # noqa: F401
import copy
import io
import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_resign as fixture
import origin_policy as op
import resign
import verification_status as status

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    setUp = fixture.ResignTests.setUp
    vendor_of = fixture.ResignTests.vendor_of
    facts = fixture.ResignTests.facts
    descriptor = fixture.ResignTests.descriptor
    build = fixture.ResignTests.build
    update = fixture.ResignTests.update
    run_resign = fixture.ResignTests.run_resign
    pinned = fixture.ResignTests.pinned
    vendor_mappings = fixture.ResignTests.vendor_mappings

    def command(self, command, tty=True, answer=None, **kwargs):
        argv = shlex.split(command)
        self.assertEqual(argv[:2], ["omnilane", "resign"])
        with patch.object(sys.stdin, "isatty", return_value=tty), patch("builtins.input", return_value=answer or ""):
            try:
                return self.run_resign(_argv=argv[2:], **kwargs)
            except SystemExit as error:
                return error.code

    def permitted(self, vendor="claude"):
        registry, _ = resign.aa_policy.load_registry(ROOT / "config/aa-model-policy.json")
        return any(r["transport_mapping"].get("runtime_verified") for r in registry["scored_configs"] if r["vendor"] == vendor)

    def test_case08_nonterminal_trust_origin_is_refused(self):
        before = self.live.read_bytes()
        self.assertEqual(self.command("omnilane resign --trust-origin claude", tty=False), 20)
        self.assertFalse(op.policy_path().exists())
        self.assertEqual(self.live.read_bytes(), before)

    def test_case09_confirmation_is_required_and_has_no_bypass(self):
        for answer in ("", "yes", "TRUST claude wrong"):
            self.assertEqual(self.command("omnilane resign --trust-origin claude", answer=answer), 20)
            self.assertFalse(op.policy_path().exists())
        self.assertEqual(self.command("omnilane resign --trust-origin claude --yes"), 2)

    def test_case10_adhoc_requires_explicit_team_even_with_old_team(self):
        self.signers["claude"] = "adhoc"
        self.assertEqual(self.command("omnilane resign --trust-origin claude", answer="TRUST claude TEST000000"), 20)
        self.assertFalse(op.policy_path().exists())
        self.assertEqual(self.command("omnilane resign --trust-origin claude --team TEST000001",
                                      answer="TRUST claude TEST000001"), 0)
        channel = op.load()["vendors"]["claude"]["channels"][0]
        self.assertEqual(channel["team_id"], "TEST000001")
        self.assertEqual(channel["identifier_family"], "")
        self.signers["claude"] = "TEST000001"
        self.update("claude")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)

    def test_case39_trust_does_not_authorize_dispatch_before_probe(self):
        self.bins["claude"].write_text("updated executable")
        self.signers["claude"] = "TEST000009"
        before = self.live.read_bytes()
        self.assertFalse(self.permitted())
        self.assertEqual(self.command("omnilane resign --trust-origin claude", answer="TRUST claude TEST000009"), 0)
        self.assertEqual(self.live.read_bytes(), before)
        self.assertFalse(self.permitted())
        self.assertEqual(self.sweeps, [])
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)
        self.assertTrue(self.permitted())

    def test_case40_approve_is_one_shot_and_does_not_create_channels(self):
        self.update("claude")
        self.signers["claude"] = "adhoc"
        self.assertEqual(self.command("omnilane resign --approve claude"), 0)
        self.assertFalse(any(c.get("accepts") == "adhoc" for c in op.load()["vendors"]["claude"]["channels"]))
        self.sweeps.clear()
        self.update("claude", "1.3.0")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 20)
        self.assertEqual(self.sweeps, [])

    def test_one_shot_developer_approval_never_becomes_an_implicit_grant(self):
        old_team = self.signers["claude"]
        self.update("claude")
        self.signers["claude"] = "TEST000009"
        self.assertEqual(self.command("omnilane resign --approve claude"), 0)
        channels = op.load()["vendors"]["claude"]["channels"]
        self.assertEqual([c["team_id"] for c in channels], [old_team])
        self.update("claude", "1.3.0")
        self.sweeps.clear()
        self.assertEqual(self.command("omnilane resign --vendor claude"), 20)
        self.assertEqual(self.sweeps, [])

    def test_one_shot_approval_cannot_resurrect_a_revoked_channel(self):
        self.assertEqual(self.command("omnilane resign --revoke-origin claude developer-id"), 0)
        self.update("claude")
        self.assertEqual(self.command("omnilane resign --approve claude"), 0)
        self.assertEqual(op.load()["vendors"]["claude"]["channels"], [])
        self.update("claude", "1.3.0")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 20)

    def test_case41_retry_flag_does_not_relax_source_gate(self):
        self.update("claude")
        self.signers["claude"] = "TEST000009"
        self.assertEqual(self.command("omnilane resign --retry-failed --vendor claude"), 20)
        self.assertEqual(self.sweeps, [])

    def test_case42_approved_source_failed_probe_does_not_repin(self):
        old = self.pinned("claude")
        anchors_before = [e for e in json.loads(self.live.read_text())["evidence"] if e.get("vendor") == "claude"]
        self.bins["claude"].write_text("updated executable")
        self.assertEqual(self.command("omnilane resign --vendor claude", outcome="unprobeable"), 20)
        self.assertEqual(self.pinned("claude"), old)
        self.assertEqual([e for e in json.loads(self.live.read_text())["evidence"] if e.get("vendor") == "claude"], anchors_before)
        self.assertFalse(self.permitted())

    def test_case43_factory_evidence_cannot_cover_later_adhoc(self):
        self.assertEqual(self.command("omnilane resign --trust-adhoc claude"), 0)
        self.bins["claude"].write_text("factory update")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)
        self.assertTrue(self.permitted())
        self.bins["claude"].write_text("local adhoc update")
        self.signers["claude"] = "adhoc"
        self.assertFalse(self.permitted())
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)
        self.assertTrue(self.permitted())
        self.assertEqual(self.sweeps, ["claude", "claude"])

    def test_case44_manual_daily_and_phase2_gate_agree(self):
        self.assertEqual(self.command("omnilane resign --trust-adhoc claude"), 0)
        for index, kind in enumerate(("TEST000000", "adhoc", "TEST000009", "unsigned", "invalid", "unverifiable")):
            self.signers["claude"] = kind
            self.update("claude", f"2.{index}.0")
            report = resign.detect(json.loads(self.live.read_text()), resign.current_anchors())["claude"]
            direct = op.gate("claude", report["recorded_codesign"], report["recorded_cli_path"],
                             self.facts(self.bins["claude"]), self.bins["claude"])[0]
            self.assertEqual(resign.gate(report, False)[0], direct)
            expected = 0 if direct else 20
            self.assertEqual(self.command("omnilane resign --vendor claude"), expected, kind)
            self.update("claude", f"3.{index}.0")
            self.assertEqual(self.command("omnilane resign --daily --vendor claude"), expected, kind)

    def test_case45_check_doctor_pending_do_not_leak_full_hashes(self):
        self.update("claude")
        self.signers["claude"] = "TEST000009"
        self.assertEqual(self.command("omnilane resign --check --json"), 10)
        check_output = "\n".join(self.printed)
        self.assertIn("origin_channels", check_output)
        self.assertIn("developer-id", check_output)
        self.assertIn("TEST000009", check_output)
        self.assertNotIn(fixture.digest(self.live), check_output)
        self.assertEqual(self.command("omnilane resign --daily --vendor claude"), 20)
        self.assertNotIn(fixture.digest(self.live), status.pending_summary())
        self.assertIn("來源", status.pending_summary())
        # Run doctor's actual helper with fake facts and paths, preserving its output protocol.
        import runpy
        output = io.StringIO()
        with patch.object(sys, "argv", ["overlay_health.py", str(ROOT)]), patch("sys.stdout", output):
            with self.assertRaises(SystemExit) as exit_result:
                runpy.run_path(str(ROOT / "scripts/lib/overlay_health.py"), run_name="__main__")
        self.assertEqual(exit_result.exception.code, 0)
        self.assertIn("origin kind=developer-id", output.getvalue())
        self.assertNotRegex(output.getvalue(), r"\b[0-9a-f]{64}\b")

    def test_trust_location_command_from_guidance_changes_state(self):
        self.signers["claude"] = "script"
        self.update("claude")
        guidance = status.origin_guidance("claude", "origin-unapproved", self.facts(self.bins["claude"]))
        self.assertEqual(self.command(guidance["next_command"]), 0)
        self.assertEqual(self.command("omnilane resign --trust-location claude"), 0)
        self.update("claude", "1.3.0")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)
        self.assertEqual(self.command("omnilane resign --revoke-origin claude script"), 0)
        self.update("claude", "1.4.0")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 20)

    def test_adhoc_guidance_and_revoke_commands_change_state(self):
        self.signers["claude"] = "adhoc"
        self.update("claude")
        guidance = status.origin_guidance("claude", "origin-unapproved", self.facts(self.bins["claude"]))
        self.assertEqual(self.command(guidance["next_command"]), 0)
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)
        self.assertEqual(self.command("omnilane resign --revoke-origin claude adhoc"), 0)
        self.update("claude", "1.3.0")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 20)
        self.assertEqual(self.command("omnilane resign --trust-adhoc claude"), 0)
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)

    def test_invalid_signature_never_accepts_approve(self):
        self.update("claude")
        for kind in ("invalid", "unsigned", "unverifiable"):
            self.signers["claude"] = kind
            self.assertEqual(self.command("omnilane resign --approve claude"), 20)
            self.assertEqual(self.sweeps, [])
            self.assertFalse(op.policy_path().exists())

    def test_bad_origin_file_refuses_all_commands(self):
        op.policy_path().write_text("{}")
        op.policy_path().chmod(0o600)
        before = self.live.read_bytes()
        for command in ("--trust-origin claude", "--trust-adhoc claude", "--revoke-origin claude adhoc"):
            self.assertEqual(self.command("omnilane resign " + command, answer="TRUST claude TEST000000"), 20)
            self.assertEqual(self.live.read_bytes(), before)

    def test_origin_command_cannot_clear_daily_status_without_probes(self):
        self.assertEqual(self.command("omnilane resign --daily --trust-origin claude"), 2)
        self.assertFalse(op.policy_path().exists())
        self.assertEqual(self.command("omnilane resign --team ''"), 2)

    def test_location_guidance_approves_then_trusts_the_new_adhoc_location(self):
        self.assertEqual(self.command("omnilane resign --trust-adhoc claude"), 0)
        self.signers["claude"] = "adhoc"
        self.update("claude", directory=self.base / "new-location")
        guidance = status.origin_guidance("claude", "location-changed", self.facts(self.bins["claude"]))
        self.assertEqual(self.command(guidance["next_command"]), 0)
        self.assertEqual(self.command("omnilane resign --trust-adhoc claude"), 0)
        self.bins["claude"].write_text("another adhoc update at the newly approved location")
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)

    def test_factory_location_and_identifier_guidance_updates_channels(self):
        self.assertEqual(self.command("omnilane resign --trust-origin claude", answer="TRUST claude TEST000000"), 0)
        self.update("claude", directory=self.base / "factory-new-location")
        guidance = status.origin_guidance("claude", "location-changed", self.facts(self.bins["claude"]))
        self.assertEqual(self.command(guidance["next_command"], answer="TRUST claude TEST000000"), 0)
        self.assertEqual(self.command("omnilane resign --vendor claude"), 0)
        original = self.facts
        def changed_identifier(path, runner=None):
            data = original(path, runner)
            if self.vendor_of(Path(path).name) == "claude":
                data["identifier"] = "renamed-product"
            return data
        self.bins["claude"].write_text("factory identifier changed")
        with patch.object(resign.cli_provenance, "facts", changed_identifier):
            guidance = status.origin_guidance("claude", "identifier-changed", changed_identifier(self.bins["claude"]))
            self.assertEqual(self.command(guidance["next_command"], answer="TRUST claude TEST000000"), 0)
            self.assertEqual(self.command("omnilane resign --vendor claude"), 0)

    def test_protected_files_are_byte_identical_to_base(self):
        paths = ["scripts/lib/probe.py", "scripts/lib/probe_sweep.py", "scripts/lib/transport_compat.py",
                 "config/transport-compatibility.json"]
        paths += [str(p.relative_to(ROOT)) for p in (ROOT / "scripts/runners").glob("run-*.sh")]
        for path in paths:
            base = subprocess.run(["git", "show", "e662aba:" + path], cwd=ROOT, capture_output=True, check=True).stdout
            self.assertEqual((ROOT / path).read_bytes(), base, path)


if __name__ == "__main__":
    unittest.main()
