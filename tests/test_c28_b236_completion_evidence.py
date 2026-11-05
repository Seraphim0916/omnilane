import offline_env  # Activate suite isolation for direct file execution.

import copy
import importlib.util
from pathlib import Path
import unittest


class CompletionEvidenceTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "native_b236", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)
        self.state = dict(job_id="20261026-120000-1-1", vendor="codex",
                          model="fixture-model", effort="high", harness="fixture")
        self.value = dict(schema_version=1, job_id=self.state["job_id"],
                          agent_id="/root/worker", outcome="success", result="verified",
                          evidence=["test:passed"],
                          runtime={key: self.state[key] for key in
                                   ("vendor", "model", "effort", "harness")})
        self.value["runtime"]["backend"] = "fixture-backend"

    def test_capacity_and_item_length_limits_preserve_order(self):
        for evidence in (["evidence:%03d" % i for i in reversed(range(128))],
                         ["x" * 4096], ["z:last", "a:first", "m:middle"]):
            with self.subTest(count=len(evidence), first_length=len(evidence[0])):
                self.value["evidence"] = evidence
                before = copy.deepcopy((self.value, self.state))
                result = self.native.validate_completion(self.value, self.state)
                self.assertIs(result, self.value)
                self.assertIs(result["evidence"], evidence)
                self.assertEqual((self.value, self.state), before)

    def test_invalid_evidence_is_rejected_without_mutation(self):
        cases = [["item:%d" % i for i in range(129)], ["x" * 4097],
                 ["repeat", "other", "repeat"], [], "not-a-list", ("tuple",), None]
        cases += [["valid", invalid] for invalid in
                  (None, 1, True, {}, [], "", "   ", "line\nbreak", "tab\there",
                   "nul\x00here", "del\x7fhere")]
        for evidence in cases:
            with self.subTest(evidence_type=type(evidence).__name__, evidence=str(evidence)[:80]):
                self.value["evidence"] = evidence
                before = copy.deepcopy((self.value, self.state))
                with self.assertRaisesRegex(ValueError, "evidence"):
                    self.native.validate_completion(self.value, self.state)
                self.assertEqual((self.value, self.state), before)


if __name__ == "__main__":
    unittest.main()
