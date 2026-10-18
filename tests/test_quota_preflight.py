"""Offline quota preflight contract; fake CLIs never contact providers."""

import offline_env
import home_isolation
import datetime as dt
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class QuotaPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quota-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env, self.violations = offline_env.isolated_environment(
            self.base / "isolated", os.environ.get("PATH", ""))
        self.home = Path(self.env["HOME"]) / ".omnilane"
        self.quota = self.home / "quota"
        self.quota.mkdir()
        self.bins = self.base / "bin"
        self.bins.mkdir()
        self.marker = self.base / "provider-called"
        for vendor in ("codex", "claude", "grok", "gemini"):
            binary = self.bins / vendor
            binary.write_text(f"#!/bin/sh\necho {vendor} >> '{self.marker}'\nexit 91\n")
            binary.chmod(0o755)
            self.env[vendor.upper() + "_BIN"] = str(binary)
        self.env.update(OMNILANE_HOME=str(self.home),
                        AGY_BIN=str(self.bins / "gemini"),
                        PATH=str(self.bins) + os.pathsep + self.env["PATH"],
                        OMNILANE_QUOTA_ENABLED="1")
        self.chain = "codex fixture-codex high | claude fixture-claude high"
        self.routes()
        self.snapshot()

    def tearDown(self):
        self.assertFalse(self.violations.exists(), "unmocked network command")

    def routes(self, chain=None):
        (self.home / "routing.local.yaml").write_text("".join(
            f"{lane}: {chain or self.chain}\n"
            for lane in ("quota-unit", "hardest-coding", "hard-judgment")))

    def snapshot(self, used=50, age=0, **changes):
        now = dt.datetime.now(dt.timezone.utc)
        value = dict(vendor="codex", window="weekly", used_percent=used,
                     captured_at=(now - dt.timedelta(seconds=age)).isoformat(),
                     resets_at=(now + dt.timedelta(days=2)).isoformat(),
                     source="offline-fixture")
        value.update(changes)
        (self.quota / "codex.json").write_text(json.dumps(value))

    def dispatch(self, *flags, lane="quota-unit", dry=True):
        return subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/dispatch.sh"),
             *(["--dry-run"] if dry else []), "--executor", "cli", *flags,
             lane, "quota fixture"], env=self.env, cwd=ROOT,
            capture_output=True, text=True, timeout=30)

    def plan(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists(), "dry-run invoked provider")
        return next(json.loads(line.split("=", 1)[1]) for line in result.stdout.splitlines()
                    if line.startswith("quota_preflight="))

    def test_normal_keeps_original(self):
        result = self.dispatch()
        plan = self.plan(result)
        self.assertIn("vendor=codex\n", result.stdout)
        self.assertEqual(plan["status"], "fresh")
        self.assertEqual(plan["action"], "unchanged")

    def test_threshold_reroutes_same_lane_in_order(self):
        self.snapshot(90)
        self.routes(self.chain + " | gemini fixture-gemini high")
        result = self.dispatch()
        plan = self.plan(result)
        self.assertIn("vendor=claude\n", result.stdout)
        self.assertEqual(plan["action"], "rerouted")
        self.assertEqual(plan["original"]["vendor"], "codex")
        self.assertEqual(plan["final"]["vendor"], "claude")
        self.assertEqual(plan["exclusion_reason"], "quota_near_limit")
        self.assertEqual(plan["snapshot"]["source"], "offline-fixture")

    def test_explicit_flags_preserve_target(self):
        self.snapshot(99)
        for flag, value in (("--vendor", "codex"), ("--model", "fixture-codex"),
                            ("--effort", "high")):
            with self.subTest(flag=flag):
                result = self.dispatch(flag, value, lane="hardest-coding")
                self.assertEqual(self.plan(result)["action"], "explicit")
                self.assertIn("vendor=codex\n", result.stdout)
                self.assertIn("quota_near_limit", result.stderr)

    def test_unknown_and_stale_do_not_reroute(self):
        cases = [dict(age=1801), dict(captured_at="bad"),
                 dict(captured_at="2026-10-18T00:00:00"), dict(age=-60),
                 dict(window="five_hour"), dict(vendor="claude"),
                 dict(used_percent=True), dict(used_percent=101),
                 dict(used_percent=float("nan")), dict(source=None),
                 dict(resets_at=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                self.snapshot(99, **changes) if "used_percent" not in changes else self.snapshot(**changes)
                result = self.dispatch()
                plan = self.plan(result)
                self.assertEqual(plan["status"], "unknown")
                self.assertIn("vendor=codex\n", result.stdout)
                if changes.get("age") == 1801:
                    self.assertEqual(plan["snapshot"]["freshness"], "stale")
        (self.quota / "codex.json").write_text("{")
        self.assertEqual(self.plan(self.dispatch())["status"], "unknown")
        (self.quota / "codex.json").rename(self.quota / "missing.json")
        self.assertEqual(self.plan(self.dispatch())["status"], "unknown")

    def test_missing_fields_unknown(self):
        for key in ("vendor", "window", "used_percent", "captured_at", "resets_at", "source"):
            self.snapshot(99)
            path = self.quota / "codex.json"
            value = json.loads(path.read_text())
            del value[key]
            path.write_text(json.dumps(value))
            self.assertEqual(self.plan(self.dispatch())["status"], "unknown", key)

    def test_snapshot_time_boundaries_and_untrusted_shapes(self):
        spec = importlib.util.spec_from_file_location("quota_preflight", ROOT / "scripts/lib/quota_preflight.py")
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
        path = self.quota / "codex.json"
        with patch.dict(os.environ, self.env, clear=True):
            for age, status in ((1800, "fresh"), (1800.001, "unknown"), (-0.001, "unknown")):
                self.snapshot(90, captured_at=(now - dt.timedelta(seconds=age)).isoformat(),
                              resets_at=(now + dt.timedelta(days=1)).isoformat())
                value = reader.snapshot("codex", str(self.home), now)
                self.assertEqual(value["status"], status)
                self.assertEqual(value["near_limit"], status == "fresh")
            for raw in ("[]", "null", '{"vendor":"codex","vendor":"claude"}',
                        "[" * 1200 + "]" * 1200, " " * 65537):
                path.write_text(raw)
                self.assertEqual(reader.snapshot("codex", str(self.home), now)["status"], "unknown")

    def test_no_alternative_hard_lanes_refuse(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high")
        for lane in ("hardest-coding", "hard-judgment"):
            result = self.dispatch(lane=lane)
            self.assertNotEqual(result.returncode, 0)
            refusal = json.loads(result.stderr.strip())
            self.assertEqual(refusal["code"], "quota_near_limit_no_alternative")
            self.assertFalse(refusal["allowed"])
            self.assertIsNone(refusal["quota_preflight"]["final"])
        self.assertFalse((self.home / "jobs").exists())

    def test_no_alternative_regular_lane_warns_and_keeps_original(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high | off")
        result = self.dispatch()
        self.assertEqual(self.plan(result)["action"], "quota_near_limit")
        self.assertIn("vendor=codex\n", result.stdout)
        self.assertIn("quota_near_limit", result.stderr)

    def test_unavailable_candidate_is_not_selected(self):
        self.snapshot(99)
        self.env["CLAUDE_BIN"] = str(self.bins / "missing")
        self.routes(self.chain + " | gemini fixture-gemini high")
        result = self.dispatch()
        self.plan(result)
        self.assertIn("vendor=gemini\n", result.stdout)

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS capability gate")
    def test_grok_work_capability_still_applies(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high | grok fixture-grok high | claude fixture-claude high")
        result = self.dispatch("--mode", "work", "--workdir", str(ROOT))
        self.plan(result)
        self.assertIn("vendor=claude\n", result.stdout)

    def test_configurable_threshold_expiry_and_directory(self):
        self.snapshot(85, age=100)
        self.env.update(OMNILANE_QUOTA_THRESHOLD_PERCENT="80",
                        OMNILANE_QUOTA_MAX_AGE_SECONDS="200",
                        OMNILANE_QUOTA_DIR=str(self.quota))
        self.assertEqual(self.plan(self.dispatch())["action"], "rerouted")
        self.env["OMNILANE_QUOTA_MAX_AGE_SECONDS"] = "10"
        self.assertEqual(self.plan(self.dispatch())["status"], "unknown")

    def test_disabled_has_no_quota_output_and_matches_legacy(self):
        self.env["OMNILANE_QUOTA_ENABLED"] = "0"
        self.snapshot(1)
        low = self.dispatch()
        self.snapshot(99)
        high = self.dispatch()
        self.assertEqual(low.returncode, 0, low.stderr)
        self.assertEqual((low.stdout, low.stderr), (high.stdout, high.stderr))
        self.assertNotIn("quota", low.stderr)
        self.assertNotIn("quota_preflight=", low.stdout)

    def test_local_chain_omission_is_not_bypassed(self):
        # A host's temporary exclusion is represented by its effective chain.
        self.snapshot(99)
        self.routes("codex fixture-codex high | gemini fixture-gemini high")
        result = self.dispatch()
        self.plan(result)
        self.assertIn("vendor=gemini\n", result.stdout)
        self.assertNotIn("vendor=claude\n", result.stdout)

    def test_vote_cannot_reintroduce_excluded_vendor(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high | vote codex,claude 1 | claude fixture-claude high")
        result = self.dispatch()
        self.plan(result)
        self.assertIn("vendor=claude\n", result.stdout)

    def test_original_vote_checks_its_codex_participant(self):
        self.snapshot(99)
        self.routes("vote codex,claude 1 | claude fixture-claude high")
        result = self.dispatch()
        plan = self.plan(result)
        self.assertEqual(plan["original"]["vendor"], "vote")
        self.assertEqual(plan["snapshot"]["vendor"], "codex")
        self.assertIn("vendor=claude\n", result.stdout)

    def native_context(self, vendor="claude", modes=None):
        value = dict(schema_version=1, harness="quota-fixture", vendor=vendor,
                     requirements=dict(tools=[], isolation="shared-inherited", lifecycle="single-shot"),
                     capabilities=[dict(model="fixture-" + vendor, efforts=["high"],
                                        modes=modes or ["advise"], workdirs=[str(ROOT)], tools=[],
                                        isolations=["shared-inherited"], lifecycles=["single-shot"])])
        path = self.base / "native-context.json"
        path.write_text(json.dumps(value))
        return path

    def test_native_candidate_uses_existing_capability_gate(self):
        self.snapshot(99)
        self.routes(self.chain + " | gemini fixture-gemini high")
        self.env["CLAUDE_BIN"] = str(self.bins / "missing")
        path = self.native_context("gemini")
        result = self.dispatch("--executor", "auto", "--native-context", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["executor"], "native")
        self.assertEqual(plan["vendor"], "gemini")
        self.assertEqual(plan["quota_preflight"]["action"], "rerouted")
        self.assertFalse(self.marker.exists())

    def test_native_capability_mismatch_cannot_be_alternative(self):
        self.snapshot(99)
        path = self.native_context(modes=["work"])
        result = self.dispatch("--executor", "native", "--native-context", str(path), lane="hard-judgment")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["code"], "quota_near_limit_no_alternative")

    def exact_policy(self):
        registry = json.loads((ROOT / "config/aa-model-policy.json").read_text())
        rows = registry["scored_configs"]
        codex = max((r for r in rows if r["vendor"] == "codex"), key=lambda r: r["score"])
        above = max((r for r in rows if r["vendor"] == "claude"), key=lambda r: r["score"])
        below = max((r for r in rows if r["vendor"] == "claude" and r["score"] < codex["score"]),
                    key=lambda r: r["score"])
        identity = ("vendor", "model", "effort", "reasoning", "fallback")
        evidence_path = self.base / "selector-proof.txt"
        evidence_path.write_text("offline selector fixture; not live transport proof\n")
        evidence = dict(path=str(evidence_path), sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest())
        mappings = []
        models = []
        for row in (codex, above, below):
            name = row["transport_mapping"].get("candidate_model_ids", [row["model"]])[0]
            models.append(name)
            mappings.append(dict(config_id=row["id"], identity={k: row[k] for k in identity},
                                 runtime_model=name, runtime_effort=row["effort"],
                                 selector_type="model_and_effort", verification="request-selector-contract"))
        overlay = self.base / "overlay.json"
        overlay.write_text(json.dumps(dict(schema_version=1, snapshot_id=registry["snapshot"]["id"],
                                           host=socket.gethostname(), evidence=[evidence], mappings=mappings)))
        caller = self.base / "caller.json"
        caller.write_text(json.dumps(dict(schema_version=1, snapshot_id=registry["snapshot"]["id"],
                                          kind="model", caller={k: codex[k] for k in identity},
                                          inherited_ceiling=codex["score"])))
        self.env.update(OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="0",
                        OMNILANE_AA_CALLER_CONTEXT=str(caller),
                        OMNILANE_AA_TRANSPORT_OVERLAY=str(overlay))
        self.routes("codex {} {} | claude {} {} | claude {} {}".format(
            models[0], codex["effort"] or "-", models[1], above["effort"] or "-",
            models[2], below["effort"] or "-"))
        self.expected_below = below["id"]
        return caller, overlay, codex, below

    def test_rerouted_target_still_obeys_exact_aa_ceiling(self):
        self.snapshot(99)
        self.exact_policy()
        result = self.dispatch()
        self.plan(result)
        policy = next(json.loads(l.split("=", 1)[1]) for l in result.stdout.splitlines() if l.startswith("aa_policy="))
        self.assertEqual(policy["target_config_id"], self.expected_below)
        self.assertLessEqual(policy["target_score"], policy["effective_ceiling"])

    def test_unverified_transport_never_becomes_alternative(self):
        self.snapshot(99)
        _, overlay, _, _ = self.exact_policy()
        value = json.loads(overlay.read_text())
        value["mappings"] = value["mappings"][:2]  # lower target has no verified mapping
        overlay.write_text(json.dumps(value))
        result = self.dispatch(lane="hardest-coding")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["code"], "quota_near_limit_no_alternative")

    def test_inherited_ceiling_is_not_relaxed(self):
        self.snapshot(99)
        caller, _, codex, _ = self.exact_policy()
        value = json.loads(caller.read_text())
        value["inherited_ceiling"] = 0
        caller.write_text(json.dumps(value))
        result = self.dispatch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertNotIn('"allowed":true', result.stderr)

    def test_existing_cli_job_is_not_retried_after_quota_changes(self):
        # Only the fake Codex process changes the snapshot, after job creation.
        self.snapshot(99)
        near = self.base / "near.json"
        near.write_bytes((self.quota / "codex.json").read_bytes())
        self.snapshot(10)
        (self.bins / "codex").write_text(
            f"#!/bin/sh\necho codex >> '{self.marker}'\n"
            f"cp '{near}' '{self.quota / 'codex.json'}'\nexit 91\n")
        result = self.dispatch("--timeout", "5", dry=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.marker.read_text().splitlines(), ["codex"])
        jobs = list((self.home / "jobs").glob("*/meta.json"))
        self.assertEqual(len(jobs), 1)
        meta = json.loads(jobs[0].read_text())
        self.assertEqual(meta["vendor"], "codex")
        quota = json.loads((jobs[0].parent / "quota-preflight.json").read_text())
        self.assertEqual(quota["action"], "unchanged")
        self.assertEqual(quota["job_id"], jobs[0].parent.name)
        self.assert_job_audit()
        self.assertEqual(json.loads((self.quota / "codex.json").read_text())["used_percent"], 99)

    def test_no_alternative_warning_is_persisted_in_job(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high")
        self.dispatch("--timeout", "5", dry=False)
        jobs = list((self.home / "jobs").glob("*/meta.json"))
        self.assertEqual(len(jobs), 1)
        quota = json.loads((jobs[0].parent / "quota-preflight.json").read_text())
        self.assertEqual(quota["action"], "quota_near_limit")
        self.assertEqual(quota["job_id"], jobs[0].parent.name)
        self.assert_job_audit()

    def test_native_reroute_decision_is_persisted(self):
        self.snapshot(99)
        path = self.native_context()
        result = self.dispatch("--executor", "native", "--native-context", str(path), dry=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        quota = json.loads((self.home / "jobs" / plan["job_id"] / "quota-preflight.json").read_text())
        self.assertEqual(quota["action"], "rerouted")
        self.assertEqual(quota["job_id"], plan["job_id"])
        self.assertEqual(plan["quota_preflight"], quota)
        self.assert_job_audit()
        self.assertFalse(self.marker.exists())

    def assert_job_audit(self):
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "audit", "--json"],
                                env=self.env, cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["failed"], 0)


if __name__ == "__main__":
    unittest.main()
