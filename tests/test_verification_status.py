"""Offline daily failure persistence and actionable-message tests."""
from __future__ import annotations

import offline_env  # noqa: F401
import home_isolation  # noqa: F401
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy
import verification_status as status


class DailyStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".sandbox-tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.messages = []

    def run_daily(self, code, **kwargs):
        return status.run_daily(lambda: code, log=self.messages.append, **kwargs)

    def test_all_failure_exit_codes_remain_nonzero_and_queryable(self):
        for code in (2, 10, 20, 30, 40, 50, 137):
            with self.subTest(code=code):
                self.assertEqual(self.run_daily(code), code)
                self.assertEqual(status.load()["pending"]["codex"]["exit_code"], code)
                self.assertIn("next:", status.pending_summary())
        self.assertEqual(status.status_path().stat().st_mode & 0o777, 0o600)

    def test_success_resolves_only_selected_vendors(self):
        self.run_daily(20)
        self.run_daily(0, vendors=["codex"])
        self.assertNotIn("codex", status.load()["pending"])
        self.assertIn("grok", status.load()["pending"])
        self.run_daily(0)
        self.assertEqual(status.pending_summary(), "")

    def test_repeated_failure_is_deduplicated_but_counted(self):
        self.run_daily(30, vendors=["claude"])
        before = status.load()["pending"]["claude"]
        self.run_daily(30, vendors=["claude"])
        after = status.load()["pending"]["claude"]
        self.assertEqual(after["first_seen"], before["first_seen"])
        self.assertEqual(after["attempts"], 2)
        self.assertEqual(len(status.load()["pending"]), 1)

    def test_unwritable_status_never_claims_saved_or_success(self):
        with patch.object(status, "record", side_effect=PermissionError("blocked")):
            self.assertEqual(self.run_daily(0), 40)
            self.assertEqual(self.run_daily(30), 30)
        self.assertTrue(all("could not be saved" in line for line in self.messages))

    def test_interruption_is_recorded_and_not_success(self):
        def interrupt():
            raise KeyboardInterrupt
        self.assertEqual(status.run_daily(interrupt, log=self.messages.append), 130)
        self.assertEqual(status.load()["pending"]["grok"]["exit_code"], 130)

    def test_early_exception_is_recorded_without_raw_details(self):
        def fail():
            raise OSError("SECRET_CREDENTIAL")
        self.assertEqual(status.run_daily(fail, log=self.messages.append), 40)
        self.assertNotIn("SECRET_CREDENTIAL", status.status_path().read_text())
        self.assertNotIn("SECRET_CREDENTIAL", " ".join(self.messages))

    def test_login_and_signer_guidance_are_distinct(self):
        summary = dict(held=["claude", "grok"],
                       sweeps={"claude": {"outcome": "unprobeable", "detail": "SECRET_PROMPT"}},
                       vendors={"grok": {"gate": {"allowed": False}, "cli": "/secret/path"}})
        self.run_daily(20, summary=summary)
        pending = status.load()["pending"]
        self.assertEqual(pending["claude"]["reason"], "needs-login-session")
        self.assertEqual(pending["grok"]["reason"], "review-executable-source")
        self.assertEqual(set(pending), {"claude", "grok"})
        text = status.status_path().read_text() + status.pending_summary()
        self.assertNotIn("SECRET_PROMPT", text)
        self.assertNotIn("/secret/path", text)
        self.assertIn("inspect the executable", text)

    def test_forged_status_is_informational_not_a_transport_permission(self):
        status.record(0)
        self.assertEqual(status.pending_summary(), "")
        env = dict(os.environ)
        env.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
        with patch.dict(os.environ, env, clear=True):
            registry, _ = aa_policy.load_registry(ROOT / "config/aa-model-policy.json")
        self.assertTrue(all(not r["transport_mapping"].get("runtime_verified")
                            for r in registry["scored_configs"]))

    def test_guidance_names_vendor_row_command_and_gui_requirement(self):
        with patch.object(sys, "platform", "darwin"):
            result = status.transport_guidance("claude", "claude/model-high")
            self.assertIn("claude/model-high", result["message"])
            self.assertEqual(result["next_command"], "omnilane resign --vendor claude")
            self.assertTrue(result["requires_gui_session"])
            self.assertIn("圖形桌面", result["message_zh_tw"])
            self.assertFalse(status.transport_guidance("codex")["requires_gui_session"])

    def test_malformed_state_is_reported_not_silently_cleared(self):
        status.status_path().write_text('{"schema_version":1,"pending":[]}')
        self.assertEqual(self.run_daily(0), 40)
        self.assertIn("could not be saved", self.messages[-1])

    def test_real_daily_wrapper_and_doctor_show_unconfigured_failure(self):
        from offline_env import isolated_environment
        env, violations = isolated_environment(self.home / "tools", os.environ["PATH"])
        env["OMNILANE_HOME"] = str(self.home)
        result = subprocess.run(["bash", str(ROOT / "scripts/resign-daily.sh")], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(status.status_path().is_file())
        doctor = subprocess.run(["bash", str(ROOT / "scripts/doctor.sh"), "--json"], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertIn("verification-pending", doctor.stdout)
        self.assertIn("not-configured-or-invalid-arguments", doctor.stdout)
        self.assertFalse(violations.exists())

    def test_daily_argument_error_is_persisted(self):
        import contextlib
        import io
        import resign
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(resign.main(["--daily", "--not-a-real-flag"]), 2)
        self.assertTrue(status.load()["pending"])

    def test_unfinished_process_remains_visible(self):
        def terminated():
            raise SystemExit(143)
        with self.assertRaises(SystemExit):
            status.run_daily(terminated)
        self.assertIn("interrupted", status.pending_summary())


if __name__ == "__main__":
    unittest.main()
