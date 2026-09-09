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
                         ("pass", "expected-token-and-model-matched", "requested"))
        result = probe.verdict(self.evidence(), self.stdout(modelUsage={"different": {}}),
                               "", "claude", "CUSTOM_OK")
        self.assertEqual(result, ("fail", "model-mismatch", "different"))

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
