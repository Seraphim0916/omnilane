"""Offline probe identity and overlay regression tests; never call a provider."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".rollback/overlay-full-20260907/evidence"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/lib/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load_module("probe")
builder = load_module("build_overlay")
aa_policy = load_module("aa_policy")

REGRESSION_CASES = (
    ("cl-claude-opus-5-max", "claude", "CLAUDE_SELECTOR_OK", "pass", ""),
    ("cl-claude-fable-5-1-max", "claude", "CLAUDE_SELECTOR_OK", "fail", "quota"),
    ("cl-rmode-claude-opus-4-5-noeffort", "claude", "CLAUDE_SELECTOR_OK", "fail", "api-error"),
    ("cl-invalid-effort", "claude", "CLAUDE_SELECTOR_OK", "fail", "effort"),
    ("agy-gemini-3_8-flash-medium", "agy", "GEMINI_SELECTOR_OK", "pass", ""),
    ("agy-invalid-model", "agy", "GEMINI_SELECTOR_OK", "fail", ""),
    ("gk-grok-4_6-medium", "grok", "GROK_SELECTOR_OK", "pass", ""),
    ("gk-invalid-effort", "grok", "GROK_SELECTOR_OK", "fail", ""),
    ("cx-gpt-6-astra-xhigh", "codex", "CODEX_SELECTOR_OK", "pass", ""),
)


class VerdictTests(unittest.TestCase):
    def evidence(self, **updates):
        return {"command": ["claude", "--model", "requested"],
                "exit_code": 0, "timed_out": False, **updates}

    def stdout(self, **updates):
        return json.dumps({"result": "CUSTOM_OK", "is_error": False,
                           "modelUsage": {"requested": {}}, **updates})

    def test_claude_validates_actual_model_and_requested_token(self):
        self.assertEqual(probe.verdict(self.evidence(), self.stdout(), "", "claude", "CUSTOM_OK"),
                         ("pass", "expected-token-and-model-matched", "requested", "billed-model"))
        result = probe.verdict(self.evidence(), self.stdout(modelUsage={"different": {}}),
                               "", "claude", "CUSTOM_OK")
        self.assertEqual(result, ("fail", "model-mismatch", "different", "selector-only"))

    def test_claude_failures(self):
        cases = [
            (self.evidence(timed_out=True), self.stdout(), "", "timeout"),
            (self.evidence(), "not json", "", "invalid-json"),
            (self.evidence(), "[]", "", "invalid-json-result"),
            (self.evidence(), self.stdout(modelUsage={}), "", "missing-model-usage"),
            (self.evidence(), self.stdout(modelUsage={"requested": {}, "other": {}}), "", "model-mismatch"),
            (self.evidence(command=["claude"]), self.stdout(), "", "missing-requested-model"),
            (self.evidence(), self.stdout(), "Unknown --effort 'NOSUCH'", "effort-silently-defaulted"),
            (self.evidence(exit_code=1), self.stdout(), "", "exit-code"),
            (self.evidence(), self.stdout(result="wrong token"), "", "missing-expected-token"),
        ]
        for record, stdout, stderr, reason in cases:
            with self.subTest(reason=reason):
                result = probe.verdict(record, stdout, stderr, "claude", "CUSTOM_OK")
                self.assertEqual(result[0], "fail")
                self.assertIn(reason, result[1])

    def test_claude_error_reason_preserves_first_120_result_characters(self):
        message = "API Error: " + "x" * 150
        result = probe.verdict(self.evidence(), self.stdout(is_error=True, result=message),
                               "", "claude", "CUSTOM_OK")
        self.assertEqual(result[:2], ("fail", "api-error: " + message[:120]))

    def test_model_equals_option_and_purity(self):
        record = self.evidence(command=["/bin/claude", "--model=requested"])
        before = copy.deepcopy(record)
        self.assertEqual(probe.verdict(record, self.stdout(), "", "claude", "CUSTOM_OK")[0], "pass")
        self.assertEqual(record, before)

    def test_vendor_specific_stderr_and_missing_token(self):
        for vendor in ("grok", "agy", "codex"):
            with self.subTest(vendor=vendor):
                record = self.evidence()
                self.assertEqual(probe.verdict(record, "CUSTOM_OK", "", vendor, "CUSTOM_OK")[0], "pass")
                self.assertEqual(probe.verdict(record, "wrong", "", vendor, "CUSTOM_OK")[0], "fail")
                self.assertEqual(probe.verdict(record, "CUSTOM_OK", "", vendor, "")[0], "fail")
                self.assertEqual(probe.verdict(self.evidence(timed_out=True), "CUSTOM_OK", "", vendor, "CUSTOM_OK")[0], "fail")
                self.assertEqual(probe.verdict(self.evidence(exit_code=1), "CUSTOM_OK", "", vendor, "CUSTOM_OK")[0], "fail")
                result = probe.verdict(record, "CUSTOM_OK", "ordinary banner", vendor, "CUSTOM_OK")
                self.assertEqual(result[0], "pass" if vendor == "codex" else "fail")
        result = probe.verdict(self.evidence(), "CUSTOM_OK", "WARNING: inspect me\nerror: review", "codex", "CUSTOM_OK")
        self.assertEqual(result[0], "pass")
        self.assertIn("WARNING: inspect me", result[1])
        self.assertIn("error: review", result[1])
        self.assertIsNone(result[2])
        self.assertEqual(probe.verdict(self.evidence(), "CUSTOM_OK", "", "unknown", "CUSTOM_OK")[0], "fail")

    @unittest.skipUnless(EVIDENCE.is_dir(), "historical evidence is host-local and read-only")
    def test_nine_historical_evidence_regressions(self):
        for name, vendor, token, expected, reason in REGRESSION_CASES:
            with self.subTest(evidence=name):
                record = json.loads((EVIDENCE / f"{name}.json").read_text())
                stdout = (EVIDENCE / f"{name}.stdout").read_text()
                stderr = (EVIDENCE / f"{name}.stderr").read_text()
                result = probe.verdict(record, stdout, stderr, vendor, token)
                self.assertEqual(result[0], expected)
                self.assertIn(reason, result[1])

    @unittest.skipUnless(EVIDENCE.is_dir(), "historical evidence is host-local and read-only")
    def test_evidence_predating_the_tier_field_reads_as_selector_only(self):
        for name, vendor, token, expected, _ in REGRESSION_CASES:
            with self.subTest(evidence=name):
                record = json.loads((EVIDENCE / f"{name}.json").read_text())
                stdout = (EVIDENCE / f"{name}.stdout").read_text()
                stderr = (EVIDENCE / f"{name}.stderr").read_text()
                result = probe.verdict(record, stdout, stderr, vendor, token)
                expected_tier = "billed-model" if vendor == "claude" and expected == "pass" \
                    else "selector-only"
                self.assertEqual(result[3], expected_tier)


class EvidenceTierTests(unittest.TestCase):
    """The tier follows the evidence a run produced, never the vendor name."""

    def grok_evidence(self, model="grok-4.6"):
        return {"command": ["grok", "-m", model, "--output-format", "json"],
                "exit_code": 0, "timed_out": False}

    def grok_stdout(self, *billed):
        return json.dumps({"text": "CUSTOM_OK",
                           "modelUsage": {name: {"costUSD": 0.1} for name in billed}})

    def test_grok_accepts_the_exact_model_and_its_build_suffix_only(self):
        for billed, expected in (("grok-4.6", "pass"), ("grok-4.6-build", "pass"),
                                 ("grok-4.6-anything", "fail"), ("grok-4", "fail"),
                                 ("grok-4.6-build-extra", "fail")):
            with self.subTest(billed=billed):
                result = probe.verdict(self.grok_evidence(), self.grok_stdout(billed),
                                       "", "grok", "CUSTOM_OK")
                self.assertEqual(result[0], expected)
                self.assertEqual(result[3], "billed-model" if expected == "pass" else "selector-only")

    def test_grok_two_billed_models_is_a_mismatch(self):
        result = probe.verdict(self.grok_evidence(),
                               self.grok_stdout("grok-4.6", "grok-4.5"),
                               "", "grok", "CUSTOM_OK")
        self.assertEqual(result[:2], ("fail", "model-mismatch"))
        self.assertEqual(result[3], "selector-only")

    def test_grok_plain_output_still_passes_at_the_lower_tier(self):
        result = probe.verdict(self.grok_evidence(), "CUSTOM_OK", "", "grok", "CUSTOM_OK")
        self.assertEqual(result[0], "pass")
        self.assertIn("no-billed-model", result[1])
        self.assertEqual(result[3], "selector-only")

    def test_codex_rollout_promotes_matching_and_fails_contradicting(self):
        record = {"command": ["codex", "exec", "-m", "gpt-5.6-luna"],
                  "exit_code": 0, "timed_out": False}
        matching = "# source\t/x/rollout.jsonl\npayload.model\tgpt-5.6-luna\n"
        result = probe.verdict(record, "CUSTOM_OK", "", "codex", "CUSTOM_OK",
                               {"rollout": matching})
        self.assertEqual(result[0], "pass")
        self.assertEqual(result[2], "gpt-5.6-luna")
        self.assertEqual(result[3], "client-echo")

        other = "payload.model\tgpt-5.6-terra\n"
        result = probe.verdict(record, "CUSTOM_OK", "", "codex", "CUSTOM_OK",
                               {"rollout": other})
        self.assertEqual(result[0], "fail")
        self.assertIn("client-record-mismatch", result[1])
        self.assertEqual(result[3], "selector-only")

    def test_codex_without_a_rollout_stays_a_selector_only_pass(self):
        record = {"command": ["codex", "exec", "-m", "gpt-5.6-luna"],
                  "exit_code": 0, "timed_out": False}
        for rollout in ("", "# source\t/x/rollout.jsonl\n"):
            with self.subTest(rollout=rollout):
                result = probe.verdict(record, "CUSTOM_OK", "", "codex", "CUSTOM_OK",
                                       {"rollout": rollout})
                self.assertEqual(result[0], "pass")
                self.assertIn("no-client-record", result[1])
                self.assertEqual(result[3], "selector-only")

    def test_codex_stderr_review_survives_the_tier_judgement(self):
        record = {"command": ["codex", "exec", "-m", "gpt-5.6-luna"],
                  "exit_code": 0, "timed_out": False}
        result = probe.verdict(record, "CUSTOM_OK", "WARNING: under development", "codex",
                               "CUSTOM_OK", {"rollout": "payload.model\tgpt-5.6-luna\n"})
        self.assertEqual(result[0], "pass")
        self.assertIn("WARNING: under development", result[1])
        self.assertEqual(result[3], "client-echo")

    def test_agy_compares_the_resolved_model_and_never_the_display_label(self):
        record = {"command": ["agy", "--model", "gemini-3.8-flash-low"],
                  "exit_code": 0, "timed_out": False}
        digest = ("# source\t/x/cli.log\n# label\tGemini 3.8 Flash (Low)\n"
                  "cli.log:resolved-model:gemini-3.8-flash-low\tgemini-3.8-flash-low\n")
        result = probe.verdict(record, "CUSTOM_OK", "", "agy", "CUSTOM_OK", {"cli_log": digest})
        self.assertEqual(result[0], "pass")
        self.assertEqual(result[3], "client-echo")

        label_only = "# source\t/x/cli.log\n# label\tGemini 3.8 Flash (Low)\n"
        result = probe.verdict(record, "CUSTOM_OK", "", "agy", "CUSTOM_OK",
                               {"cli_log": label_only})
        self.assertEqual(result[0], "pass")
        self.assertEqual(result[3], "selector-only")

    def test_agy_resolving_a_different_model_fails(self):
        record = {"command": ["agy", "--model", "gemini-3.8-flash-low"],
                  "exit_code": 0, "timed_out": False}
        digest = "cli.log:resolved-model:x\tgemini-3.8-flash-high\n"
        result = probe.verdict(record, "CUSTOM_OK", "", "agy", "CUSTOM_OK", {"cli_log": digest})
        self.assertEqual(result[0], "fail")
        self.assertIn("client-record-mismatch", result[1])

    def test_every_reported_tier_is_a_known_tier(self):
        cases = [
            ({"command": [], "timed_out": True}, "", "", "claude", "T", {}),
            ({"command": [], "exit_code": 1}, "", "", "grok", "T", {}),
            ({"command": [], "exit_code": 0}, "T", "", "agy", "T", {}),
            ({"command": [], "exit_code": 0}, "T", "", "codex", "T", {}),
            ({"command": [], "exit_code": 0}, "T", "", "nope", "T", {}),
        ]
        for record, out, err, vendor, token, extra in cases:
            with self.subTest(vendor=vendor):
                self.assertIn(probe.verdict(record, out, err, vendor, token, extra)[3],
                              probe.EVIDENCE_TIERS)

    def test_codex_record_reads_the_rollout_named_by_the_thread_id(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".sandbox-tmp") as tmp:
            sessions = Path(tmp) / "2026/09/10"
            sessions.mkdir(parents=True)
            thread = "01a08774-0fb5-7fd0-aba3-77489839d1c9"
            rollout = sessions / f"rollout-2026-09-10T02-35-25-{thread}.jsonl"
            rollout.write_text(json.dumps({"payload": {"model": "gpt-5.6-luna"}}) + "\n"
                               + "not json\n"
                               + json.dumps({"items": [{"model": "gpt-5.6-luna"}]}) + "\n")
            stdout = json.dumps({"type": "thread.started", "thread_id": thread}) + "\n"
            digest = probe.codex_record(stdout, Path(tmp))
            self.assertIn(f"# thread_id\t{thread}", digest)
            self.assertEqual(probe.record_values(digest), ["gpt-5.6-luna", "gpt-5.6-luna"])
            self.assertEqual(probe.codex_record("no events here", Path(tmp)), "")
            self.assertEqual(probe.codex_record(
                json.dumps({"thread_id": "absent-thread"}), Path(tmp)), "")

    def test_agy_record_ignores_logs_written_before_this_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".sandbox-tmp") as tmp:
            app_root = Path(tmp)
            log = app_root / "cli.log"
            log.write_text('model_resolver.go] Resolving model gemini-3.8-flash-low\n'
                           'model_config_manager.go] Propagating selected model override to '
                           'backend: label="Gemini 3.8 Flash (Low)"\n')
            digest = probe.agy_record(app_root, log.stat().st_mtime)
            self.assertEqual(probe.record_values(digest), ["gemini-3.8-flash-low"])
            self.assertIn('# label\tGemini 3.8 Flash (Low)', digest)
            self.assertEqual(probe.agy_record(app_root, log.stat().st_mtime + 600), "")
            self.assertEqual(probe.agy_record(app_root / "absent", 0), "")


class ProbeAndOverlayTests(unittest.TestCase):
    def setUp(self):
        sandbox = ROOT / ".sandbox-tmp"
        sandbox.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="probe-identity-test-", dir=sandbox)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def evidence(self, name, verdict="pass", **updates):
        directory = self.root / "evidence"
        directory.mkdir(exist_ok=True)
        record = {"command": ["claude", "--model", "claude-opus-5"], "exit_code": 0,
                  "timed_out": False, "probed_at": "2026-09-09T00:00:00+00:00",
                  "verdict_reason": "fixture-reason", "observed_model": "fixture-model", **updates}
        if verdict is not None:
            record["verdict"] = verdict
        (directory / f"{name}.json").write_text(json.dumps(record))
        (directory / f"{name}.stdout").write_text("FIXTURE_OK")
        (directory / f"{name}.stderr").write_text("")

    def build(self, proven):
        output = StringIO()
        with patch.object(builder, "PROVEN", proven), patch.object(builder, "CORE_EVIDENCE", []), redirect_stdout(output):
            builder.main(["--root", str(self.root)])
        overlay = json.loads((self.root / "transport-contracts.local.json").read_text())
        manifest = json.loads((self.root / "probe-manifest.json").read_text())
        return overlay, manifest, output.getvalue()

    def test_probe_cli_writes_verdict_using_only_a_local_python_fixture(self):
        command = [sys.executable, str(ROOT / "scripts/lib/probe.py"), "--root", str(self.root),
                   "--vendor", "codex", "--expect", "LOCAL_FIXTURE_OK", "local-fixture", "--",
                   sys.executable, "-c", "import sys; print('LOCAL_FIXTURE_OK'); print('banner', file=sys.stderr)"]
        completed = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["verdict"], "pass")
        self.assertIsNone(record["observed_model"])
        self.assertIn("probed_at", record)
        self.assertEqual(json.loads((self.root / "evidence/local-fixture.json").read_text()), record)

    def test_probe_requires_expect_before_running_any_command(self):
        with self.assertRaises(ValueError):
            probe.probe("missing-token", [sys.executable, "-c", "raise SystemExit('must not run')"],
                        root=self.root, vendor="codex")
        self.assertFalse((self.root / "evidence").exists())

    def test_failed_probe_is_visible_but_not_signed_or_hashed(self):
        self.evidence("pass")
        self.evidence("fail", "fail")
        proven = {
            "claude/claude-opus-5": ("model_and_effort", "claude-opus-5", "pass"),
            "claude/claude-sonnet-5": ("model_and_effort", "claude-sonnet-5", "fail"),
        }
        overlay, manifest, _ = self.build(proven)
        self.assertEqual([row["config_id"] for row in overlay["mappings"]], ["claude/claude-opus-5"])
        self.assertEqual(list(manifest["probe_runs"]), ["claude/claude-opus-5"])
        self.assertEqual(overlay["unproven"], [{"config_id": "claude/claude-sonnet-5",
                                              "verdict_reason": "fixture-reason", "observed_model": "fixture-model",
                                              "probed_at": "2026-09-09T00:00:00+00:00"}])
        (self.root / "evidence/fail.stdout").write_text("changed unproven evidence")
        # Unproven rows do not enter the frozen registry, evidence hash checks or dispatch gate.
        with patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": str(self.root / "transport-contracts.local.json")}, clear=False):
            with patch.dict(os.environ):
                os.environ.pop("OMNILANE_AA_OVERLAY_SHA256", None)
                registry, _ = aa_policy.load_registry(ROOT / "config/aa-model-policy.json")
        rows = {row["id"]: row for row in registry["scored_configs"]}
        self.assertTrue(rows["claude/claude-opus-5"]["transport_mapping"]["runtime_verified"])
        self.assertIsNot(rows["claude/claude-sonnet-5"]["transport_mapping"].get("runtime_verified"), True)

    def test_legacy_descriptor_still_signed_with_named_warning(self):
        self.evidence("old-format", None, exit_code=1, probed_at=None)
        proven = {"claude/claude-opus-5": ("model_and_effort", "claude-opus-5", "old-format")}
        overlay, _, stdout = self.build(proven)
        self.assertEqual(len(overlay["mappings"]), 1)
        self.assertEqual(overlay["unproven"], [])
        self.assertIn("warning", stdout.lower())
        self.assertIn("unknown", stdout)
        self.assertIn("claude/claude-opus-5", stdout)
        self.assertIn("old-format", stdout)

    def test_any_explicit_nonpass_verdict_is_unproven_not_legacy(self):
        self.evidence("bad", "maybe")
        path = self.root / "evidence/bad.json"
        for value in ("maybe", "unknown", None, 0, False):
            with self.subTest(verdict=value):
                descriptor = json.loads(path.read_text())
                descriptor["verdict"] = value
                path.write_text(json.dumps(descriptor))
                overlay, manifest, stdout = self.build({
                    "claude/claude-opus-5": ("model_and_effort", "claude-opus-5", "bad")})
                self.assertEqual(overlay["mappings"], [])
                self.assertEqual(manifest["probe_runs"], {})
                self.assertEqual(len(overlay["unproven"]), 1)
                self.assertNotIn("legacy", stdout)


if __name__ == "__main__":
    unittest.main()
