import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
# Run as a script, aa_policy has scripts/lib first on sys.path for its lazy imports.
sys.path.insert(0, str(ROOT / "scripts/lib"))
REGISTRY_PATH = ROOT / "config/aa-model-policy.json"
SPEC = importlib.util.spec_from_file_location("aa_policy", ROOT / "scripts/lib/aa_policy.py")
aa_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aa_policy)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = json.loads(REGISTRY_PATH.read_text())

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="aa-inherited-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.registry = copy.deepcopy(self.real)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)

    def row(self, config_id):
        return next(row for row in self.registry["scored_configs"] if row["id"] == config_id)

    def model_rows(self, vendor, model):
        return [row for row in self.registry["scored_configs"]
                if row["vendor"] == vendor and row["model"] == model]

    def context(self, row, ceiling=None, **extra):
        value = {
            "schema_version": 1,
            "snapshot_id": self.registry["snapshot"]["id"],
            "kind": "model",
            "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
            "inherited_ceiling": row["score"] if ceiling is None else ceiling,
        }
        value.update(extra)
        return value

    def inherited(self, caller=None, **kwargs):
        return aa_policy.decide_inherited(self.registry, "registry-sha", caller=caller,
                                          caller_sha256="caller-sha" if caller else None, **kwargs)


class DecideInheritedTests(Fixture):
    def test_human_operator_has_no_runtime_to_inherit(self):
        value = self.inherited(operator_asserted_human=True,
                               host_asserted={"vendor": "claude", "model": "m"})
        self.assertFalse(value["allowed"])
        self.assertEqual(value["code"], "inherit-requires-model-caller")
        self.assertEqual(value["caller_kind"], "operator-asserted-human")
        self.assertEqual(value["failed_gate"], "caller-identity")

    def test_unreadable_caller_on_the_hosts_word_is_unverified(self):
        value = self.inherited(host_asserted={"vendor": "claude", "model": "claude-opus-5-5"})
        self.assertTrue(value["allowed"])
        self.assertEqual(value["code"], "native-inherited-unverified-caller")
        self.assertFalse(value["caller_identity_verified"])
        self.assertEqual(value["caller_identity_source"], "host-asserted")
        self.assertEqual(value["target"], {"inherit": True, "vendor": "claude", "model": "claude-opus-5-5"})
        self.assertIsNone(value["child_context"])
        self.assertIsNone(value["effective_ceiling"])
        self.assertIsNone(value["target_config_id"])

    def test_no_caller_and_no_host_statement_is_denied(self):
        value = self.inherited()
        self.assertFalse(value["allowed"])
        self.assertEqual(value["code"], "missing-caller-context")
        self.assertIn("--inherits-caller-runtime", value["next_command"])

    def test_verified_caller_inherits_its_own_score_and_narrowed_ceiling(self):
        row = self.row("claude/claude-opus-5-5")
        value = self.inherited(self.context(row, ceiling=row["score"] - 3))
        self.assertTrue(value["allowed"])
        self.assertEqual(value["code"], "native-inherited-allowed")
        self.assertTrue(value["caller_identity_verified"])
        self.assertEqual(value["caller_context_sha256"], "caller-sha")
        self.assertFalse(value["caller_degraded"])
        self.assertEqual(value["caller_score"], row["score"])
        self.assertEqual(value["effective_ceiling"], row["score"] - 3)
        self.assertEqual(value["child_context"]["inherited_ceiling"], row["score"] - 3)
        self.assertNotIn("effort_unverified", value["child_context"])
        self.assertEqual(value["target"], {"inherit": True, "vendor": "claude", "model": "claude-opus-5-5"})

    def test_effort_unverified_caller_must_name_the_model_floor(self):
        rows = self.model_rows("claude", "claude-opus-5-5")
        floor = min(rows, key=lambda r: r["score"])
        top = max(rows, key=lambda r: r["score"])
        self.assertNotEqual(floor["score"], top["score"])
        denied = self.inherited(self.context(top, effort_unverified=True))
        self.assertEqual((denied["allowed"], denied["code"]), (False, "invalid-degraded-caller"))
        allowed = self.inherited(self.context(floor, effort_unverified=True))
        self.assertTrue(allowed["allowed"])
        self.assertTrue(allowed["caller_degraded"])
        self.assertTrue(allowed["child_context"]["effort_unverified"])

    def test_unknown_caller_uses_the_registry_reason_when_one_exists(self):
        unknown = self.registry["unknown_configs"][0]
        value = self.inherited(self.context(dict(unknown, score=1)))
        self.assertEqual((value["allowed"], value["code"]), (False, "unknown-caller-config"))
        self.assertEqual(value["message"], unknown["reason"])
        row = dict(self.row("claude/claude-opus-5-5"), model="claude-nonexistent")
        value = self.inherited(self.context(row))
        self.assertEqual(value["code"], "unknown-caller-config")
        self.assertIn("not uniquely scored", value["message"])

    def test_duplicate_scored_row_is_ambiguous(self):
        row = self.row("claude/claude-opus-5-5")
        self.registry["scored_configs"].append(dict(row, id="claude/duplicate"))
        value = self.inherited(self.context(row))
        self.assertEqual((value["allowed"], value["code"]), (False, "ambiguous-caller-config"))


class PublishContextTests(Fixture):
    def publish_dir(self):
        directory = self.base / "publish"
        directory.mkdir()
        return directory

    def test_publishes_registry_authorizer_child_and_lineage(self):
        row = self.row("claude/claude-opus-5-5")
        caller = self.context(row)
        decision = self.inherited(caller)
        directory = self.publish_dir()
        lineage = aa_policy.publish_context(directory, self.registry, caller, decision, REGISTRY_PATH)
        self.assertEqual((directory / "aa-registry.json").read_bytes(), REGISTRY_PATH.read_bytes())
        self.assertEqual(lineage["registry_sha256"], aa_policy.APPROVED_REGISTRY_SHA256)
        self.assertEqual(json.loads((directory / "aa-authorizer.json").read_text()), caller)
        self.assertEqual(lineage["authorizer_sha256"],
                         hashlib.sha256((directory / "aa-authorizer.json").read_bytes()).hexdigest())
        self.assertEqual(json.loads((directory / "aa-child-context.json").read_text()),
                         decision["child_context"])
        self.assertFalse(lineage["operator_asserted_human"])
        self.assertNotIn("transport_overlay_sha256", lineage)
        self.assertEqual(json.loads((directory / "aa-lineage.json").read_text()), lineage)
        self.assertEqual(json.loads((directory / "aa-decision.json").read_text()), decision)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), [
            "aa-authorizer.json", "aa-child-context.json", "aa-decision.json",
            "aa-lineage.json", "aa-registry.json"])

    def test_default_source_and_operator_decision_have_no_authorizer(self):
        decision = aa_policy.decide(self.registry, "sha", vendor="claude", model="m", effort=None,
                                    caller=None, caller_sha256=None, operator_asserted_human=True)
        directory = self.publish_dir()
        lineage = aa_policy.publish_context(directory, self.registry, None, decision)
        self.assertTrue(lineage["operator_asserted_human"])
        self.assertIsNone(lineage["authorizer_sha256"])
        self.assertIsNone(lineage["child_context"])
        self.assertFalse((directory / "aa-authorizer.json").exists())
        self.assertFalse((directory / "aa-child-context.json").exists())

    def test_overlay_is_copied_into_the_lineage(self):
        overlay = self.base / "overlay.json"
        overlay.write_text(json.dumps({"schema_version": 1, "mappings": []}))
        os.environ["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(overlay)
        self.registry["_transport_compatibility_sha256"] = "compat"
        decision = self.inherited(host_asserted={"vendor": "claude", "model": "m"})
        directory = self.publish_dir()
        lineage = aa_policy.publish_context(directory, self.registry, None, decision, REGISTRY_PATH)
        copied = directory / "aa-transport-overlay.json"
        self.assertEqual(json.loads(copied.read_text()), {"schema_version": 1, "mappings": []})
        self.assertEqual(lineage["transport_overlay_sha256"], hashlib.sha256(copied.read_bytes()).hexdigest())
        self.assertEqual(lineage["transport_compatibility_sha256"], "compat")
        self.assertEqual(lineage["transport_carry_forward"], [])

    def test_refusals(self):
        allowed = self.inherited(host_asserted={"vendor": "claude", "model": "m"})
        with self.assertRaisesRegex(aa_policy.PolicyError, "invalid context publication directory"):
            aa_policy.publish_context(self.base / "absent", self.registry, None, allowed, REGISTRY_PATH)
        link = self.base / "link"
        link.symlink_to(self.publish_dir())
        with self.assertRaisesRegex(aa_policy.PolicyError, "invalid context publication directory"):
            aa_policy.publish_context(link, self.registry, None, allowed, REGISTRY_PATH)
        with self.assertRaisesRegex(aa_policy.PolicyError, "cannot publish a denied decision"):
            aa_policy.publish_context(self.base / "publish", self.registry, None, self.inherited(), REGISTRY_PATH)
        altered = self.base / "altered.json"
        altered.write_bytes(REGISTRY_PATH.read_bytes() + b"\n")
        with self.assertRaisesRegex(aa_policy.PolicyError, "unapproved AA registry snapshot publication"):
            aa_policy.publish_context(self.base / "publish", self.registry, None, allowed, altered)
        self.assertEqual(list((self.base / "publish").iterdir()), [])

    def test_atomic_bytes_leaves_no_temporary_on_failure(self):
        directory = self.publish_dir()
        with mock.patch.object(aa_policy.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                aa_policy.atomic_bytes(directory / "x.json", b"{}")
        self.assertEqual(list(directory.iterdir()), [])


class MainTests(Fixture):
    def run_main(self, *argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = aa_policy.main(["--registry", str(REGISTRY_PATH), *argv])
        return code, json.loads(stdout.getvalue())

    def test_operator_assertion_allows_and_publishes(self):
        directory = self.base / "publish"
        directory.mkdir()
        code, value = self.run_main("--operator-asserted-human", "--vendor", "claude",
                                    "--model", "claude-opus-5-5", "--effort", "-",
                                    "--publish-dir", str(directory))
        self.assertEqual(code, 0)
        self.assertEqual(value["code"], "operator-asserted-human-exemption")
        self.assertIsNone(value["target_request"]["effort"])
        self.assertTrue((directory / "aa-lineage.json").is_file())

    def test_caller_context_decision_is_exit_three_when_denied(self):
        row = self.row("claude/claude-opus-5-5")
        caller = self.base / "caller.json"
        caller.write_text(json.dumps(self.context(row)))
        directory = self.base / "publish"
        directory.mkdir()
        code, value = self.run_main("--caller-context", str(caller), "--vendor", "claude",
                                    "--model", "claude-opus-5-5", "--effort", "max",
                                    "--publish-dir", str(directory))
        self.assertEqual(code, 3)
        self.assertFalse(value["allowed"])
        self.assertEqual(list(directory.iterdir()), [])

    def test_missing_caller_is_exit_three(self):
        code, value = self.run_main("--vendor", "claude", "--model", "m", "--effort", "")
        self.assertEqual((code, value["code"]), (3, "missing-caller-context"))

    def test_invalid_inputs_are_exit_two_with_guidance(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = aa_policy.main(["--registry", str(self.base / "absent.json"),
                                   "--vendor", "v", "--model", "m", "--effort", "e"])
        value = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(value["code"], "invalid-policy-input")
        self.assertEqual(value["message"], "invalid or inaccessible AA policy input")
        self.assertEqual(value["next_command"], "omnilane doctor")

        code, value = self.run_main("--expected-registry-sha256", "0" * 64,
                                    "--vendor", "v", "--model", "m", "--effort", "e")
        self.assertEqual(code, 2)
        self.assertEqual(value["message"], "AA registry changed after initial decision")

    def test_non_finite_json_numbers_are_policy_errors(self):
        for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.subTest(text=text):
                path = self.base / "nan.json"
                path.write_text(text)
                with self.assertRaisesRegex(aa_policy.PolicyError, "invalid JSON number"):
                    aa_policy._read_json(path)
        path = self.base / "bad.json"
        path.write_bytes(b"\xff")
        with self.assertRaisesRegex(aa_policy.PolicyError, "invalid JSON input"):
            aa_policy._read_json(path)


if __name__ == "__main__":
    unittest.main()
