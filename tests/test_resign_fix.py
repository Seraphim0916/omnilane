"""Returned P3 acceptance regressions, using only isolated fake executables."""
import offline_env  # noqa: F401
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_resign as existing
import test_origin_integration as integration
from test_probe_retry import RetryFixture
import cli_provenance as cp
import origin_policy as op
import probe_sweep
import resign

ROOT = Path(__file__).resolve().parents[1]
REAL_FACTS = cp.facts
KINDS = ("script", "no-platform-signature", "adhoc", "unsigned")


class Host(RetryFixture, unittest.TestCase):
    run_resign = existing.ResignTests.run_resign
    never_probed = existing.ResignTests.never_probed
    older_snapshot = existing.ResignTests.older_snapshot
    missing_cli = existing.ResignTests.missing_cli
    pinned = existing.ResignTests.pinned
    vendor_mappings = existing.ResignTests.vendor_mappings
    command = integration.IntegrationTests.command


@contextmanager
def host(kind="script"):
    fixture = Host()
    fixture.setUp()
    try:
        fixture.signers["claude"] = kind
        fixture.build(fixture.sweep_root)
        fixture.live.write_bytes((fixture.sweep_root / "transport-contracts.local.json").read_bytes())
        yield fixture
    finally:
        fixture.doCleanups()


def prepare(fixture, scenario):
    if scenario == "missing":
        fixture.never_probed()
    elif scenario == "retry":
        fixture.fail_row()
    elif scenario == "expired":
        row = probe_sweep.plan("claude")[-1]["config_id"]
        fixture.change_overlay(lambda overlay: next(m for m in overlay["mappings"]
                               if m["config_id"] == row).update(expires_at="2000-01-01T00:00:00+00:00"))
    elif scenario == "pending":
        with fixture.missing_cli("claude"):
            fixture.run_resign(vendor=["claude"])
    elif scenario == "registry":
        fixture.older_snapshot()
    else:
        raise AssertionError(scenario)


class ReturnedFixTests(unittest.TestCase):
    def assert_reprobed(self, fixture, scenario):
        self.assertEqual(fixture.run_retry(retry_failed=scenario == "retry"), 0, fixture.lines)
        self.assertTrue(fixture.calls, fixture.lines)
        self.assertFalse(any(m.get("pending_recheck") for m in fixture.vendor_mappings("claude")))
        self.assertFalse(op.policy_path().exists())

    def test_unchanged_no_channel_matrix_reprobes_all_twelve_cells(self):
        for kind in KINDS:
            for scenario in ("missing", "retry", "expired"):
                with self.subTest(kind=kind, scenario=scenario), host(kind) as fixture:
                    prepare(fixture, scenario)
                    with patch.object(op, "gate", side_effect=AssertionError("unchanged source rechecked")):
                        self.assert_reprobed(fixture, scenario)

    def test_unchanged_pending_and_registry_keep_legacy_behavior(self):
        for kind in ("script", "adhoc"):
            for scenario in ("pending", "registry"):
                with self.subTest(kind=kind, scenario=scenario), host(kind) as fixture:
                    prepare(fixture, scenario)
                    with patch.object(op, "gate", side_effect=AssertionError("unchanged source rechecked")):
                        self.assert_reprobed(fixture, scenario)

    def test_unchanged_gate_never_calls_facts_or_signature_tool(self):
        for scenario in ("missing", "registry"):
            for error in (FileNotFoundError(), subprocess.TimeoutExpired("codesign", 15)):
                with self.subTest(scenario=scenario, error=type(error).__name__), host("adhoc") as fixture:
                    prepare(fixture, scenario)
                    report = resign.detect(json.loads(fixture.live.read_text()), resign.current_anchors())["claude"]
                    tool = Mock(side_effect=error)
                    with patch.object(cp, "facts", side_effect=lambda path: REAL_FACTS(path, tool)) as facts:
                        with patch.object(op, "gate", side_effect=AssertionError("unchanged source rechecked")):
                            self.assertTrue(resign.gate(report, False)[0])
                        facts.assert_not_called()
                    tool.assert_not_called()
                    self.assert_reprobed(fixture, scenario)

    def test_unchanged_bad_policy_does_not_retain_operator_trust(self):
        for failure in ("empty", "host", "oserror"):
            with self.subTest(failure=failure), host("adhoc") as fixture:
                prepare(fixture, "missing")
                fixture.change_overlay(lambda overlay: [e.update(operator_trust=cp.TRUST_ADHOC)
                                       for e in overlay["evidence"] if e.get("vendor") == "claude"])
                policy = {} if failure == "empty" else dict(schema_version=1, host="another-host", vendors={})
                op.policy_path().write_text(json.dumps(policy))
                op.policy_path().chmod(0o600)
                if failure == "oserror":
                    with patch.object(op, "channels_for", side_effect=OSError("unreadable")):
                        code = fixture.run_retry()
                else:
                    code = fixture.run_retry()
                self.assertEqual(code, 0, fixture.lines)
                self.assertTrue(fixture.calls)
                anchors = [e for e in fixture.overlay()["evidence"] if e.get("vendor") == "claude"]
                self.assertTrue(anchors)
                self.assertTrue(all("operator_trust" not in e for e in anchors))
                self.assertTrue(any("origin policy unavailable" in line for line in fixture.lines))

    def test_changed_no_channel_kinds_are_still_stopped(self):
        for kind in KINDS:
            with self.subTest(kind=kind), host(kind) as fixture:
                fixture.update("claude")
                self.assertEqual(fixture.run_retry(), 20, fixture.lines)
                self.assertEqual(fixture.calls, [])
                if kind == "unsigned":
                    self.assertEqual(fixture.run_retry(approve=["claude"]), 20, fixture.lines)
                    self.assertEqual(fixture.calls, [])

    def test_unchanged_approval_does_not_touch_source_policy(self):
        with host("adhoc") as fixture:
            prepare(fixture, "missing")
            op.policy_path().write_text("{}")
            op.policy_path().chmod(0o600)
            with patch.object(op, "preserve_before_override", side_effect=AssertionError("unchanged source preserved")):
                self.assertEqual(fixture.run_retry(approve=["claude"]), 0, fixture.lines)
            self.assertTrue(fixture.calls)
            self.assertEqual(op.policy_path().read_text(), "{}")

    def test_changed_signature_tool_failure_still_stops_even_approval(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("codesign", 15)):
            with self.subTest(error=type(error).__name__), host("adhoc") as fixture:
                path = fixture.update("claude")
                path.write_bytes(b"\xcf\xfa\xed\xfe" + b"fake")
                tool = Mock(side_effect=error)
                with patch.object(sys, "platform", "darwin"), patch.object(cp, "facts", side_effect=lambda p: REAL_FACTS(p, tool)):
                    for approval in (None, ["claude"]):
                        self.assertEqual(fixture.run_retry(approve=approval), 20, fixture.lines)
                self.assertTrue(tool.called)
                self.assertEqual(fixture.calls, [])

    def test_changed_bad_policy_stops_even_approval(self):
        for policy in ({}, dict(schema_version=1, host="another-host", vendors={})):
            with self.subTest(policy=policy), host("script") as fixture:
                op.policy_path().write_text(json.dumps(policy))
                op.policy_path().chmod(0o600)
                fixture.update("claude")
                for approval in (None, ["claude"]):
                    self.assertEqual(fixture.run_retry(approve=approval), 20, fixture.lines)
                self.assertEqual(fixture.calls, [])

    def test_check_not_installed_has_no_signature_calls_or_source_guidance(self):
        with host() as fixture, fixture.missing_cli("claude"):
            with patch.object(cp, "facts", wraps=fixture.facts) as facts:
                fixture.run_resign(check=True, vendor=["claude"])
            self.assertFalse(any(Path(call.args[0]).name == "claude" for call in facts.call_args_list))
            output = "\n".join(fixture.lines)
            self.assertIn("not installed; skipped / 沒有安裝，略過", output)
            for text in ("could not be classified", "No approval command", "無法分類或查驗"):
                self.assertNotIn(text, output)

    def test_check_unchanged_script_only_displays_kind_and_channels(self):
        from verification_status import origin_guidance
        with host() as fixture:
            self.assertEqual(fixture.run_resign(check=True, vendor=["claude"]), 0)
            output = "\n".join(fixture.lines)
            self.assertIn("kind=script, channels=[]", output)
            for text in ("--approve", "這次沒有執行", "This attempt did not execute", "No approval command"):
                self.assertNotIn(text, output)
            for reason in ("origin-unapproved", "team-changed", "identifier-changed", "location-changed",
                           "invalid", "unsigned", "unverifiable", "policy-invalid"):
                self.assertNotIn(origin_guidance("claude", reason)["message"].split(". This attempt")[0], output)
            self.assertEqual(fixture.command("omnilane resign --trust-location claude"), 0)
            self.assertEqual(fixture.run_resign(check=True, vendor=["claude"]), 0)
            self.assertIn('"accepts": "script"', "\n".join(fixture.lines))

    def test_check_changed_script_still_shows_stop_guidance(self):
        with host() as fixture:
            fixture.update("claude")
            self.assertEqual(fixture.run_resign(check=True, vendor=["claude"]), 10)
            output = "\n".join(fixture.lines)
            self.assertIn("--approve claude", output)
            self.assertIn("這次沒有執行", output)

    def test_trust_origin_cli_rejects_malformed_teams_before_requirement(self):
        bad_teams = ('TEST00000"', "TEST 00001", "TEST0000;1", "short", "test000001", "TEST000001\n")
        for team in bad_teams:
            with self.subTest(team=repr(team)), host("adhoc") as fixture:
                path = fixture.bins["claude"]
                path.write_bytes(b"\xcf\xfa\xed\xfe" + b"fake")
                before = fixture.live.read_bytes()
                calls = []
                def run(argv, **kwargs):
                    calls.append(argv)
                    return SimpleNamespace(returncode=0, stdout="", stderr="Identifier=claude\nSignature=adhoc\nTeamIdentifier=not set\n")
                with patch.object(sys, "platform", "darwin"), patch.object(cp, "facts", side_effect=lambda p: REAL_FACTS(p, run)):
                    with patch.object(sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="TRUST claude " + team):
                        self.assertEqual(fixture.run_resign(_argv=["--trust-origin", "claude", "--team", team]), 20)
                self.assertFalse(op.policy_path().exists())
                self.assertEqual(fixture.live.read_bytes(), before)
                self.assertFalse(any("-R" in call for call in calls))

    def test_trust_origin_cli_rejects_valid_but_different_team(self):
        with host("TEST000001") as fixture:
            before = fixture.live.read_bytes()
            self.assertEqual(fixture.command("omnilane resign --trust-origin claude --team TEST000002",
                                            answer="TRUST claude TEST000002"), 20)
            self.assertFalse(op.policy_path().exists())
            self.assertEqual(fixture.live.read_bytes(), before)

    def test_omnilane_home_elsewhere_uses_only_legacy_implicit_channels(self):
        with host("adhoc") as fixture:
            op.record("claude", op._channel("adhoc", fixture.bins["claude"], fixture.facts(fixture.bins["claude"])))
            recorded = dict(signer=cp.ADHOC, valid=True, identifier="claude")
            with patch.dict(os.environ, OMNILANE_HOME=str(fixture.base / "other-home")):
                self.assertEqual(op.channels_for("claude", recorded, str(fixture.bins["claude"])), [])
                recorded["operator_trust"] = cp.TRUST_ADHOC
                self.assertEqual(op.channels_for("claude", recorded, str(fixture.bins["claude"]))[0]["accepts"], "adhoc")

    def test_skill_keeps_escalation_and_operator_only_contracts(self):
        text = re.sub(r"\s+", " ", (ROOT / "skills/omnilane/SKILL.md").read_text())
        self.assertIn("After two failed attempts at a task, reassess the scope; an upward move needs the human.", text)
        self.assertIn('On vendor quota exhaustion (429, "stream disconnected", usage limit) send mid-tier coding through `coding-overflow`; never silently downgrade `hardest-coding` — wait or escalate.', text)
        self.assertIn("An assistant must never execute trust, approval, signer-recording or revocation commands itself.", text)
        for command in ("--approve", "--record-signers", "--trust-adhoc"):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
