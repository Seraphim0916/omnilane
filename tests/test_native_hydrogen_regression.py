"""Offline regressions: Gemini 4 Hydrogen is never dispatched without transport proof."""

import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import copy
import hashlib
import io
import itertools
import json
import os
import re
import socket
import unittest
from unittest.mock import patch

from tests import test_aa_policy as policy_tests
from tests import test_aa_routing_update as routing_tests
from tests import test_native_executor as fixtures

aa_policy = policy_tests.aa_policy

# config id -> agy selector; AA's unsuffixed gemini-4-hydrogen row is the xhigh thinking level.
HYDROGEN = (("gemini/gemini-4-hydrogen", "gemini-4-hydrogen-xhigh"),
            ("gemini/gemini-4-hydrogen-high", "gemini-4-hydrogen-high"),
            ("gemini/gemini-4-hydrogen-medium", "gemini-4-hydrogen-medium"),
            ("gemini/gemini-4-hydrogen-low", "gemini-4-hydrogen-low"))


class HydrogenRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads((fixtures.ROOT / "config/aa-model-policy.json").read_text())

    def test_four_scored_rows_with_encoded_selectors(self):
        rows = {row["id"]: row for row in self.registry["scored_configs"]}
        expected = {"gemini/gemini-4-hydrogen": ("xhigh", 54), "gemini/gemini-4-hydrogen-high": ("high", 51),
                    "gemini/gemini-4-hydrogen-medium": ("medium", 41), "gemini/gemini-4-hydrogen-low": ("low", 27)}
        for config_id, selector in HYDROGEN:
            with self.subTest(config_id=config_id):
                row = rows[config_id]
                self.assertEqual((row["effort"], row["score"]), expected[config_id])
                self.assertEqual(row["transport_mapping"]["candidate_model_ids"], [selector])
                self.assertFalse(row["transport_mapping"]["runtime_verified"])
                self.assertIn(config_id, aa_policy.HUMAN_TRANSPORT_PROOF_REQUIRED)

    def test_hydrogen_high_sits_right_after_argon(self):
        # Hydrogen high never precedes Argon high, so every `--vendor gemini` call Argon serves
        # today stays on Argon.
        hydrogen, argon = "gemini gemini-4-hydrogen-high -", "gemini gemini-4-argon-high -"
        lanes = {}
        for line in (fixtures.ROOT / "routing.yaml").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([a-z-]+):\s*([^#]+)", line)
            if match:
                lanes[match.group(1)] = [item.strip() for item in match.group(2).split("|")]
        with_argon = [lane for lane, chain in lanes.items() if argon in chain]
        self.assertEqual(len(with_argon), 11)
        for lane, chain in lanes.items():
            with self.subTest(lane=lane):
                others = [item for item in chain if "gemini-4-hydrogen" in item and item != hydrogen]
                self.assertEqual(others, [])
                if lane in with_argon:
                    self.assertEqual(chain.count(hydrogen), 1)
                    self.assertEqual(chain.index(argon) + 1, chain.index(hydrogen))
                else:
                    self.assertNotIn(hydrogen, chain)


class HydrogenPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = json.loads((fixtures.ROOT / "config/aa-model-policy.json").read_text())

    def setUp(self):
        self.registry = copy.deepcopy(self.real)

    def human(self, model, target_config=None):
        return aa_policy.decide(
            self.registry, "fixture-sha", vendor="gemini", model=model, effort=None,
            caller=None, caller_sha256=None, operator_asserted_human=True,
            target_config=target_config)

    def prove(self, config_id, selector):
        row = next(row for row in self.registry["scored_configs"] if row["id"] == config_id)
        row["transport_mapping"].update(status="verified", runtime_verified=True, runtime_model=selector,
                                        runtime_effort=row["effort"], selector_type="model_id_encoded_effort")

    def test_human_exemption_refuses_unproven_hydrogen(self):
        for config_id, selector in HYDROGEN:
            for target_config in (None, config_id):
                with self.subTest(selector=selector, target_config=target_config):
                    result = self.human(selector, target_config)
                    self.assertFalse(result["allowed"])
                    self.assertEqual(result["failed_gate"], "target-transport")
                    self.assertEqual(result["code"], "runtime-mapping-unverified")
                    self.assertIn("omnilane resign --vendor gemini", result["next_command"])

    def test_human_exemption_allows_hydrogen_once_proven(self):
        for config_id, selector in HYDROGEN:
            self.prove(config_id, selector)
            with self.subTest(selector=selector):
                result = self.human(selector)
                self.assertTrue(result["allowed"])
                self.assertEqual(result["code"], "operator-asserted-human-exemption")


class HydrogenDispatchTests(unittest.TestCase):
    """dispatch.sh with the quick-start human assertion and an installed agy, but no proof."""

    def setUp(self):
        self.fixture = routing_tests.AARoutingUpdateTests("test_all_twelve_default_lane_selections")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_explicit_hydrogen_selector_is_refused(self):
        for lane in ("hardest-coding", "ui-draft", "consult"):
            for _, selector in HYDROGEN:
                with self.subTest(lane=lane, selector=selector):
                    result = self.fixture._run("--dry-run", "--vendor", "gemini", "--model", selector,
                                               lane, "fixture")
                    self.assertEqual(3, result.returncode, result.stdout + result.stderr)
                    self.assertNotIn("dry_run=yes", result.stdout)
                    self.assertIn("target-transport", result.stderr)
                    self.assertIn("runtime-mapping-unverified", result.stderr)

    def test_pinned_gemini_still_selects_argon_without_hydrogen_proof(self):
        # hardest-coding and ui-draft start their Gemini rows with Carbon; the pin skips the
        # unproven Carbon rows there and reaches Argon like every other lane.
        for lane in routing_tests.EXPECTED_DEFAULTS:
            if lane == "arbitrate":
                continue
            with self.subTest(lane=lane):
                dry = self.fixture._run("--dry-run", "--vendor", "gemini", lane, "fixture")
                self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
                self.assertIn("model=gemini-4-argon-high\n", dry.stdout)

    def test_lane_dispatch_never_selects_hydrogen(self):
        for lane in routing_tests.EXPECTED_DEFAULTS:
            if lane == "arbitrate":
                continue
            with self.subTest(lane=lane):
                dry = self.fixture._run("--dry-run", lane, "fixture")
                self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
                self.assertNotIn("gemini-4-hydrogen", dry.stdout)


def _lanes():
    lanes = {}
    for line in (fixtures.ROOT / "routing.yaml").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-z-]+):\s*([^#]+)", line)
        if match:
            lanes[match.group(1)] = [item.strip() for item in match.group(2).split("|")]
    return lanes


class VendorPinSkipsUnprovenRowsTests(unittest.TestCase):
    """`--vendor gemini` without --model passes over same-vendor rows this host has no proof for."""

    ARGON = "gemini gemini-4-argon-high -"
    CARBON_LANES = {"hardest-coding": "gemini-4-carbon-high", "ui-draft": "gemini-4-carbon-medium"}
    SKIP = "no transport proof on this host"

    def setUp(self):
        self.fixture = routing_tests.AARoutingUpdateTests("test_all_twelve_default_lane_selections")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.registry = json.loads((fixtures.ROOT / "config/aa-model-policy.json").read_text())
        self.lanes = _lanes()

    def overlay(self, *selectors):
        # A valid offline selector-proof fixture, never host/provider evidence.
        rows = {row["transport_mapping"]["candidate_model_ids"][0]: row
                for row in self.registry["scored_configs"]
                if row["vendor"] == "gemini" and row["transport_mapping"].get("candidate_model_ids")}
        evidence = self.fixture.base / "selector-contract.txt"
        evidence.write_text("offline Gemini selector contract fixture\n")
        mappings = []
        for selector in selectors:
            row = rows[selector]
            mappings.append({
                "config_id": row["id"],
                "identity": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
                "runtime_model": selector, "runtime_effort": row["effort"],
                "selector_type": "model_id_encoded_effort",
                "verification": "request-selector-contract",
            })
        overlay = {
            "schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"],
            "host": socket.gethostname(),
            "evidence": [{"path": str(evidence), "vendor": "gemini",
                          "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
            "mappings": mappings,
        }
        path = self.fixture.base / "overlay.json"
        path.write_text(json.dumps(overlay))
        env = self.fixture.env.copy()
        env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(path)
        return env

    def model_caller(self, env, config_id):
        row = next(row for row in self.registry["scored_configs"] if row["id"] == config_id)
        caller = {"schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"], "kind": "model",
                  "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
                  "inherited_ceiling": row["score"]}
        path = self.fixture.base / "caller.json"
        path.write_text(json.dumps(caller))
        env = env.copy()
        env["OMNILANE_AA_OPERATOR_ASSERTED_HUMAN"] = "0"
        return env, ["--caller-context", str(path)]

    def candidate(self, lane, spec):
        chain = self.lanes[lane]
        return f"candidate={chain.index(spec) + 1}/{len(chain)}\n"

    def test_unproven_carbon_lanes_fall_through_to_argon(self):
        for lane in self.CARBON_LANES:
            with self.subTest(lane=lane):
                dry = self.fixture._run("--dry-run", "--vendor", "gemini", lane, "fixture")
                self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
                self.assertIn("model=gemini-4-argon-high\n", dry.stdout)
                self.assertIn(self.candidate(lane, self.ARGON), dry.stdout)
                self.assertIn(self.SKIP, dry.stderr)
                self.assertIn("runtime-mapping-unverified", dry.stderr)
                skipped = [spec for spec in self.lanes[lane] if "gemini-4-carbon" in spec]
                self.assertEqual(len(skipped), dry.stderr.count(self.SKIP))

    def test_proven_carbon_is_still_selected(self):
        env = self.overlay("gemini-4-carbon-high", "gemini-4-carbon-low", "gemini-4-carbon-medium")
        for lane, selector in self.CARBON_LANES.items():
            with self.subTest(lane=lane):
                dry = self.fixture._run("--dry-run", "--vendor", "gemini", lane, "fixture", env=env)
                self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
                self.assertIn(f"model={selector}\n", dry.stdout)
                self.assertIn(self.candidate(lane, f"gemini {selector} -"), dry.stdout)
                self.assertNotIn(self.SKIP, dry.stderr)

    def test_explicit_model_still_refuses_without_proof(self):
        for lane in self.CARBON_LANES:
            with self.subTest(lane=lane):
                result = self.fixture._run("--dry-run", "--vendor", "gemini", "--model",
                                           "gemini-4-carbon-high", lane, "fixture")
                self.assertEqual(3, result.returncode, result.stdout + result.stderr)
                self.assertNotIn("dry_run=yes", result.stdout)
                self.assertIn("target-transport", result.stderr)
                self.assertNotIn(self.SKIP, result.stderr)

    CEILING = "above the caller ceiling"
    PROVEN = ("gemini-4-carbon-high", "gemini-4-carbon-low", "gemini-4-argon-high",
              "gemini-4-hydrogen-high", "gemini-3.8-flash-medium")

    def test_ceiling_52_passes_proven_carbon_and_selects_argon(self):
        env, caller = self.model_caller(self.overlay(*self.PROVEN), "claude/claude-opus-5-5-high")
        dry = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "hardest-coding",
                                "fixture", env=env)
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertIn("model=gemini-4-argon-high\n", dry.stdout)
        self.assertIn(self.candidate("hardest-coding", self.ARGON), dry.stdout)
        self.assertIn("score 60 is above the caller ceiling 52", dry.stderr)
        self.assertIn("score 54 is above the caller ceiling 52", dry.stderr)
        self.assertEqual(2, dry.stderr.count(self.CEILING))
        self.assertNotIn(self.SKIP, dry.stderr)

    def test_ceiling_49_passes_argon_too(self):
        env, caller = self.model_caller(self.overlay(*self.PROVEN), "claude/claude-fable-5-1")
        dry = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "hardest-coding",
                                "fixture", env=env)
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertIn("model=gemini-3.8-flash-medium\n", dry.stdout)
        self.assertIn(self.candidate("hardest-coding", "gemini gemini-3.8-flash-medium -"), dry.stdout)
        for score in (60, 54, 51, 50):
            self.assertIn(f"score {score} is above the caller ceiling 49", dry.stderr)

    def test_ceiling_49_without_a_usable_row_reports_the_first_refusal(self):
        env, caller = self.model_caller(self.overlay(*self.PROVEN[:4]), "claude/claude-fable-5-1")
        result = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "hardest-coding",
                                   "fixture", env=env)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("dry_run=yes", result.stdout)
        refusal = next(json.loads(line) for line in result.stderr.splitlines()
                       if line.startswith("{") and '"failed_gate"' in line)
        self.assertEqual("target-above-effective-ceiling", refusal["code"])
        self.assertEqual("gemini-4-carbon-high", refusal["target_request"]["model"])

    def test_explicit_model_above_the_ceiling_still_refuses(self):
        env, caller = self.model_caller(self.overlay(*self.PROVEN), "claude/claude-opus-5-5-high")
        result = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "--model",
                                   "gemini-4-carbon-high", "hardest-coding", "fixture", env=env)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("dry_run=yes", result.stdout)
        self.assertIn("target-above-effective-ceiling", result.stderr)
        self.assertNotIn(self.CEILING, result.stderr)

    def test_model_caller_reaches_proven_argon_past_unproven_carbon(self):
        env = self.overlay("gemini-4-argon-high")
        env, caller = self.model_caller(env, "gemini/gemini-4-argon")
        dry = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "hardest-coding",
                                "fixture", env=env)
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertIn("model=gemini-4-argon-high\n", dry.stdout)
        self.assertIn(self.candidate("hardest-coding", self.ARGON), dry.stdout)

    def test_all_unproven_rows_report_the_first_refusal(self):
        # A model caller has no human exemption, so nothing in the Gemini block is proven.
        env, caller = self.model_caller(self.fixture.env, "gemini/gemini-4-argon")
        result = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "hardest-coding",
                                   "fixture", env=env)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("dry_run=yes", result.stdout)
        refusal = next(json.loads(line) for line in result.stderr.splitlines()
                       if line.startswith("{") and '"failed_gate"' in line)
        self.assertEqual("target-transport", refusal["failed_gate"])
        self.assertEqual("gemini-4-carbon-high", refusal["target_request"]["model"])

    def test_json_only_refusal_keeps_the_explicit_refusal_shape(self):
        # JSON-only refusals start with the decision line; held skip notes must not precede it.
        env, caller = self.model_caller(self.fixture.env, "gemini/gemini-4-argon")
        env["OMNILANE_REFUSAL_JSON_ONLY"] = "1"
        pinned = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "hardest-coding",
                                   "fixture", env=env)
        explicit = self.fixture._run("--dry-run", *caller, "--vendor", "gemini", "--model",
                                     "gemini-4-carbon-high", "hardest-coding", "fixture", env=env)
        self.assertEqual(3, pinned.returncode, pinned.stdout + pinned.stderr)
        self.assertNotIn(self.SKIP, pinned.stderr)
        self.assertEqual(len(explicit.stderr.splitlines()), len(pinned.stderr.splitlines()))
        refusal = json.loads(pinned.stderr.splitlines()[0])
        self.assertEqual("gemini-4-carbon-high", refusal["target_request"]["model"])
        self.assertEqual("target-transport", refusal["failed_gate"])

    def test_argon_led_lanes_do_not_skip(self):
        argon_led = [lane for lane, chain in self.lanes.items()
                     if self.ARGON in chain and lane not in self.CARBON_LANES]
        self.assertEqual(9, len(argon_led))
        for lane in argon_led:
            with self.subTest(lane=lane):
                dry = self.fixture._run("--dry-run", "--vendor", "gemini", lane, "fixture")
                self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
                self.assertIn("model=gemini-4-argon-high\n", dry.stdout)
                self.assertIn(self.candidate(lane, self.ARGON), dry.stdout)
                self.assertNotIn(self.SKIP, dry.stderr)


class NativeHydrogenProofTests(unittest.TestCase):
    EFFORTS = ("xhigh", "high", "medium", "low")

    def setUp(self):
        self.fixture = fixtures.NativeExecutorTests("test_native_eligible_and_pending")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.native = self.fixture.load_native()
        self.registry = json.loads(
            (fixtures.ROOT / "config/aa-model-policy.json").read_text())
        self.fixture.env["OMNILANE_REFUSAL_JSON_ONLY"] = "1"

    def prepare(self, effort, requested_model, caller_kind, *, proof=False, fallback=False):
        model = "gemini-4-hydrogen-" + effort
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
                       if row["model"] == "gemini-4-hydrogen" and row["effort"] == effort)
            evidence = self.fixture.base / "selector-contract.txt"
            evidence.write_text("offline Hydrogen selector contract fixture\n")
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

    def test_unproven_hydrogen_matrix(self):
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

    def test_unproven_hydrogen_cannot_fall_back_to_cli(self):
        for effort, request, caller in self.cases():
            with self.subTest(effort=effort, request=request, caller=caller):
                args, model = self.prepare(effort, request, caller, fallback=True)
                rc, stdout, stderr = self.invoke(args)
                self.assertEqual(rc, 3, stdout + stderr)
                self.assertEqual(stdout, "")
                decision = json.loads(stderr)
                self.assertEqual(decision["target_request"]["model"], model)
                self.assertEqual(decision["failed_gate"], "target-transport")

    def test_proven_hydrogen_matrix_records_final_model(self):
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
                self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
