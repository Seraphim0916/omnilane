import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy  # noqa: E402
import resign  # noqa: E402

CODEX_ROW = next(cid for cid, row in resign.build_overlay.ROWS.items() if row["vendor"] == "codex")


def completed(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class HomeFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="resign-cov-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "omnilane"
        self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logs = []

    def log(self, text):
        self.logs.append(text)


class CanaryAndEvidenceTests(HomeFixture):
    def test_canary_reports_why_a_cli_is_not_usable(self):
        def raising(*args, **kwargs):
            raise FileNotFoundError(2, "absent")

        self.assertEqual(resign.canary("/x/codex", raising),
                         (False, "--version did not run: FileNotFoundError"))
        timeout = mock.Mock(side_effect=subprocess.TimeoutExpired("codex", 30))
        self.assertEqual(resign.canary("/x/codex", timeout),
                         (False, "--version did not run: TimeoutExpired"))
        self.assertEqual(resign.canary("/x/codex", lambda *a, **k: completed(3, "1.0")),
                         (False, "--version exited 3"))
        self.assertEqual(resign.canary("/x/codex", lambda *a, **k: completed(0, " \n")),
                         (False, "--version exited 0"))
        long_line = "codex-cli " + "9" * 100
        self.assertEqual(resign.canary("/x/codex", lambda *a, **k: completed(0, "", long_line + "\nmore")),
                         (True, long_line[:80]))

    def test_previous_root_is_the_manifest_without_a_vendor(self):
        self.assertIsNone(resign.previous_root({}))
        overlay = {"evidence": [{"vendor": "codex", "path": "/a/probe-manifest.json"},
                                {"path": "/b/other.json"},
                                {"path": "/sweep/1/probe-manifest.json"}]}
        self.assertEqual(resign.previous_root(overlay), Path("/sweep/1"))
        self.assertIsNone(resign.previous_root({"evidence": overlay["evidence"][:2]}))

    def test_keep_failed_output_sets_aside_only_this_sweeps_rewrites(self):
        source, target = self.base / "old", self.base / "new"
        source.mkdir()
        target.mkdir()
        (source / "probe.json").write_text("old")
        (target / "probe.json").write_text("old")
        (target / "probe.stderr").write_text("new failure")
        (target / "probe.failed.json").write_text("earlier")
        (source / "probe.out").write_text("old out")
        (target / "probe.out").write_text("new out")
        kept = resign.keep_failed_output(source, target, "probe")
        self.assertEqual(sorted(p.name for p in kept), ["probe.failed.out", "probe.failed.stderr"])
        self.assertEqual((target / "probe.failed.out").read_text(), "new out")
        self.assertEqual((target / "probe.failed.json").read_text(), "earlier")
        self.assertEqual((target / "probe.json").read_text(), "old")


class SmokeTests(HomeFixture):
    def overlay(self, **mapping):
        value = {"config_id": CODEX_ROW, "runtime_model": "gpt-fixture", "runtime_effort": "high",
                 "selector_type": "flag"}
        value.update(mapping)
        return {"mappings": [value]}

    def finished(self, job, code, answer):
        directory = self.home / "jobs" / job
        directory.mkdir(parents=True)
        (directory / "exit").write_text(code + "\n")
        (directory / "out.txt").write_text(answer)

    def smoke(self, runner, overlay=None):
        return resign.smoke("codex", overlay or self.overlay(), self.base / "overlay.json",
                            timeout=5, runner=runner)

    def test_no_dispatchable_mapping_is_refused_without_running_anything(self):
        runner = mock.Mock()
        overlay = self.overlay(runtime_effort="", selector_type="flag")
        self.assertEqual(self.smoke(runner, overlay), (False, "no verified mapping to dispatch"))
        self.assertEqual(self.smoke(runner, {"mappings": []}), (False, "no verified mapping to dispatch"))
        runner.assert_not_called()

    def test_dispatch_that_does_not_start_or_exits_nonzero(self):
        broken = mock.Mock(side_effect=OSError("no bash"))
        self.assertEqual(self.smoke(broken), (False, "dispatch did not start: OSError"))
        self.assertEqual(self.smoke(lambda *a, **k: completed(4, "", "x" * 300 + "\n")),
                         (False, "dispatch exited 4: " + "x" * 200))
        self.assertEqual(self.smoke(lambda *a, **k: completed(0, "  \n", "")),
                         (False, "dispatch exited 0: "))

    def test_dispatch_command_pins_the_overlay_and_the_cheapest_selector(self):
        seen = {}

        def runner(command, **kwargs):
            seen.update(command=command, env=kwargs["env"])
            return completed(0, "noise\n20261001-000000-1-1\n")

        os.environ["OMNILANE_AA_OVERLAY_SHA256"] = "stale"
        self.finished("20261001-000000-1-1", "0", "200")
        with mock.patch.object(resign.secrets, "randbelow", return_value=0):
            ok, detail = self.smoke(runner)
        self.assertEqual((ok, detail), (True, f"job 20261001-000000-1-1 on {CODEX_ROW} answered"))
        command = seen["command"]
        self.assertEqual(command[command.index("--model") + 1], "gpt-fixture")
        self.assertEqual(command[command.index("--effort") + 1], "high")
        self.assertIn("What is 100 plus 100?", command[-1])
        self.assertEqual(seen["env"]["OMNILANE_AA_TRANSPORT_OVERLAY"], str(self.base / "overlay.json"))
        self.assertNotIn("OMNILANE_AA_OVERLAY_SHA256", seen["env"])

    def test_encoded_effort_selector_sends_no_effort_flag(self):
        seen = []
        overlay = self.overlay(runtime_effort="", selector_type="model_id_encoded_effort")
        self.smoke(lambda command, **kwargs: seen.append(command) or completed(1), overlay)
        self.assertNotIn("--effort", seen[0])

    def test_unfinished_or_wrong_answer_is_a_failed_smoke(self):
        job = "20261001-000000-2-2"
        runner = lambda *a, **k: completed(0, job + "\n")  # noqa: E731
        clock = iter([0, 0, 10 ** 9])
        with mock.patch.object(resign.time, "time", side_effect=lambda: next(clock)), \
                mock.patch.object(resign.time, "sleep") as sleep:
            self.assertEqual(self.smoke(runner), (False, f"job {job} did not finish"))
        sleep.assert_called_once_with(3)
        self.finished(job, "0", "I cannot do arithmetic")
        with mock.patch.object(resign.secrets, "randbelow", return_value=0):
            self.assertEqual(self.smoke(runner),
                             (False, f"job {job} exited 0 without the expected answer 200"))
        (self.home / "jobs" / job / "exit").write_text("1\n")
        (self.home / "jobs" / job / "out.txt").write_text("200")
        with mock.patch.object(resign.secrets, "randbelow", return_value=0):
            self.assertEqual(self.smoke(runner),
                             (False, f"job {job} exited 1 without the expected answer 200"))


class OperatorActionTests(HomeFixture):
    def setUp(self):
        super().setUp()
        self.cli = self.base / "codex"
        self.cli.write_text("#!/bin/sh\nexit 0\n")
        self.live = self.base / "overlay.json"
        self.overlay = {"evidence": [{"vendor": "codex", "path": str(self.cli), "sha256": "0" * 64},
                                     {"vendor": "codex", "path": "/r/run-codex.sh", "sha256": "1" * 64}]}
        self.original = json.dumps(self.overlay)
        self.live.write_text(self.original)
        self.report = {vendor: {"cli": None, "cli_changed": False, "recorded_codesign": None,
                                "recorded_cli_path": None} for vendor in resign.VENDORS}
        self.report["codex"].update(cli=str(self.cli), recorded_cli_path=str(self.cli))

    def args(self, **changes):
        value = dict(trust_origin=None, trust_location=None, revoke_origin=None, team=None)
        value.update(changes)
        return SimpleNamespace(**value)

    def test_record_signers_rolls_back_an_overlay_that_stops_loading(self):
        with mock.patch.object(resign, "verified", side_effect=aa_policy.PolicyError("broken")):
            code = resign.record_signers(self.live, self.overlay, self.report, ["codex"], self.log)
        self.assertEqual(code, resign.EXIT_ROLLED_BACK)
        self.assertEqual(self.live.read_text(), self.original)
        self.assertIn("the overlay stopped loading (broken); restored", self.logs[-1])
        backups = list(self.base.glob("overlay.json.before-record-signers-*"))
        self.assertEqual(len(backups), 1)

    def test_record_signers_skips_drifted_vendors_and_records_the_rest(self):
        self.report["codex"]["cli_changed"] = True
        self.assertEqual(resign.record_signers(self.live, self.overlay, self.report, ["codex"], self.log),
                         resign.EXIT_OK)
        self.assertIn("drifted, so its signer is not adopted", self.logs[0])
        self.assertIn("already has its signer recorded", self.logs[-1])
        self.report["codex"]["cli_changed"] = False
        with mock.patch.object(resign, "verified"):
            self.assertEqual(resign.record_signers(self.live, self.overlay, self.report, ["codex"],
                                                   self.log), resign.EXIT_OK)
        saved = json.loads(self.live.read_text())
        self.assertEqual(saved["evidence"][0]["codesign"]["kind"], "script")
        self.assertIn("recorded signers codex=", self.logs[-1])

    def test_trust_adhoc_needs_a_recorded_signer_and_rolls_back_on_failure(self):
        self.assertEqual(resign.trust_adhoc(self.live, self.overlay, self.report, ["codex"], self.log),
                         resign.EXIT_OK)
        self.assertIn("has no recorded signer", self.logs[0])
        self.assertIn("nothing to record", self.logs[-1])
        self.overlay["evidence"][0]["codesign"] = {"kind": "adhoc", "signer": "adhoc"}
        with mock.patch.object(resign, "verified", side_effect=OSError("gone")):
            code = resign.trust_adhoc(self.live, self.overlay, self.report, ["codex"], self.log)
        self.assertEqual(code, resign.EXIT_ROLLED_BACK)
        self.assertEqual(self.live.read_text(), self.original)
        self.assertFalse((self.home / "transport-origins.local.json").exists())

    def test_trust_adhoc_refuses_a_damaged_origin_policy(self):
        self.overlay["evidence"][0]["codesign"] = {"kind": "adhoc", "signer": "adhoc"}
        (self.home / "transport-origins.local.json").write_text("{not json")
        code = resign.trust_adhoc(self.live, self.overlay, self.report, ["codex"], self.log)
        self.assertEqual(code, resign.EXIT_OPERATOR)
        self.assertIn("origin policy rejected", self.logs[-1])
        self.assertEqual(self.live.read_text(), self.original)

    def test_origin_action_refusals_never_run_the_executable(self):
        self.assertEqual(resign.origin_action(self.args(trust_origin="nobody"), self.report, self.log),
                         resign.EXIT_OPERATOR)
        self.assertEqual(self.logs[-1], "omnilane: unknown origin vendor; nothing changed")
        cases = [
            (self.args(trust_origin="claude"), "CLI is not installed"),
            (self.args(trust_origin="codex"), "--trust-origin requires an interactive terminal"),
            (self.args(trust_location="claude"), "CLI is not installed"),
        ]
        for args, reason in cases:
            with self.subTest(reason=reason), \
                    mock.patch.object(resign.sys, "stdin", io.StringIO()):
                self.assertEqual(resign.origin_action(args, self.report, self.log), resign.EXIT_OPERATOR)
                self.assertEqual(self.logs[-1],
                                 f"omnilane: origin command refused: {reason}; no executable was run")
        self.report["codex"]["cli_changed"] = True
        self.assertEqual(resign.origin_action(self.args(trust_location="codex"), self.report, self.log),
                         resign.EXIT_OPERATOR)
        self.assertIn("trust-location requires the unchanged pinned script", self.logs[-1])
        self.assertFalse((self.home / "transport-origins.local.json").exists())

    def test_trust_origin_on_a_terminal_still_needs_a_verified_sample_and_confirmation(self):
        tty = mock.Mock()
        tty.isatty.return_value = True
        facts = {"kind": "developer-id", "valid": True, "signer": "ABCDE12345", "identifier": "com.x.codex"}
        cases = [
            ({"kind": "script", "valid": False}, None, None,
             "trust-origin requires a verified Developer ID or adhoc sample"),
            ({"kind": "adhoc", "valid": True, "signer": "other"}, None, None, "invalid adhoc facts"),
            ({"kind": "adhoc", "valid": True, "signer": "adhoc"}, None, None,
             "adhoc samples require an explicit ten-character --team"),
            (dict(facts, identifier=""), None, None, "verified sample has no usable product identifier"),
            (facts, "ZZZZZ99999", None, "--team differs from the verified sample"),
            (facts, None, "TRUST codex WRONG", "explicit confirmation not received"),
        ]
        for current, team, answer, reason in cases:
            with self.subTest(reason=reason), mock.patch.object(resign.sys, "stdin", tty), \
                    mock.patch.object(resign.cli_provenance, "facts", return_value=current), \
                    mock.patch("builtins.input", return_value=answer or ""):
                code = resign.origin_action(self.args(trust_origin="codex", team=team), self.report, self.log)
                self.assertEqual(code, resign.EXIT_OPERATOR)
                self.assertEqual(self.logs[-1],
                                 f"omnilane: origin command refused: {reason}; no executable was run")
        with mock.patch.object(resign.sys, "stdin", tty), \
                mock.patch.object(resign.cli_provenance, "facts", return_value=facts), \
                mock.patch("builtins.input", side_effect=EOFError):
            self.assertEqual(resign.origin_action(self.args(trust_origin="codex"), self.report, self.log),
                             resign.EXIT_OPERATOR)
        self.assertFalse((self.home / "transport-origins.local.json").exists())


class MainTests(HomeFixture):
    def run_main(self, *argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = resign.main(list(argv))
        return code, stderr.getvalue()

    def test_parser_combinations_are_usage_errors(self):
        for argv in (["--team", "ABCDE12345"], ["--trust-location", "codex", "--team", "ABCDE12345"],
                     ["--trust-origin", "codex", "--check"], ["--revoke-origin", "codex", "location",
                                                              "--approve", "codex"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                self.run_main(*argv)
            self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.home / "verification-status.json").exists())

    def test_daily_usage_error_is_recorded_as_unresolved(self):
        code, err = self.run_main("--daily", "--team", "ABCDE12345")
        self.assertEqual(code, 2)
        status = json.loads((self.home / "verification-status.json").read_text())
        self.assertNotIn("running", status)
        self.assertIn("automatic verification did not complete", err)

    def test_daily_refuses_no_smoke_and_records_it(self):
        with mock.patch.object(resign, "resign") as run:
            code, err = self.run_main("--daily", "--no-smoke")
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn("--daily requires smoke verification; remove --no-smoke", err)
        self.assertTrue((self.home / "verification-status.json").is_file())

    def test_daily_success_with_unprobed_gaps_still_needs_an_operator(self):
        def fake(args, log):
            args.status_summary = {"still_unprobed": {"codex": [CODEX_ROW]}}
            return resign.EXIT_OK

        with mock.patch.object(resign, "resign", side_effect=fake):
            code, _ = self.run_main("--daily", "--vendor", "codex")
        self.assertEqual(code, resign.EXIT_OPERATOR)
        with mock.patch.object(resign, "resign", side_effect=fake):
            code, _ = self.run_main("--daily", "--vendor", "claude")
        self.assertEqual(code, resign.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
