"""Operator commands, source gate and transport proofs with fake vendor tools."""
import offline_env  # noqa: F401
import copy
import hashlib
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

# Protected files: this branch must not change them. The gate is the pinned SHA-256 of each file's
# content at commit e662aba, so it runs in a shallow clone, a release archive, or with no git at all.
# A legitimate change to a protected file has to update this table in the same commit.
PROTECTED_BASE = "e662aba"
# AA snapshot proposals may update these data/pin artifacts while retaining the
# exact historical bytes and every other protected runtime file as baselines.
SNAPSHOT_REBASE_BASE_SHA256 = {
    "scripts/lib/transport_compat.py":
        "3534d96a4835a43b0265b8756c139922e3b542cd4ecbdffc9c7a484fc4e99512",
    "config/transport-compatibility.json":
        "3c5f261d348d011fe1eed40ef734cf188903b19d9915cf520a3487fa6292c63e",
    # The Gemini runner stopped silently truncating large tasks (G1).
    "scripts/runners/run-gemini.sh":
        "2e4c4c064f3228293ff63e882154be77bac256e6c40add3f25403f1e539ff12c",
}
PROTECTED_SHA256 = {
    "scripts/lib/probe.py":
        "561c129266028c3549091ce36d28581b06833c87606b4d9a43b10ce6575cd719",
    "scripts/lib/probe_sweep.py":
        "5b98a7facc460e94cfab573a88c4835df06b55f56f6f56d6576d0c5975d14bc7",
    "scripts/lib/transport_compat.py":
        "76c93ae1805dbd264cffd7ca102983c9a5d90bf0a1646460aca0552237256656",
    "config/transport-compatibility.json":
        "32b9da5f6572427098e9527395a508d2565a0bc004cbf4ab80cff88b806f363b",
    "scripts/runners/run-cerebras.sh":
        "7a981da6af5cc143d9801ffd5abef4b376cbd45f31a1ee09b40999839b007b1f",
    "scripts/runners/run-claude.sh":
        "a84c59ec7adf04893b2833c503606717a3288ca290138e8176adc4bc42448945",
    "scripts/runners/run-codex.sh":
        "edf44622756a5c72358bbc6a105df191fa8c9d7899090a1590921da367fa721f",
    "scripts/runners/run-deepseek.sh":
        "8aaf03eaeb3fc1c8d6a8ce7d2db2cf4dfb5a098b014a0cad73a91fb645b8b064",
    "scripts/runners/run-exec.sh":
        "bd44a523b02884d8d350197516fb1ef2d7e5ae98fbd726bd6d04def00d32ef9a",
    "scripts/runners/run-gemini.sh":
        "27ac29d5631a2962e7741c9e6793c5f59e0038a34027e34aa73fca82febdd8af",
    "scripts/runners/run-grok.sh":
        "120910ce341e9bbfebb33f64c7d6ac72e7578fcfe20642016a649f035f0b613b",
    "scripts/runners/run-groq.sh":
        "29f741cdbe62535ed029e53ae9ee7cb8647fdb1a71179715e7e60ff21707237f",
    "scripts/runners/run-kimi.sh":
        "0f2b3d3692b9aff1f548c079bc97cbaa56c9661500ee27fee796f4202d5512fc",
    "scripts/runners/run-mistral.sh":
        "4893c2dc96ef193d6bd679638c81960ec911da82aea47dd0cee77bd459d8825a",
    "scripts/runners/run-openai-compat.sh":
        "e6e7b0cbf600823035f1714f0c617af14ddf530ecd4954744ad76ccea123e4f2",
    "scripts/runners/run-opencode.sh":
        "7dd07be771fb8a60194cacd18b0eeeebd6535bd9a7d0a5e4bad314c87651c90d",
    "scripts/runners/run-openrouter.sh":
        "64ee12e583f143e5b2b23cb173d320ade83c4a3bdab91e19ea671642e2b170ab",
    "scripts/runners/run-qwen.sh":
        "aed846b06a287291c210b153f727153a537aae8bfc9b49894f431298db24183d",
    "scripts/runners/run-vote.sh":
        "22825848e8dfedd27b16029917cfbd4105a07b6f06e15ea1b0cb9c7d7d46c618",
    "scripts/runners/run-zai.sh":
        "45a05bf343ec4523d5f51ae1491ee444b26246576e8366c92a8f7238217833bc",
}


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
        runners = {str(p.relative_to(ROOT)) for p in (ROOT / "scripts/runners").glob("run-*.sh")}
        self.assertEqual(runners, {p for p in PROTECTED_SHA256 if p.startswith("scripts/runners/")})
        for path, expected in PROTECTED_SHA256.items():
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), expected, path)

    def test_protected_files_match_base_commit_when_history_is_available(self):
        for path in PROTECTED_SHA256:
            try:
                base = subprocess.run(["git", "show", PROTECTED_BASE + ":" + path], cwd=ROOT, capture_output=True)
                reason = base.stderr.decode("utf-8", "replace").strip()
            except OSError as error:
                base, reason = None, str(error)
            if base is None or base.returncode != 0:
                self.skipTest("history comparison cannot read {}:{} from the repository: {}".format(
                    PROTECTED_BASE, path, reason))
            if path in SNAPSHOT_REBASE_BASE_SHA256:
                self.assertEqual(hashlib.sha256(base.stdout).hexdigest(),
                                 SNAPSHOT_REBASE_BASE_SHA256[path], path)
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
                                 PROTECTED_SHA256[path], path)
            else:
                self.assertEqual((ROOT / path).read_bytes(), base.stdout, path)


if __name__ == "__main__":
    unittest.main()
