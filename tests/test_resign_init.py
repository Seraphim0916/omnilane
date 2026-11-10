"""First installation: real overlay builder/gate, fake vendors and smoke dispatch."""
import offline_env
import contextlib
import io
import json
import os
import functools
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_resign as fixtures

resign = fixtures.resign
import origin_policy
import probe_failure


class InitTests(unittest.TestCase):
    def setUp(self):
        self.host = fixtures.ResignTests(methodName="runTest")
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.home = self.host.base / "fresh home"
        self.home.mkdir()
        self.local = self.home / "local.sh"
        self.local.write_text("# keep user settings\nexport CUSTOM_SETTING=kept\n")
        os.environ["OMNILANE_HOME"] = str(self.home)
        os.environ.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
        self.live = self.home / "transport-contracts.local.json"
        self.probed = []
        self.output = io.StringIO()
        self.errors = io.StringIO()

    def sweep(self, vendor, root, **kwargs):
        self.probed.append(vendor)
        rows = fixtures.probe_sweep.plan(vendor)
        for row in rows:
            self.host.descriptor(root, row["name"], "pass")
        return dict(outcome="done", passed=[r["config_id"] for r in rows], failed=[])

    def run_init(self, *args, tty=True, smoke=(True, "fixture passed"), sweep=None):
        smoke_context = (patch.object(resign, "smoke", wraps=functools.partial(
            resign.smoke, runner=self.dispatch_smoke)) if smoke is None else
            patch.object(resign, "smoke", return_value=smoke))
        with patch.object(sys.stdin, "isatty", return_value=tty), \
                patch.object(resign, "canary", return_value=(True, "fixture version")), \
                smoke_context as smoked, \
                patch.object(probe_failure, "run_sweep", side_effect=sweep or self.sweep), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            result = resign.main(["--init", "--json", *args])
        self.smoked = smoked.call_count
        return result

    def dispatch_smoke(self, command, **kwargs):
        # Execute the real dispatch parser and overlay loader; only background
        # job execution is simulated, so no provider or process inspection runs.
        command = [arg for arg in command if arg not in ("--background", "--single-shot")]
        command.insert(2, "--dry-run")
        if getattr(self, "tamper_candidate", False):
            candidate = Path(command[command.index("--transport-overlay") + 1])
            candidate.write_text('{"schema_version": 999}')
        kwargs["env"] = dict(kwargs["env"])
        for vendor, path in self.host.bins.items():
            kwargs["env"][fixtures.build_overlay.CLI_NAMES[vendor].upper() + "_BIN"] = str(path)
        result = subprocess.run(command, **kwargs)
        self.dispatch_results.append(result)
        if result.returncode:
            return result
        words = command[-1].replace("?", "").split()
        answer = int(words[2]) + int(words[4])
        job = self.home / "jobs" / "fixture-smoke"
        job.mkdir(parents=True, exist_ok=True)
        (job / "exit").write_text("0\n")
        (job / "out.txt").write_text(str(answer))
        return subprocess.CompletedProcess(command, 0, "fixture-smoke\n", result.stderr)

    def configure_missing_overlay(self):
        self.live = self.home / "custom overlay.json"
        self.local.write_text("# keep custom setting\nexport OMNILANE_AA_TRANSPORT_OVERLAY="
                              + shlex.quote(str(self.live)) + "\n")
        self.dispatch_results = []
        # Dispatch must resolve only fixture executables, never real vendors.
        for vendor, path in self.host.bins.items():
            path.chmod(0o755)
        os.environ["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(self.home / "wrong inherited.json")

    def test_missing_configured_overlay_smoke_loads_candidate_after_local_settings(self):
        self.configure_missing_overlay()
        before = self.local.read_bytes()
        self.assertEqual(self.run_init("--vendor", "codex", smoke=None), 0, self.errors.getvalue())
        self.assertEqual(len(self.dispatch_results), 1)
        self.assertEqual(self.dispatch_results[0].returncode, 0)
        summary = json.loads(self.output.getvalue())
        self.assertTrue(summary["installed"])
        self.assertIn("answered", summary["smoke"]["codex"]["detail"])
        self.assertEqual(self.local.read_bytes(), before)
        self.assertEqual((Path(summary["sweep_root"]) / "local.sh.BEFORE").read_bytes(), before)

    def test_real_smoke_still_rejects_a_corrupted_candidate_before_install(self):
        self.configure_missing_overlay()
        self.tamper_candidate = True
        before = self.local.read_bytes()
        self.assertEqual(self.run_init("--vendor", "codex", smoke=None), 30, self.errors.getvalue())
        self.assertNotEqual(self.dispatch_results[0].returncode, 0)
        self.assertFalse(json.loads(self.output.getvalue())["installed"])
        self.assertEqual(self.local.read_bytes(), before)
        self.assertFalse(self.live.exists())
        self.assertFalse(origin_policy.policy_path().exists())

    def test_inherited_overlay_cannot_make_an_early_return_look_persistent(self):
        self.local.write_text("# preserve control flow\nexport CUSTOM_SETTING=kept\nreturn 0\n")
        self.local.chmod(0o640)
        before = self.local.read_bytes()
        os.environ["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(self.live)
        self.assertEqual(self.run_init(), 40, self.errors.getvalue())
        summary = json.loads(self.output.getvalue())
        self.assertFalse(summary["installed"])
        self.assertIn("inherited overlay unset", self.errors.getvalue())
        self.assertIn("before any early return", self.errors.getvalue())
        self.assertIn("export OMNILANE_AA_TRANSPORT_OVERLAY=" + shlex.quote(str(self.live)),
                      self.errors.getvalue())
        self.assertEqual(self.local.read_bytes(), before)
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o640)
        self.assertEqual((Path(summary["sweep_root"]) / "local.sh.BEFORE").read_bytes(), before)
        self.assertFalse(self.live.exists())
        self.assertFalse(origin_policy.policy_path().exists())

    def test_second_init_leaves_installed_files_and_backup_unchanged(self):
        self.assertEqual(self.run_init(), 0, self.errors.getvalue())
        paths = (self.local, self.live, origin_policy.policy_path(),
                 Path(json.loads(self.output.getvalue())["sweep_root"]) / "local.sh.BEFORE")
        before = {path: path.read_bytes() for path in paths}
        count = len(self.probed)
        self.assertEqual(self.run_init(), 2)
        self.assertEqual(len(self.probed), count)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_bootstrap_builds_verifies_records_first_trust_and_preserves_local_settings(self):
        self.assertEqual(self.run_init(), 0, self.errors.getvalue())
        summary = json.loads(self.output.getvalue())
        self.assertTrue(summary["installed"])
        self.assertEqual(summary["attention"], [])
        self.assertEqual(set(summary["first_trust"]), set(resign.VENDORS))
        self.assertEqual(self.smoked, 4)
        self.assertIn("installed: true", self.errors.getvalue())
        self.assertIn("export CUSTOM_SETTING=kept", self.local.read_text())
        self.assertEqual(self.local.read_text().count("export OMNILANE_AA_TRANSPORT_OVERLAY="), 1)
        self.assertTrue(all(resign.verified(self.live).values()))
        policy = origin_policy.load()
        self.assertTrue(all(channel["approved_via"] == "first-use"
                            for vendor in policy["vendors"].values() for channel in vendor["channels"]))
        backup = Path(summary["sweep_root"]) / "local.sh.BEFORE"
        self.assertEqual(backup.read_text(), "# keep user settings\nexport CUSTOM_SETTING=kept\n")

    def test_non_tty_and_existing_overlay_refuse_before_probing(self):
        self.assertEqual(self.run_init(tty=False), 2)
        self.assertEqual(self.probed, [])
        self.assertIn("interactive terminal", self.errors.getvalue())
        self.live.write_text("existing bytes")
        self.assertEqual(self.run_init(), 2)
        self.assertEqual(self.live.read_text(), "existing bytes")
        self.assertEqual(self.probed, [])

    def test_daily_and_approval_combinations_are_not_bootstrap(self):
        for flags in (("--daily",), ("--approve", "claude"), ("--trust-adhoc", "claude"),
                      ("--record-signers",), ("--no-smoke",), ("--allow-shrink",), ("--check",)):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as error:
                self.run_init(*flags)
            self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.probed, [])
        self.assertFalse((self.home / "verification-status.json").exists())

    def test_adhoc_and_invalid_signatures_never_execute(self):
        for kind in ("adhoc", "invalid", "unsigned", "unverifiable"):
            with self.subTest(kind=kind):
                self.host.signers["claude"] = kind
                self.assertEqual(self.run_init("--vendor", "claude"), 20)
                self.assertEqual(self.probed, [])
                self.assertFalse(self.live.exists())
                self.assertFalse(origin_policy.policy_path().exists())

    def test_existing_source_policy_does_not_approve_a_changed_signer(self):
        current = self.host.facts(self.host.bins["claude"])
        self.assertTrue(origin_policy.record_first_use("claude", current, str(self.host.bins["claude"])))
        before = origin_policy.policy_path().read_bytes()
        self.host.signers["claude"] = "OTHER12345"
        self.assertEqual(self.run_init("--vendor", "claude"), 20)
        self.assertEqual(origin_policy.policy_path().read_bytes(), before)
        self.assertEqual(self.probed, [])
        self.assertFalse(self.live.exists())

    def test_smoke_failure_keeps_configuration_and_no_live_overlay(self):
        before = self.local.read_bytes()
        self.assertEqual(self.run_init(smoke=(False, "fixture failure")), 30)
        self.assertEqual(self.local.read_bytes(), before)
        self.assertFalse(self.live.exists())
        self.assertFalse(origin_policy.policy_path().exists())

    def test_executable_update_during_probe_never_installs(self):
        def sweep(vendor, root, **kwargs):
            result = self.sweep(vendor, root, **kwargs)
            self.host.update(vendor)
            return result
        self.assertEqual(self.run_init(sweep=sweep), 30)
        self.assertFalse(self.live.exists())
        self.assertFalse(origin_policy.policy_path().exists())

    def test_publication_failure_restores_files(self):
        before = self.local.read_bytes()
        original = resign.aa_policy.atomic_bytes
        def write(path, content):
            if path == self.local:
                raise OSError("fixture configuration write failure")
            return original(path, content)
        with patch.object(resign.aa_policy, "atomic_bytes", side_effect=write):
            self.assertEqual(self.run_init(), 40)
        self.assertEqual(self.local.read_bytes(), before)
        self.assertFalse(self.live.exists())
        self.assertFalse(origin_policy.policy_path().exists())

    def test_unreachable_config_assignment_rolls_back(self):
        self.local.write_text("# user control flow\nreturn 0\n")
        self.local.chmod(0o640)
        before = self.local.read_bytes()
        self.assertEqual(self.run_init(), 40)
        self.assertEqual(self.local.read_bytes(), before)
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.live.exists())
        self.assertFalse(origin_policy.policy_path().exists())

    def test_unfinished_vendor_is_not_installed_but_other_vendor_is(self):
        def sweep(vendor, root, **kwargs):
            result = self.sweep(vendor, root, **kwargs)
            if vendor == "claude":
                result.update(outcome="unprobeable", detail="fixture login failure")
            return result
        self.assertEqual(self.run_init(sweep=sweep), 20)
        summary = json.loads(self.output.getvalue())
        self.assertTrue(summary["installed"])
        self.assertEqual(resign.verified(self.live)["claude"], 0)
        self.assertNotIn("claude", origin_policy.load()["vendors"])
        attention = {item["vendor"]: item for item in summary["attention"]}
        self.assertEqual(attention["claude"]["reason"], "needs-login-session")
        self.assertIn("installed: true; some rows are still outstanding", self.errors.getvalue())


if __name__ == "__main__":
    unittest.main()
