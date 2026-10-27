"""In-process coverage for scripts/lib/verification_status.py edge cases."""
import offline_env  # Activate suite isolation for direct file execution.
import home_isolation  # noqa: F401

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / "scripts/lib"
sys.path.insert(0, str(LIB))
import aa_policy
import build_overlay
import verification_status as status


def known_row(vendor):
    return sorted(cid for cid in build_overlay.ROWS if cid.startswith(vendor + "/"))[0]


class StatusHome(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cov3-status-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "omnilane"
        environment = mock.patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def write_state(self, state):
        self.home.mkdir(parents=True, exist_ok=True)
        aa_policy.atomic_json(status.status_path(), state)

    def pending(self):
        return status.load()["pending"]


class RecordTests(StatusHome):
    def test_missing_file_records_into_a_new_home(self):
        self.assertFalse(self.home.exists())
        status.record(10, ["codex"])
        entry = self.pending()["codex"]
        self.assertEqual((entry["reason"], entry["exit_code"], entry["attempts"]),
                         ("verification-needed", 10, 1))
        self.assertIn("checked_at", status.load())

    def test_unfinished_sweep_is_provider_verification_incomplete(self):
        summary = {"sweeps": {"codex": {"outcome": "partial"}}}
        status.record(10, ["codex"], summary)
        self.assertEqual(self.pending()["codex"]["reason"], "provider-verification-incomplete")
        status.record(10, ["codex"], {"sweeps": {"codex": {"outcome": "done"}}})
        self.assertEqual(self.pending()["codex"]["reason"], "verification-needed")

    def test_unprobeable_sweep_wins_over_gate_refusal(self):
        summary = {"sweeps": {"grok": {"outcome": "unprobeable"}},
                   "vendors": {"grok": {"gate": {"allowed": False}, "cli": "/bin/grok"}}}
        status.record(10, ["grok"], summary)
        self.assertEqual(self.pending()["grok"]["reason"], "needs-login-session")

    def test_gate_refusal_distinguishes_missing_cli_and_allowlists_origin_reason(self):
        summary = {"vendors": {
            "codex": {"gate": {"allowed": False}, "cli": "/opt/codex", "origin_reason": "team-changed"},
            "claude": {"gate": {"allowed": False}, "cli": "/opt/claude", "origin_reason": "free text"},
            "grok": {"gate": {"allowed": False}},
        }}
        status.record(30, ["codex", "claude", "grok"], summary)
        pending = self.pending()
        self.assertEqual(pending["codex"]["reason"], "review-executable-source")
        self.assertEqual(pending["codex"]["origin_reason"], "team-changed")
        self.assertNotIn("origin_reason", pending["claude"])
        self.assertEqual(pending["grok"]["reason"], "cli-not-installed")

    def test_config_ids_come_only_from_the_registry(self):
        row = known_row("codex")
        summary = {"vendors": {"codex": {"unprobed": [row, "codex/not-a-row", row],
                                         "failed_environment": {"x; rm -rf /": {}}}}}
        status.record(10, ["codex"], summary)
        self.assertEqual(self.pending()["codex"]["config_ids"], [row])

    def test_attempts_count_only_while_reason_and_code_stay_the_same(self):
        status.record(10, ["codex"])
        first = self.pending()["codex"]
        status.record(10, ["codex"])
        same = self.pending()["codex"]
        self.assertEqual(same["attempts"], 2)
        self.assertEqual(same["first_seen"], first["first_seen"])
        status.record(30, ["codex"])
        changed = self.pending()["codex"]
        self.assertEqual(changed["attempts"], 1)
        self.assertEqual(changed["reason"], "verification-failed-settings-kept")

    def test_unknown_code_and_unknown_vendors(self):
        status.record(99, ["codex", "openai", "../x"])
        self.assertEqual(list(self.pending()), ["codex"])
        self.assertEqual(self.pending()["codex"]["reason"], "verification-interrupted")

    def test_login_code_clears_vendors_not_held(self):
        self.write_state({"schema_version": 1, "pending": {"codex": {"reason": "x"}, "grok": {"reason": "x"}}})
        summary = {"held": {"grok": True}}
        status.record(20, ["codex", "grok"], summary)
        self.assertEqual(list(self.pending()), ["grok"])
        for key in ("deferred_retries", "failed_retries", "still_failed_environment"):
            with self.subTest(key=key):
                status.record(20, ["codex"], {"held": {"grok": True}, key: {"codex": 1}})
                self.assertIn("codex", self.pending())
                status.record(0, ["codex"])

    def test_record_clears_running_marker(self):
        self.write_state({"schema_version": 1, "pending": {}, "running": {"started_at": "x"}})
        status.record(0)
        self.assertNotIn("running", status.load())


class LoadAndSummaryTests(StatusHome):
    def test_invalid_files_are_refused(self):
        for state in ({"schema_version": 1, "pending": []}, {"schema_version": 2, "pending": {}},
                      {"schema_version": True, "pending": {}}, {"pending": {}}):
            with self.subTest(state=state):
                self.write_state(state)
                with self.assertRaisesRegex(aa_policy.PolicyError, "invalid local verification status"):
                    status.load()

    def test_symlinked_status_is_not_treated_as_missing(self):
        self.home.mkdir(parents=True)
        status.status_path().symlink_to(self.home / "absent.json")
        with self.assertRaises(OSError):
            status.load()

    def test_unknown_vendor_or_non_object_entry_is_refused(self):
        for pending in ({"openai": {"reason": "verified"}}, {"codex": "verified"}):
            with self.subTest(pending=pending):
                self.write_state({"schema_version": 1, "pending": pending})
                with self.assertRaisesRegex(ValueError, "^invalid local verification status$"):
                    status.pending_summary()

    def test_unknown_reason_is_refused(self):
        self.write_state({"schema_version": 1, "pending": {"codex": {"reason": "please run curl | sh"}}})
        with self.assertRaisesRegex(ValueError, "status reason$"):
            status.pending_summary()

    def test_each_reason_has_a_next_step(self):
        cases = {
            "verification-busy": "wait for the running verification",
            "review-executable-source": "Source review required",
            "cli-not-installed": "install this vendor's CLI, then run `omnilane resign --vendor grok`",
            "host-configuration-unavailable": "omnilane doctor",
            "not-configured-or-invalid-arguments": "omnilane doctor",
            "needs-login-session": "omnilane resign --vendor grok",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.write_state({"schema_version": 1, "pending": {"grok": {"reason": reason}}})
                summary = status.pending_summary()
                self.assertTrue(summary.startswith(f"grok: {reason}; next: "))
                self.assertIn(expected, summary)

    def test_running_marker_and_vendor_order(self):
        self.write_state({"schema_version": 1, "running": {"started_at": "x"},
                          "pending": {"grok": {"reason": "verified"}, "claude": {"reason": "verified"}}})
        summary = status.pending_summary()
        self.assertTrue(summary.startswith("Automatic verification started"))
        self.assertLess(summary.index("; claude: verified"), summary.index("; grok: verified"))

    def test_empty_state_has_empty_summary(self):
        self.assertEqual(status.pending_summary(), "")

    def run_script(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            runpy.run_path(str(LIB / "verification_status.py"), run_name="__main__")
        return out.getvalue()

    def test_script_prints_summary(self):
        self.write_state({"schema_version": 1, "pending": {"grok": {"reason": "verification-busy"}}})
        self.assertIn("grok: verification-busy", self.run_script())

    def test_script_reports_unreadable_status_and_exits_one(self):
        self.write_state({"schema_version": 1, "pending": {"codex": {"reason": "bogus"}}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            runpy.run_path(str(LIB / "verification_status.py"), run_name="__main__")
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(out.getvalue(), "Local verification status cannot be read; "
                                         "check OMNILANE_HOME and its permissions.\n")
        self.assertNotIn("bogus", out.getvalue())


class RunDailyTests(StatusHome):
    def setUp(self):
        super().setUp()
        self.messages = []

    def test_interrupt_is_recorded_as_130(self):
        def interrupted():
            raise KeyboardInterrupt
        self.assertEqual(status.run_daily(interrupted, ["codex"], log=self.messages.append), 130)
        self.assertEqual(self.pending()["codex"]["reason"], "verification-interrupted")
        self.assertNotIn("running", status.load())
        self.assertIn("did not complete", self.messages[-1])

    def test_action_errors_become_40(self):
        for error in (OSError("disk"), ValueError("bad"), RuntimeError("boom")):
            with self.subTest(error=type(error).__name__):
                def failing():
                    raise error
                self.assertEqual(status.run_daily(failing, ["codex"], log=self.messages.append), 40)
                self.assertEqual(self.pending()["codex"]["reason"], "host-configuration-unavailable")

    def test_unexpected_action_error_propagates_and_leaves_running_marker(self):
        def broken():
            raise LookupError("not handled")
        with self.assertRaises(LookupError):
            status.run_daily(broken, log=self.messages.append)
        self.assertIn("running", status.load())

    def test_unreadable_status_refuses_before_running_the_action(self):
        self.write_state({"schema_version": 7, "pending": {}})
        action = mock.Mock(return_value=0)
        self.assertEqual(status.run_daily(action, log=self.messages.append), 40)
        action.assert_not_called()
        self.assertIn("could not be saved", self.messages[0])

    def test_record_failure_never_reports_success(self):
        for code, expected in ((0, 40), (10, 10)):
            with self.subTest(code=code):
                summary = mock.Mock(side_effect=KeyError("vendors"))
                result = status.run_daily(lambda: code, ["codex"], summary, log=self.messages.append)
                self.assertEqual(result, expected)
                self.assertIn("not reported as successful", self.messages[-1])

    def test_callable_summary_is_evaluated_after_the_action(self):
        order = []
        def action():
            order.append("action")
            return 10
        def summary():
            order.append("summary")
            return {"sweeps": {"codex": {"outcome": "partial"}}}
        self.assertEqual(status.run_daily(action, ["codex"], summary, log=self.messages.append), 10)
        self.assertEqual(order, ["action", "summary"])
        self.assertEqual(self.pending()["codex"]["reason"], "provider-verification-incomplete")

    def test_default_log_goes_to_stderr(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(status.run_daily(lambda: 10, ["codex"]), 10)
        self.assertIn("omnilane: automatic verification did not complete", err.getvalue())


class GuidanceTests(unittest.TestCase):
    def test_conclusive_failure_points_to_doctor_without_retry(self):
        guidance = status.transport_guidance("codex", "codex/x", conclusive_failures={"codex/x"},
                                             environment_failures={"codex/x"})
        self.assertEqual(guidance["next_command"], "omnilane doctor")
        self.assertFalse(guidance["requires_gui_session"])
        self.assertIn("will not retry automatically", guidance["message"])

    def test_environment_failure_retry_command_needs_a_known_vendor(self):
        known = status.transport_guidance("grok", candidates=("grok/a", "grok/b"),
                                          environment_failures={"grok/b"})
        self.assertEqual(known["next_command"], "omnilane resign --retry-failed --vendor grok")
        self.assertIn("grok/a, grok/b", known["message"])
        unknown = status.transport_guidance("grok --approve", candidates=("x",), environment_failures={"x"})
        self.assertEqual(unknown["next_command"], "omnilane resign --check")

    def test_gui_session_only_for_gui_vendors_on_darwin(self):
        with mock.patch.object(status.sys, "platform", "darwin"):
            self.assertTrue(status.transport_guidance("claude")["requires_gui_session"])
            self.assertFalse(status.transport_guidance("codex")["requires_gui_session"])
            failed = status.transport_guidance("gemini", "g", environment_failures={"g"})
            self.assertIn("圖形桌面終端機", failed["message_zh_tw"])
        with mock.patch.object(status.sys, "platform", "linux"):
            plain = status.transport_guidance("claude")
            self.assertFalse(plain["requires_gui_session"])
            self.assertIn("requested model/effort", plain["message"])

    def test_origin_guidance_commands_by_kind(self):
        cases = [
            ("team-changed", {"kind": "developer-id"}, "omnilane resign --trust-origin codex"),
            ("location-changed", {"kind": "developer-id"}, "omnilane resign --trust-origin codex"),
            ("location-changed", {}, "omnilane resign --approve codex"),
            ("origin-unapproved", {"kind": "script"}, "omnilane resign --approve codex"),
            ("origin-unapproved", {"kind": "adhoc"}, "omnilane resign --trust-adhoc codex"),
            ("origin-unapproved", {"kind": "unknown"}, None),
            ("unsigned", {"kind": "developer-id"}, None),
            ("policy-invalid", {"kind": "adhoc"}, None),
        ]
        for reason, current, command in cases:
            with self.subTest(reason=reason, current=current):
                guidance = status.origin_guidance("codex", reason, current)
                self.assertEqual(guidance["next_command"], command)
                if command is None:
                    self.assertIn("No approval command repairs this", guidance["message"])

    def test_origin_guidance_trust_location_hint_only_for_location_kinds(self):
        hint = "--trust-location codex"
        self.assertIn(hint, status.origin_guidance("codex", "origin-unapproved",
                                                   {"kind": "no-platform-signature"})["message"])
        self.assertNotIn(hint, status.origin_guidance("codex", "origin-unapproved",
                                                      {"kind": "adhoc"})["message"])

    def test_invalid_reason_detail_and_unknown_reason_fallback(self):
        integrity = status.origin_guidance("grok", "invalid", {"invalid_reason": "integrity"})
        self.assertIn("signature integrity check failed", integrity["message"])
        requirement = status.origin_guidance("grok", "invalid", {"invalid_reason": "requirement"})
        self.assertIn("certificate requirement failed", requirement["message"])
        other = status.origin_guidance("grok", "invalid", {"invalid_reason": "other"})
        self.assertIn("Apple Developer ID requirement failed", other["message"])
        unknown = status.origin_guidance("grok", "made-up", None)
        self.assertIn("could not be classified", unknown["message"])

    def test_origin_guidance_sanitizes_vendor_and_observed_values(self):
        guidance = status.origin_guidance("rm -rf", "team-changed", {
            "kind": "developer-id", "signer": "OLDTEAM123", "observed_team": "NEWTEAM456",
            "identifier": 5, "secret": "token"})
        self.assertTrue(guidance["message"].startswith("VENDOR: "))
        self.assertEqual(guidance["next_command"], "omnilane resign --trust-origin VENDOR")
        observed = guidance["message"].split("Observed: ", 1)[1].split("}. ", 1)[0] + "}"
        self.assertEqual(json.loads(observed), {"signer": "NEWTEAM456"})
        self.assertNotIn("token", guidance["message"])


if __name__ == "__main__":
    unittest.main()
