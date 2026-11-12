"""Offline user-facing refusal and maintenance guidance contracts."""
import offline_env
import os
import contextlib
import copy
import io
import json
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import refusal_evidence
import resign
import verification_status
import build_overlay
import aa_policy
import caller_identity
import native

PRIVATE_FIXTURE = "/tmp/alice@example.invalid sk-syntheticToken012345678901234567 org-synthetic-account"


def assert_private_line(case, line):
    for value in ("/tmp/", "alice@example.invalid", "sk-syntheticToken012345678901234567",
                  "org-synthetic-account"):
        case.assertNotIn(value, line)
    case.assertIn("Next:", line)
    case.assertLessEqual(len(line), 600)
    case.assertEqual(line, " ".join(line.split()))


class GuidanceTests(unittest.TestCase):
    def test_human_redaction_keeps_operator_flags_and_covers_identifier_families(self):
        for secret in ("organisation id example-org", "account_id=example-account", "Bearer fixture-secret",
                       "api_key=fixture-secret", "acct-fixture-private", "abcdef0123456789abcdef0123456789"):
            with self.subTest(secret=secret):
                line = refusal_evidence.human_line(dict(reason=secret,
                    next_command="a human operator adds --operator-asserted-human"))
                self.assertNotIn(secret, line)
                self.assertIn("[redacted]", line)
                self.assertIn("--operator-asserted-human", line)
        for workdir in ("'/tmp/a private dir'", '"/tmp/a private dir"', "/tmp/alice@example.invalid"):
            line = refusal_evidence.human_line(dict(next_command="omnilane native-context --workdir=" + workdir))
            self.assertIn("--workdir <your project dir>", line)
            self.assertNotIn("/tmp/", line)
        self.assertLessEqual(len(refusal_evidence.human_line(dict(reason="\ud800" * 200))), 600)

    def test_entire_human_line_is_private_bounded_and_does_not_mutate_json(self):
        decision = dict(allowed=False, code="deny", failed_gate=PRIVATE_FIXTURE,
                        reason=PRIVATE_FIXTURE + "\n\x1b[31maccount_id=fixture-private-account",
                        next_command="omnilane native-context --workdir " + PRIVATE_FIXTURE)
        before = json.dumps(decision)
        line = refusal_evidence.human_line(decision)
        assert_private_line(self, line)
        self.assertNotIn("fixture-private-account", line)
        self.assertNotIn("\x1b", line)
        self.assertIn("--workdir <your project dir>", line)
        self.assertEqual(json.dumps(decision), before)
        decision["reason"] = "word " * 500
        self.assertLessEqual(len(refusal_evidence.human_line(decision)), 600)
        summary = refusal_evidence.summary(json.loads(before), None)
        command = summary.split('next_command="', 1)[1].split('"', 1)[0]
        for value in ("alice@example.invalid", "sk-syntheticToken", "fixture-private-account"):
            self.assertNotIn(value, command)

    def test_all_native_refusal_renderers_preserve_json_and_redact_human_line(self):
        for route in ("inherited-policy", "inherited-capability", "policy"):
            for json_only in (False, True):
                with self.subTest(route=route, json_only=json_only):
                    args = SimpleNamespace(caller_context=None, operator_asserted_human=False,
                        workdir="/tmp/alice@example.invalid", context=None, policy="fixture",
                        expected_registry_sha256=None, vendor="codex", model="fixture", effort="high",
                        target_config=None, executor="auto")
                    decision = dict(allowed=route == "inherited-capability", code="fixture-denied",
                        failed_gate="fixture", reason=PRIVATE_FIXTURE, message=PRIVATE_FIXTURE,
                        next_command="omnilane native-context --workdir " + args.workdir)
                    err = io.StringIO()
                    with patch.dict(os.environ, OMNILANE_REFUSAL_JSON_ONLY="1" if json_only else "0"), \
                            patch.object(aa_policy, "decide_inherited", return_value=decision), \
                            patch.object(aa_policy, "decide", return_value=decision), \
                            patch.object(aa_policy, "load_registry", return_value=({}, "fixture")), \
                            patch.object(native, "canonical_dir", side_effect=lambda path: path), \
                            patch.object(native, "choose_inherited", return_value=(None, PRIVATE_FIXTURE)), \
                            contextlib.redirect_stderr(err):
                        code = (native.route(args) if route == "policy" else
                                native.route_inherited(args, {"vendor": "codex", "current_model": "fixture"}, {}, "fixture"))
                    self.assertEqual(code, 3)
                    raw = aa_policy._json_line(decision)
                    if json_only:
                        self.assertEqual(err.getvalue(), raw)
                    else:
                        human, remainder = err.getvalue().split("\n", 1)
                        assert_private_line(self, human)
                        self.assertIn("--workdir <your project dir>", human)
                        self.assertEqual(remainder, raw)

    def test_both_whoami_refusal_paths_redact_first_line_and_keep_legacy_bytes(self):
        for failure in ("lookup", "unscored"):
            outputs = {}
            for json_only in (False, True):
                err = io.StringIO()
                with patch.dict(os.environ, OMNILANE_REFUSAL_JSON_ONLY="1" if json_only else "0"), \
                        patch.object(caller_identity, "load_registry", return_value=({}, "fixture")), \
                        patch.object(caller_identity, "read_caller", return_value=(123, ("codex", PRIVATE_FIXTURE, "high"), ""),
                            side_effect=ValueError(PRIVATE_FIXTURE) if failure == "lookup" else None), \
                        patch.object(caller_identity, "resolve", return_value=(None, caller_identity.NO_ROW + PRIVATE_FIXTURE)), \
                        contextlib.redirect_stderr(err):
                    self.assertEqual(caller_identity.main([]), 3)
                outputs[json_only] = err.getvalue()
            human, remainder = outputs[False].split("\n", 1)
            assert_private_line(self, human)
            self.assertEqual(remainder, outputs[True])

    def test_missing_identity_causes_have_distinct_next_steps(self):
        cases = [
            ("no vendor CLI among this process's ancestors", "--operator-asserted-human"),
            ("codex direct child CODEX_THREAD_ID is missing; run the omnilane command as the only command", "only command"),
            ("no scored configuration for codex new-model at effort high", "--inherit"),
        ]
        for diagnostic, expected in cases:
            with self.subTest(diagnostic=diagnostic):
                guidance = refusal_evidence.caller_guidance(diagnostic)
                self.assertIn(expected, guidance["next_command"])
        unscored = refusal_evidence.caller_guidance(cases[-1][0])
        self.assertIn("approved score table", unscored["reason"])
        self.assertIn("by design", unscored["reason"])
        self.assertNotIn("whoami", str(unscored))

    def test_human_line_and_summary_are_single_line(self):
        decision = dict(allowed=False, code="missing-caller-context", failed_gate="caller-identity",
                        reason="missing\nidentity", next_command="run\ncommand")
        self.assertEqual(refusal_evidence.human_line(decision),
                         "omnilane: refused (caller-identity): missing identity. Next: run command")
        self.assertIn('next_command="run command"', refusal_evidence.summary(decision, None))
        with patch.dict(os.environ, OMNILANE_REFUSAL_JSON_ONLY="1"):
            self.assertNotIn("next_command=", refusal_evidence.summary(decision, None))

    def test_whoami_missing_launch_flags_name_the_cause_and_preserve_detail(self):
        for selector, expected in ((('claude', 'claude-opus-5', None), '--effort'),
                                   (('claude', None, None), '--model'),
                                   (('codex', 'unscored-fixture', None), 'model_reasoning_effort')):
            outputs = {}
            for json_only in (False, True):
                err = io.StringIO()
                with patch.dict(os.environ, OMNILANE_REFUSAL_JSON_ONLY="1" if json_only else "0"), \
                        patch.object(caller_identity, "read_caller", return_value=(123, selector, "")), \
                        contextlib.redirect_stderr(err):
                    self.assertEqual(caller_identity.main([]), 3)
                outputs[json_only] = err.getvalue()
            human, detail = outputs[False].split("\n", 1)
            self.assertIn("launcher was found", human)
            self.assertIn(expected, human)
            self.assertIn(expected, human.split("Next: ", 1)[1])
            self.assertEqual(detail, outputs[True])
            self.assertIn("cannot read the caller identity from pid 123", detail)
        unknown = refusal_evidence.caller_guidance("cannot query caller process (errno 1)")
        self.assertIn("cannot query caller process", unknown["reason"])
        self.assertNotEqual(unknown["next_command"], "omnilane whoami")

    def test_attention_uses_daily_reason_and_operator_instruction(self):
        summary = {"vendors": {"claude": {"cli": "/fake/claude", "drifted": True,
                   "gate": {"allowed": False}, "origin_reason": "team-changed",
                   "codesign": {"kind": "developer-id"}}}, "held": ["claude"]}
        attention = verification_status.attention(20, summary)
        self.assertEqual(attention[0]["reason"], "review-executable-source")
        self.assertIn("human operator", attention[0]["next_command"])
        self.assertIn("terminal", attention[0]["next_command"])

    def test_local_overlay_assignment_is_idempotent(self):
        original = "# keep this\nexport OTHER=ok\nexport OMNILANE_AA_TRANSPORT_OVERLAY=old; export ALSO_KEEP=yes\n"
        path = Path("/tmp/with a quote's/overlay.json")
        text = resign.overlay_configuration(original, path)
        self.assertEqual(resign.overlay_configuration(text, path), text)
        self.assertIn("export OTHER=ok", text)
        self.assertTrue(text.startswith(original))
        self.assertEqual(text.count("export OMNILANE_AA_TRANSPORT_OVERLAY="), 2)

    def test_guidance_never_allows_an_unscored_caller(self):
        for diagnostic in ("no scored configuration for codex future-model at effort high",
                           "no vendor CLI among this process's ancestors"):
            decision = aa_policy.decide(build_overlay.REGISTRY, "fixture", vendor="codex", model="future",
                                        effort="high", caller=None, caller_sha256=None,
                                        caller_diagnostic=diagnostic)
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["code"], "missing-caller-context")
            self.assertIsNone(decision["effective_ceiling"])

    def test_snapshot_counts_and_probe_command(self):
        health = ROOT / "scripts/lib/overlay_health.py"
        with patch.object(sys, "argv", [str(health), str(ROOT), "--pending"]), \
                patch.dict(os.environ, OMNILANE_AA_TRANSPORT_OVERLAY=""), \
                contextlib.redirect_stdout(io.StringIO()):
            namespace = runpy.run_path(str(health))
        row_ids = [cid for cid in build_overlay.PROVEN if build_overlay.ROWS[cid]["vendor"] == "claude"]
        overlay = {"snapshot_id": "older", "mappings": [{"config_id": cid} for cid in row_ids[:-1]]}
        registry = copy.deepcopy(build_overlay.REGISTRY)
        registry["_transport_carry_forward"] = [{"config_id": cid} for cid in row_ids[:-1]]
        hint = namespace["snapshot_summary"](overlay, registry)
        self.assertEqual(hint, f"snapshot verification: {len(row_ids) - 1} row(s) carried over; "
                              "1 new and unprobed. Next: omnilane resign --vendor claude")
        overlay["snapshot_id"] = registry["snapshot"]["id"]
        self.assertEqual(namespace["snapshot_summary"](overlay, registry), hint)
        overlay["mappings"].append({"config_id": row_ids[-1]})
        self.assertIn("0 new and unprobed. Next: omnilane doctor",
                      namespace["snapshot_summary"](overlay, registry))

    def test_attention_covers_cooldown_login_incomplete_and_regression(self):
        cases = [
            ({"deferred_retries": {"claude": ["row"]}}, "operator-or-login-needed", "--retry-failed"),
            ({"sweeps": {"claude": {"outcome": "unprobeable"}}}, "needs-login-session", "desktop terminal"),
            ({"sweeps": {"claude": {"outcome": "interrupted"}}}, "provider-verification-incomplete", "--vendor claude"),
            ({"sweeps": {"claude": {"outcome": "done", "regressed": ["row"]}}}, "operator-or-login-needed", "--allow-shrink"),
            ({"evidence_missing": True}, "operator-or-login-needed", "omnilane doctor"),
        ]
        for extra, reason, command in cases:
            with self.subTest(reason=reason, extra=extra):
                summary = dict(vendors={"claude": {"cli": "/fixture", "drifted": True}}, held=["claude"], **extra)
                item, = verification_status.attention(20, summary)
                self.assertEqual(item["reason"], reason)
                self.assertEqual(item["reason"], verification_status.reason_class(20, summary, "claude"))
                self.assertIn(command, item["next_command"])
                if command == "--allow-shrink":
                    self.assertIn("human operator", item["next_command"])
                    self.assertIn("terminal", item["next_command"])

    def test_unsafe_signatures_and_evidence_tier_loss_never_suggest_approval(self):
        for reason in ("invalid", "unsigned", "unverifiable", "policy-invalid"):
            summary = {"vendors": {"claude": {"cli": "/fixture", "gate": {"allowed": False},
                       "origin_reason": reason}}, "held": ["claude"]}
            self.assertEqual(verification_status.attention(20, summary)[0]["next_command"], "omnilane doctor")
        summary = {"held": ["claude"], "sweeps": {"claude": {"outcome": "done", "regressed": ["row"],
                   "regression_reasons": {"row": "evidence-tier-regression"}}}}
        self.assertEqual(verification_status.attention(20, summary)[0]["next_command"], "omnilane doctor")


if __name__ == "__main__":
    unittest.main()
