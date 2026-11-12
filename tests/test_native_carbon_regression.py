"""Offline regressions for policy checks on the resolved native target."""

import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import hashlib
import io
import itertools
import json
import os
import socket
import unittest
from unittest.mock import patch

from tests import test_native_executor as fixtures


class NativeCarbonProofTests(unittest.TestCase):
    EFFORTS = ("high", "medium", "low")

    def setUp(self):
        self.fixture = fixtures.NativeExecutorTests("test_native_eligible_and_pending")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.native = self.fixture.load_native()
        self.registry = json.loads(
            (fixtures.ROOT / "config/aa-model-policy.json").read_text())
        self.fixture.env["OMNILANE_REFUSAL_JSON_ONLY"] = "1"

    def prepare(self, effort, requested_model, caller_kind, *, proof=False, fallback=False):
        model = "gemini-4-carbon-" + effort
        self.fixture.ctx.update(vendor="gemini", current_model=model)
        self.fixture.ctx["capabilities"][0].update(model=model, efforts=[effort])
        self.fixture.ctx["requirements"]["tools"] = ["unavailable-tool"] if fallback else []
        self.fixture.save_context()
        args = self.fixture.native_args()
        args.vendor, args.model, args.effort = "gemini", requested_model, effort
        args.executor = "auto" if fallback else "native"
        args.dry_run = True
        args.operator_asserted_human = caller_kind == "human"
        if caller_kind == "model":
            row = max(self.registry["scored_configs"], key=lambda entry: entry["score"])
            caller = {
                "schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"],
                "kind": "model",
                "caller": {key: row[key] for key in self.native.aa_policy.IDENTITY_FIELDS},
                "inherited_ceiling": row["score"],
            }
            path = self.fixture.base / "caller.json"
            path.write_text(json.dumps(caller))
            args.caller_context = str(path)
        self.fixture.env.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
        if proof:
            # A valid offline selector-proof fixture, never host/provider evidence.
            row = next(row for row in self.registry["scored_configs"]
                       if row["model"] == "gemini-4-carbon" and row["effort"] == effort)
            evidence = self.fixture.base / "selector-contract.txt"
            evidence.write_text("offline Carbon selector contract fixture\n")
            overlay = {
                "schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"],
                "host": socket.gethostname(),
                "evidence": [{"path": str(evidence), "vendor": "gemini",
                              "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
                "mappings": [{
                    "config_id": row["id"],
                    "identity": {key: row[key] for key in self.native.aa_policy.IDENTITY_FIELDS},
                    "runtime_model": model, "runtime_effort": effort,
                    "selector_type": "model_id_encoded_effort",
                    "verification": "request-selector-contract",
                }],
            }
            path = self.fixture.base / "overlay.json"
            path.write_text(json.dumps(overlay))
            self.fixture.env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(path)
        return args, model

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.fixture.env, clear=True), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = self.native.route(args)
        self.fixture.assert_no_jobs()
        return rc, stdout.getvalue(), stderr.getvalue()

    def cases(self):
        return itertools.product(self.EFFORTS, ("-", ""), ("human", "model"))

    def test_resolved_carbon_target_requires_proof(self):
        """Port of the reviewer's failing public-CLI regression, without report writes."""
        for effort in self.EFFORTS:
            with self.subTest(effort=effort):
                self.prepare(effort, "-", "human")
                result = self.fixture.route(
                    "--dry-run", "--executor", "native", "--vendor", "gemini",
                    "--model", "-", "--effort", effort,
                    lane="hardest-coding", expected=None)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.fixture.assert_no_jobs()

    def test_unproven_carbon_matrix(self):
        for effort, request, caller in self.cases():
            with self.subTest(effort=effort, request=request, caller=caller):
                args, model = self.prepare(effort, request, caller)
                rc, stdout, stderr = self.invoke(args)
                self.assertEqual(rc, 3, stdout + stderr)
                self.assertEqual(stdout, "")
                decision = json.loads(stderr)
                self.assertEqual(decision["target_request"]["model"], model)
                self.assertEqual(decision["failed_gate"], "target-transport")
                self.assertEqual(decision["code"], "runtime-mapping-unverified")

    def test_unproven_carbon_cannot_fall_back_to_cli(self):
        for effort, request, caller in self.cases():
            with self.subTest(effort=effort, request=request, caller=caller):
                args, model = self.prepare(effort, request, caller, fallback=True)
                rc, stdout, stderr = self.invoke(args)
                self.assertEqual(rc, 3, stdout + stderr)
                self.assertEqual(stdout, "")
                decision = json.loads(stderr)
                self.assertEqual(decision["target_request"]["model"], model)
                self.assertEqual(decision["failed_gate"], "target-transport")

    def test_proven_carbon_matrix_records_final_model(self):
        for effort, request, caller in self.cases():
            with self.subTest(effort=effort, request=request, caller=caller):
                args, model = self.prepare(effort, request, caller, proof=True)
                rc, stdout, stderr = self.invoke(args)
                self.assertEqual(rc, 0, stdout + stderr)
                plan = json.loads(stdout)
                self.assertEqual(plan["model"], model)
                self.assertEqual(plan["aa_policy"]["target_request"]["model"], model)
                self.assertTrue(plan["aa_policy"]["allowed"])
                self.assertFalse(plan["provider_invoked"])
                self.assertFalse(plan["job_state_created"])
                self.assertEqual(stderr, "")

    def test_proven_carbon_keeps_cli_fallback(self):
        for effort, request, caller in self.cases():
            with self.subTest(effort=effort, request=request, caller=caller):
                args, model = self.prepare(effort, request, caller, proof=True, fallback=True)
                rc, stdout, stderr = self.invoke(args)
                self.assertEqual(rc, 10, stdout + stderr)
                self.assertEqual(json.loads(stdout), {"model": model, "reason": "tools-mismatch"})
                self.assertEqual(stderr, "")

    def test_non_carbon_human_waiver_is_unchanged(self):
        for request in ("-", ""):
            with self.subTest(request=request):
                args, _ = self.prepare("high", request, "human")
                model = "gemini-4-argon-high"
                self.fixture.ctx["current_model"] = model
                self.fixture.ctx["capabilities"][0]["model"] = model
                self.fixture.save_context()
                rc, stdout, stderr = self.invoke(args)
                self.assertEqual(rc, 0, stdout + stderr)
                plan = json.loads(stdout)
                self.assertEqual(plan["model"], model)
                self.assertEqual(plan["aa_policy"]["target_request"]["model"], model)
                self.assertEqual(plan["aa_policy"]["code"], "operator-asserted-human-exemption")


if __name__ == "__main__":
    unittest.main(verbosity=2)
