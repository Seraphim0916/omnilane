"""Offline P1B contracts and adversarial false-allow regression cases."""

from __future__ import annotations
import offline_env  # noqa: F401
import copy
import json
import os
import signal
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import test_resign as existing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy
import build_overlay
import probe
import probe_failure
import probe_sweep
import resign
import verification_status

ABSENT = object()


class RetryFixture:
    descriptor = existing.ResignTests.descriptor
    build = existing.ResignTests.build
    vendor_of = existing.ResignTests.vendor_of
    facts = existing.ResignTests.facts
    update = existing.ResignTests.update

    def setUp(self):
        existing.ResignTests.setUp(self)
        self.calls = []
        self.behavior = {}
        self.hook = None
        self.env = patch.dict(os.environ, {"OMNILANE_PROBE_ANY_SESSION": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_probe(self, root, entry, kind, stamp=ABSENT, vendor="claude"):
        evidence = root / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        token = probe_sweep.TOKENS[vendor]
        response = {"modelUsage": {entry["model"]: {}}, "result": token}
        stderr, exit_code = "", 0
        if kind in ("api", "quota", "login"):
            response.update(is_error=True, result={"api": "API error: temporarily unavailable",
                                                   "quota": "quota exhausted",
                                                   "login": "not logged in"}[kind])
            exit_code = 1
        elif kind == "wrong-model":
            response["modelUsage"] = {"some-other-model": {}}
        elif kind == "wrong-effort":
            stderr = "unknown --effort; using default"
        elif kind == "missing-token":
            response["result"] = "not the expected response"
        elif kind == "nonzero":
            exit_code = 1
        stdout = json.dumps(response)
        if vendor == "codex" and kind == "api":
            stdout = json.dumps({"type": "turn.failed", "error": {"message": "API error: temporarily unavailable"}})
        if kind == "plain-login":
            stdout, stderr, exit_code = "", "not logged in", 1
        record = dict(command=probe_sweep.command(vendor, entry["model"], entry["effort"]),
                      exit_code=exit_code, timed_out=kind == "timeout",
                      probed_at=datetime.now(timezone.utc).isoformat(),
                      stdout=str(evidence / f"{entry['name']}.stdout"),
                      stderr=str(evidence / f"{entry['name']}.stderr"))
        verdict = probe.verdict(record, stdout, stderr, vendor, token)
        record.update(zip(("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict))
        if stamp is not ABSENT:
            record["last_failed_at"] = stamp
        for suffix, content in (("stdout", stdout), ("stderr", stderr), ("json", json.dumps(record))):
            (evidence / f"{entry['name']}.{suffix}").write_text(content)
        return record

    def fail_row(self, kind="api", *, index=-1, stamp=ABSENT):
        entry = probe_sweep.plan("claude")[index]
        self.write_probe(self.sweep_root, entry, kind, stamp)
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        return entry

    def prober(self, name, argv, *, root, vendor, **kwargs):
        entry = next(e for e in probe_sweep.plan(vendor) if e["name"] == name)
        self.calls.append(entry["config_id"])
        record = self.write_probe(root, entry, self.behavior.get(entry["config_id"], "pass"), vendor=vendor)
        if self.hook:
            self.hook(root, entry, record)
        return record

    def run_retry(self, *, interactive=False, smoke=None, **flags):
        self.args = Namespace(check=False, vendor=["claude"], approve=None, trust_adhoc=None,
                              no_smoke=False, json=False, record_signers=False, allow_shrink=False,
                              retry_failed=False)
        for key, value in flags.items():
            setattr(self.args, key, value)
        self.lines = []
        self.smoke = smoke or Mock(return_value=(True, "offline smoke"))
        with patch.object(probe_sweep.probe_module, "probe", self.prober), \
                patch.object(resign, "canary", return_value=(True, "offline version")), \
                patch.object(resign, "smoke", self.smoke), \
                patch.object(sys.stdin, "isatty", return_value=interactive), patch("builtins.print"):
            return resign.resign(self.args, log=self.lines.append)

    def overlay(self):
        return json.loads(self.live.read_text())

    def change_overlay(self, transform):
        overlay = self.overlay()
        transform(overlay)
        self.live.write_text(json.dumps(overlay))

    def failure(self, entry):
        return next(row for row in self.overlay()["unproven"] if row["config_id"] == entry["config_id"])

    def decision(self, entry, ceiling=100):
        registry, digest = aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
        caller = max(registry["scored_configs"], key=lambda row: row["score"])
        return aa_policy.decide(registry, digest, vendor="claude", model=entry["model"],
                                effort=entry["effort"], target_config=entry["config_id"],
                                caller={"caller": aa_policy._row_identity(caller),
                                        "inherited_ceiling": ceiling}, caller_sha256="offline")

    def assert_unverified(self, entry):
        self.assertNotIn(entry["config_id"], {row["config_id"] for row in self.overlay()["mappings"]})
        self.assertFalse(self.decision(entry)["allowed"])


class RetryBehaviorTests(RetryFixture, unittest.TestCase):
    def test_a_timeout_does_not_repeat_inside_an_unattended_sweep(self):
        entry = self.fail_row()
        self.behavior[entry["config_id"]] = "timeout"
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [entry["config_id"]])
        self.assertIn("cooldown prevents another call", "\n".join(self.lines))
        self.assert_unverified(entry)

    def test_first_login_failure_of_a_missing_row_also_starts_cooldown(self):
        entry = probe_sweep.plan("claude")[-1]
        path = self.sweep_root / "evidence" / f"{entry['name']}.json"
        path.rename(path.with_suffix(".not-probed"))
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        self.behavior[entry["config_id"]] = "plain-login"
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertIn("last_failed_at", self.failure(entry))
        before = len(self.calls)
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(len(self.calls), before)
        self.assert_unverified(entry)

    def test_five_haiku_configurations_retry_without_reprobing_other_rows(self):
        entries = [entry for entry in probe_sweep.plan("claude") if entry["model"] == "claude-haiku-5-5"]
        self.assertEqual(len(entries), 5)
        for entry in entries:
            self.write_probe(self.sweep_root, entry, "api")
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(self.calls, [entry["config_id"] for entry in entries])
        for entry in entries:
            self.assertTrue(self.decision(entry)["allowed"])

    def test_failed_vendor_without_any_verified_rows_keeps_failure_times(self):
        entries = probe_sweep.plan("claude")
        for entry in entries:
            self.write_probe(self.sweep_root, entry, "api")
            self.behavior[entry["config_id"]] = "api"
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        self.assertEqual(self.run_retry(), 20, self.lines)
        for entry in entries:
            self.assert_unverified(entry)
            self.assertIn("last_failed_at", self.failure(entry))
        self.smoke.assert_not_called()

    def test_check_reports_environment_drift_without_writing(self):
        entry = self.fail_row()
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(check=True), resign.EXIT_DRIFT)
        self.assertEqual(before, self.live.read_bytes())
        self.assertEqual(self.calls, [])
        self.assertIn("1 configuration(s) failed their last probe for an environment reason (api-error): "
                      + entry["config_id"], "\n".join(self.lines))

    def test_legacy_failure_retries_only_its_row_without_approval(self):
        entry = self.fail_row()
        original = {row["config_id"] for row in self.overlay()["mappings"]}
        self.assertNotIn("last_failed_at", self.failure(entry))
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(self.calls, [entry["config_id"]])
        self.assertEqual({row["config_id"] for row in self.overlay()["mappings"]}, original | {entry["config_id"]})
        self.assertTrue(self.decision(entry)["allowed"])

    def test_failure_updates_timestamp_and_defers_next_unattended_run(self):
        entry = self.fail_row()
        self.behavior[entry["config_id"]] = "api"
        self.assertEqual(self.run_retry(), 20, self.lines)
        first = self.failure(entry)["last_failed_at"]
        self.assertLess(datetime.now(timezone.utc) - datetime.fromisoformat(first), timedelta(minutes=1))
        before, calls = self.live.read_bytes(), len(self.calls)
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(before, self.live.read_bytes())
        self.assertIn("20-hour", "\n".join(self.lines))

    def test_interactive_execution_ignores_cooldown(self):
        entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        self.assertEqual(self.run_retry(interactive=True), 0, self.lines)
        self.assertEqual(self.calls, [entry["config_id"]])

    def test_explicit_retry_ignores_cooldown(self):
        entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        self.assertEqual(self.run_retry(retry_failed=True), 0, self.lines)
        self.assertEqual(self.calls, [entry["config_id"]])

    def test_old_failure_is_eligible_unattended(self):
        entry = self.fail_row(stamp=(datetime.now(timezone.utc) - timedelta(hours=21)).isoformat())
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(self.calls, [entry["config_id"]])

    def test_cooldown_does_not_block_a_different_failed_row(self):
        cooled = self.fail_row(index=-1, stamp=datetime.now(timezone.utc).isoformat())
        ready = self.fail_row(index=-2)
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [ready["config_id"]])
        self.assert_unverified(cooled)
        self.assertTrue(self.decision(ready)["allowed"])

    def test_vendor_filter_does_not_retry_an_unselected_vendor(self):
        entry = self.fail_row()
        self.assertEqual(self.run_retry(vendor=["codex"]), 0)
        self.assertEqual(self.calls, [])
        self.assert_unverified(entry)

    def test_dispatch_message_explains_environment_retry(self):
        entry = self.fail_row()
        decision = self.decision(entry)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["next_command"], "omnilane resign --retry-failed --vendor claude")
        self.assertIn("environment reason", decision["message"])
        self.assertIn("上次探測因環境原因失敗", decision["message_zh_tw"])

    def test_parser_retry_flag_does_not_set_approval(self):
        with patch.object(resign, "resign", return_value=0) as run:
            self.assertEqual(resign.main(["--retry-failed", "--vendor", "claude"]), 0)
        self.assertTrue(run.call_args.args[0].retry_failed)
        self.assertIsNone(run.call_args.args[0].approve)

    def test_daily_deferred_failure_remains_visible(self):
        entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        code = self.run_retry()
        verification_status.record(code, ["claude"], self.args.status_summary)
        self.assertEqual(verification_status.load()["pending"]["claude"]["config_ids"], [entry["config_id"]])

    def test_partial_login_failure_retains_attempt_and_unattempted_failures(self):
        first = self.fail_row(index=0)
        last = self.fail_row(index=-1)
        self.behavior[first["config_id"]] = "login"
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [first["config_id"]])
        self.assertIn("last_failed_at", self.failure(first))
        self.assertNotIn("last_failed_at", self.failure(last))
        self.assert_unverified(first)
        self.assert_unverified(last)


class FalseAllowTests(RetryFixture, unittest.TestCase):
    def test_false_allow_stale_client_record_cannot_upgrade_fresh_evidence(self):
        entry = probe_sweep.plan("codex")[-1]
        self.write_probe(self.sweep_root, entry, "api", vendor="codex")
        (self.sweep_root / "evidence" / f"{entry['name']}.rollout").write_text("model\t" + entry["model"] + "\n")
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        self.assertEqual(self.run_retry(vendor=["codex"]), 0, self.lines)
        mapping = next(m for m in self.overlay()["mappings"] if m["config_id"] == entry["config_id"])
        self.assertEqual(mapping["evidence_tier"], "selector-only")
        self.assertNotIn("rollout", mapping["evidence_refs"])
        self.assertEqual(self.calls, [entry["config_id"]])

    def test_false_allow_cooldown_still_applies_when_executable_changes(self):
        entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        self.update("claude")
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertNotIn(entry["config_id"], self.calls)
        self.assert_unverified(entry)

    def test_false_allow_failure_timestamp_removed_from_overlay(self):
        entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        self.change_overlay(lambda overlay: next(row for row in overlay["unproven"]
                                                if row["config_id"] == entry["config_id"]).pop("last_failed_at"))
        self.assertEqual(self.run_retry(), 40)
        self.assertIn("failure timestamp disagrees", "\n".join(self.lines))
        self.assertEqual(self.calls, [])
        self.assert_unverified(entry)

    def test_false_allow_retry_flag_never_approves_changed_signer(self):
        entry = self.fail_row()
        self.update("claude")
        self.signers["claude"] = "UNTRUSTED_TEAM"
        self.assertEqual(self.run_retry(retry_failed=True), 20, self.lines)
        self.assertEqual(self.calls, [])
        self.assert_unverified(entry)

    def test_false_allow_low_ceiling_still_refuses_a_passing_retry(self):
        entry = self.fail_row()
        self.assertEqual(self.run_retry(), 0)
        self.assertTrue(self.decision(entry)["allowed"])
        self.assertFalse(self.decision(entry, ceiling=0)["allowed"])

    def test_false_allow_smoke_failure_restores_exact_overlay(self):
        entry = self.fail_row()
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(smoke=Mock(return_value=(False, "offline failure"))), 30)
        self.assertEqual(self.live.read_bytes(), before)
        self.assert_unverified(entry)

    def test_false_allow_verified_evidence_tampering_is_refused_before_retry(self):
        entry = self.fail_row()
        other = probe_sweep.plan("claude")[0]
        (self.sweep_root / "evidence" / f"{other['name']}.stdout").write_text("tampered")
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(retry_failed=True), 40)
        self.assertEqual(self.calls, [])
        self.assertEqual(before, self.live.read_bytes())

    def test_false_allow_conclusive_row_with_retry_flag_is_not_called(self):
        entry = self.fail_row("wrong-model")
        self.assertEqual(self.run_retry(retry_failed=True), 0)
        self.assertEqual(self.calls, [])
        self.assert_unverified(entry)

    def test_false_allow_legacy_failure_can_fail_again_without_becoming_verified(self):
        entry = self.fail_row()
        self.behavior[entry["config_id"]] = "api"
        self.assertEqual(self.run_retry(), 20)
        self.assert_unverified(entry)
        self.assertIn("last_failed_at", self.failure(entry))


def failed_retry_case(kind):
    def test(self):
        entry = self.fail_row()
        self.behavior[entry["config_id"]] = kind
        self.assertEqual(self.run_retry(retry_failed=True), 20, self.lines)
        self.assert_unverified(entry)
        self.assertIn("last_failed_at", self.failure(entry))
    return test


for _kind in ("api", "quota", "timeout", "login", "plain-login", "wrong-model", "wrong-effort", "missing-token", "nonzero"):
    setattr(FalseAllowTests, "test_false_allow_retry_" + _kind.replace("-", "_"), failed_retry_case(_kind))


def invalid_timestamp_case(value):
    def test(self):
        entry = self.fail_row(stamp=value)
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [])
        self.assertEqual(before, self.live.read_bytes())
        self.assert_unverified(entry)
    return test


for _name, _stamp in (("invalid", "not-a-date"), ("naive", "2026-01-01T00:00:00"),
                      ("future", "2999-01-01T00:00:00+00:00"), ("null", None), ("number", 0)):
    setattr(FalseAllowTests, "test_false_allow_timestamp_" + _name, invalid_timestamp_case(_stamp))


def tampered_hint_case(kind):
    def test(self):
        entry = self.fail_row("wrong-model" if kind.startswith("reason") else "api",
                              stamp=datetime.now(timezone.utc).isoformat())
        overlay = self.overlay()
        row = next(row for row in overlay["unproven"] if row["config_id"] == entry["config_id"])
        if kind.startswith("reason"):
            row["verdict_reason"] = "api-error"
            if kind == "reason-and-descriptor":
                path = self.sweep_root / "evidence" / f"{entry['name']}.json"
                record = json.loads(path.read_text())
                record["verdict_reason"] = "api-error"
                path.write_text(json.dumps(record))
        elif kind == "timestamp":
            row["last_failed_at"] = "2000-01-01T00:00:00+00:00"
        elif kind.startswith("duplicate"):
            duplicate = copy.deepcopy(row)
            if kind == "duplicate-conflict":
                duplicate["verdict_reason"] = "model-mismatch"
            overlay["unproven"].append(duplicate)
        self.live.write_text(json.dumps(overlay))
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(retry_failed=True), 40, self.lines)
        self.assertEqual(self.calls, [])
        self.assertEqual(before, self.live.read_bytes())
        if kind == "reason-and-descriptor":
            with self.assertRaisesRegex(aa_policy.PolicyError, "failed probe evidence changed"):
                self.decision(entry)
        else:
            self.assert_unverified(entry)
    return test


for _kind in ("reason", "reason-and-descriptor", "timestamp", "duplicate", "duplicate-conflict"):
    setattr(FalseAllowTests, "test_false_allow_tampered_" + _kind.replace("-", "_"), tampered_hint_case(_kind))


def interrupted_case(where, error):
    def test(self):
        entry = self.fail_row()
        before = self.live.read_bytes()
        def interrupt(*args):
            raise error("offline interruption")
        if where == "probe":
            self.hook = interrupt
        with self.assertRaises(error):
            self.run_retry(smoke=Mock(side_effect=interrupt) if where == "smoke" else None)
        self.assertEqual(before, self.live.read_bytes())
        self.assert_unverified(entry)
    return test


for _where in ("probe", "smoke"):
    for _error in (KeyboardInterrupt, OSError):
        setattr(FalseAllowTests, f"test_false_allow_{_where}_{_error.__name__}", interrupted_case(_where, _error))


def killed_smoke_case(signum):
    @unittest.skipUnless(hasattr(os, "fork"), "requires a child process for a real termination signal")
    def test(self):
        entry = self.fail_row()
        before = self.live.read_bytes()
        pid = os.fork()
        if pid == 0:
            def terminate(*args):
                os.kill(os.getpid(), signum)
            self.run_retry(smoke=Mock(side_effect=terminate))
            os._exit(99)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signum)
        self.assertEqual(before, self.live.read_bytes())
        self.assert_unverified(entry)
    return test


for _signum in (signal.SIGTERM, signal.SIGKILL):
    setattr(FalseAllowTests, f"test_false_allow_smoke_signal_{_signum.name}", killed_smoke_case(_signum))


def partial_proof_case(kind):
    def test(self):
        entry = self.fail_row()
        before = self.live.read_bytes()
        def corrupt(root, current, record):
            descriptor = root / "evidence" / f"{current['name']}.json"
            value = json.loads(descriptor.read_text())
            if kind == "missing-verdict":
                value.pop("verdict")
            elif kind == "wrong-command":
                value["command"].append("--effort=wrong")
            elif kind == "fake-pass":
                path = root / "evidence" / f"{current['name']}.stdout"
                response = json.loads(path.read_text())
                response["modelUsage"] = {"wrong-model": {}}
                path.write_text(json.dumps(response))
            elif kind == "missing-stream":
                path = root / "evidence" / f"{current['name']}.stdout"
                path.rename(path.with_suffix(".incomplete"))
            descriptor.write_text(json.dumps(value))
        self.hook = corrupt
        self.assertEqual(self.run_retry(), 30, self.lines)
        self.assertEqual(before, self.live.read_bytes())
        self.assert_unverified(entry)
    return test


for _kind in ("missing-verdict", "wrong-command", "fake-pass", "missing-stream"):
    setattr(FalseAllowTests, "test_false_allow_partial_" + _kind.replace("-", "_"), partial_proof_case(_kind))


def malformed_report_case(kind):
    def test(self):
        entry = self.fail_row()
        before = self.live.read_bytes()
        sweep = probe_sweep.sweep
        def corrupt(*args, **kwargs):
            result = sweep(*args, **kwargs)
            if kind == "duplicate":
                result["passed"] *= 2
            else:
                result["passed"] = []
            return result
        with patch.object(probe_sweep, "sweep", corrupt):
            self.assertEqual(self.run_retry(), 30, self.lines)
        self.assertEqual(before, self.live.read_bytes())
        self.assert_unverified(entry)
    return test


for _kind in ("duplicate", "unreported-pass"):
    setattr(FalseAllowTests, "test_false_allow_report_" + _kind.replace("-", "_"), malformed_report_case(_kind))


class ClassificationTests(unittest.TestCase):
    def test_cooldown_boundary_is_exactly_twenty_hours(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.assertFalse(probe_failure.cooldown((now - timedelta(hours=20)).isoformat(), now=now)[0])
        self.assertTrue(probe_failure.cooldown((now - timedelta(hours=20, seconds=-1)).isoformat(), now=now)[0])
        self.assertFalse(probe_failure.cooldown(None, now=now)[0])

    def test_environment_whitelist_does_not_search_inside_conclusive_reasons(self):
        for reason in ("timeout", "api-error: down", "quota-exhausted: limit",
                       "exit-code: 1: connection refused", "result-error: unauthorized",
                       "unexpected-stderr: network unreachable"):
            self.assertIsNotNone(probe_failure.environment_reason(reason), reason)
        for reason in ("model-mismatch: API error", "effort-silently-defaulted: quota",
                       "client-record-mismatch: timeout", "missing-requested-model",
                       "missing-model-usage", "missing-expected-token", "invalid-json", "unsupported-vendor: api-error",
                       "exit-code: 1: unknown model", "verdict: fail", None, 3):
            self.assertIsNone(probe_failure.environment_reason(reason), reason)


if __name__ == "__main__":
    unittest.main()
