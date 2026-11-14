"""Source: codex-s / Astra S-1 worker / MacStudio, 2026-11-14.
Contracts against the pinned real policy plus retained synthetic SQLite fixtures.
No live provider, process table, user database, or authentication is consulted.
"""
from __future__ import annotations
import ast
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import offline_env
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy
import caller_identity
import native_context
import test_caller_identity_antigravity as fixture


class PolicyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.digest = caller_identity.load_registry(ROOT / "config/aa-model-policy.json")
        cls.rows = cls.registry["scored_configs"]

    def context(self, row):
        return {"schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"],
                "kind": "model", "caller": aa_policy._row_identity(row),
                "inherited_ceiling": row["score"]}

    def test_actual_policy_omitted_authority_is_eligible(self):
        absent = [r for r in self.rows if "authority_eligible" not in r]
        self.assertTrue(absent, "policy schema changed: no omitted authority rows")
        for row in absent:
            with self.subTest(config_id=row["id"]):
                self.assertIs(row["estimated"], False)
                self.assertFalse(row.get("score_source"))
                self.assertTrue(aa_policy.authority_eligible(row))
                self.assertTrue(caller_identity.is_authority_eligible_row(row))
                decision = aa_policy.decide_inherited(self.registry, self.digest,
                    caller=self.context(row), caller_sha256="synthetic-context")
                self.assertTrue(decision["allowed"], decision)
                self.assertEqual(decision["effective_ceiling"], row["score"])

    def test_actual_derived_callers_are_refused_by_both_engines(self):
        derived = [r for r in self.rows if r.get("authority_eligible") is False]
        self.assertTrue(derived, "policy schema changed: no derived rows")
        for row in derived:
            with self.subTest(config_id=row["id"]):
                self.assertIn(row.get("score_source"), ("upper-bound", "proxy"))
                self.assertFalse(aa_policy.authority_eligible(row))
                self.assertFalse(caller_identity.is_authority_eligible_row(row))
                args = dict(caller=self.context(row), caller_sha256="synthetic-context")
                decisions = [
                    aa_policy.decide(self.registry, self.digest, vendor=row["vendor"],
                        model=row["model"], effort=row["effort"], target_config=row["id"], **args),
                    aa_policy.decide_inherited(self.registry, self.digest, **args)]
                for decision in decisions:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["code"], "ineligible-caller-config")
                    self.assertIsNone(decision["effective_ceiling"])
                    self.assertIsNone(decision["child_context"])

    def test_derived_target_bound_keeps_transport_gate(self):
        measured = [r for r in self.rows if caller_identity.is_authority_eligible_row(r)]
        caller = max(measured, key=lambda r: (r["score"], r["id"]))
        for row in self.rows:
            if row.get("authority_eligible") is not False:
                continue
            with self.subTest(config_id=row["id"]):
                mapping = row["transport_mapping"]
                selectors = mapping.get("candidate_model_ids", [])
                self.assertTrue(selectors)
                model = mapping.get("runtime_model") or selectors[0]
                effort = mapping.get("runtime_effort", row["effort"])
                actual = aa_policy.decide(self.registry, self.digest,
                    vendor=row["vendor"], model=model, effort=effort, target_config=row["id"],
                    caller=self.context(caller), caller_sha256="synthetic-context")
                self.assertFalse(actual["allowed"])
                self.assertEqual(actual["code"], "runtime-mapping-unverified")
                # Only a local in-memory fixture supplies synthetic transport proof.
                # Real policy bytes, approval pins, scores and caller flags stay intact.
                synthetic = copy.deepcopy(self.registry)
                target = next(r for r in synthetic["scored_configs"] if r["id"] == row["id"])
                target["transport_mapping"].update(status="verified", runtime_verified=True,
                    runtime_model=model, runtime_effort=effort)
                if row["vendor"] == "grok":
                    target["transport_mapping"].update(selector_type="cli_reasoning_effort",
                                                        cli_flag="--reasoning-effort")
                decision = aa_policy.decide(synthetic, "synthetic-transport-only",
                    vendor=row["vendor"], model=model, effort=effort, target_config=row["id"],
                    caller=self.context(caller), caller_sha256="synthetic-context")
                self.assertTrue(decision["allowed"], decision)
                self.assertEqual(decision["target_score"], row["score"])
                self.assertEqual(decision["target_score_source"], row["score_source"])
                # A target bound does not become next-generation caller authority.
                inherited = aa_policy.decide_inherited(synthetic, "synthetic-transport-only",
                    caller=decision["child_context"], caller_sha256="synthetic-child")
                self.assertFalse(inherited["allowed"])
                self.assertEqual(inherited["code"], "ineligible-caller-config")


class CurrentTurnContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.digest = caller_identity.load_registry(ROOT / "config/aa-model-policy.json")

    def read(self, agy_dir, extra=None):
        environment = {"ANTIGRAVITY_CONVERSATION_ID": fixture.CONV_UUID}
        environment.update(extra or {})
        return caller_identity.read_caller(100, lookup=fixture._fake_chain(),
            environment=lambda pid: {"ANTIGRAVITY_CONVERSATION_ID": fixture.CONV_UUID},
            current_environment=environment, antigravity_dir=agy_dir, registry=self.registry)

    def test_persisted_high_model_cannot_prove_unpersisted_low_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            agy = Path(tmp) / "agy"
            fixture._write_synthetic_db(agy, fixture.CONV_UUID,
                [(1, fixture._build_executor_metadata(b"gemini-4-carbon-high"))])
            with patch.object(caller_identity, "_read_antigravity_conversation_db",
                    wraps=caller_identity._read_antigravity_conversation_db) as reader:
                with self.assertRaisesRegex(ValueError, "current-turn binding is unavailable"):
                    self.read(agy)
                reader.assert_not_called()

    def test_latest_parallel_or_newer_row_cannot_authorize_this_request(self):
        for count in (2, 7):
            with self.subTest(rows=count), tempfile.TemporaryDirectory() as tmp:
                agy = Path(tmp) / "agy"
                fixture._write_synthetic_db(agy, fixture.CONV_UUID,
                    [(i, fixture._build_executor_metadata(b"gemini-4-carbon-high")) for i in range(count)])
                with patch.object(caller_identity.sqlite3, "connect", wraps=caller_identity.sqlite3.connect) as connect:
                    with self.assertRaisesRegex(ValueError, "current-turn binding is unavailable"):
                        self.read(agy)
                    connect.assert_not_called()

    def test_self_asserted_turn_and_idx_do_not_establish_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            agy = Path(tmp) / "agy"
            fixture._write_synthetic_db(agy, fixture.CONV_UUID,
                [(999, fixture._build_executor_metadata(b"gemini-4-carbon-high"))])
            with self.assertRaisesRegex(ValueError, "current-turn binding is unavailable"):
                self.read(agy, {"ANTIGRAVITY_TURN_ID": "synthetic-current",
                                "ANTIGRAVITY_METADATA_IDX": "999"})

    def test_missing_database_still_refuses_before_database_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(caller_identity.sqlite3, "connect") as connect:
                with self.assertRaisesRegex(ValueError, "current-turn binding is unavailable"):
                    self.read(Path(tmp) / "never-created")
                connect.assert_not_called()

    def test_native_flags_are_explicit_unverified_and_cannot_grant_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agy = root / "agy"
            fixture._write_synthetic_db(agy, fixture.CONV_UUID,
                [(1, fixture._build_executor_metadata(b"gemini-4-carbon-high"))])
            out = root / "native.json"
            args = ["--registry", str(ROOT / "config/aa-model-policy.json"),
                    "--workdir", str(root), "--mode", "work", "--no-host-rows", "--out", str(out)]
            sink, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(sink), redirect_stderr(errors):
                rc = native_context.main(args, read_caller=lambda pid: self.read(agy))
            self.assertEqual(rc, 3)
            self.assertFalse(out.exists())
            with redirect_stdout(sink), redirect_stderr(errors):
                rc = native_context.main(args + ["--vendor", "gemini", "--model", "gemini-4-carbon",
                    "--harness", "antigravity-desktop", "--inherits-caller-runtime"],
                    read_caller=lambda pid: self.read(agy))
            self.assertEqual(rc, 0, errors.getvalue())
            capability = json.loads(out.read_text())
            self.assertIs(capability["caller_identity_verified"], False)
            self.assertNotIn("current_effort", capability)
            self.assertNotIn("inherited_ceiling", capability)
            self.assertTrue(capability["inherits_caller_runtime"])
            decision = aa_policy.decide_inherited(self.registry, self.digest, caller=None,
                caller_sha256=None, host_asserted={"vendor": capability["vendor"],
                                                  "model": capability["current_model"]})
            self.assertTrue(decision["allowed"])
            self.assertIs(decision["caller_identity_verified"], False)
            for key in ("caller_score", "inherited_ceiling", "effective_ceiling", "target_score", "child_context"):
                self.assertIsNone(decision[key], key)
            lane = aa_policy.decide(self.registry, self.digest, vendor="gemini", model="gemini-4-carbon",
                effort="high", caller=None, caller_sha256=None)
            self.assertFalse(lane["allowed"])
            self.assertEqual(lane["code"], "missing-caller-context")

    def test_explicit_gemini_cli_selector_regression(self):
        table = {100: (80, ["python3", "-I", "-B"]),
                 80: (1, ["/synthetic/agy", "--model", "gemini-4-carbon-high"]), 1: (0, ["launchd"])}
        result = caller_identity.read_caller(100, lookup=table.get,
            environment=lambda pid: {}, current_environment={}, registry=self.registry)
        self.assertEqual(result[1], ("gemini", "gemini-4-carbon-high", None))


if __name__ == "__main__":
    unittest.main()
