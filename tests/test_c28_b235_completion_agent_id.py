import offline_env  # Activate suite isolation for direct file execution.

import copy
import importlib.util
from pathlib import Path
import unittest


class CompletionAgentIdTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "native_b235", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
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

    def test_legal_identifiers_preserve_existing_alphabet_and_input(self):
        for agent_id in ("a" * 256, "/root/reviewer", "_worker", "a:b",
                         "0", "Z", "/", "_", "aA0._:/-", "/root/" + "a" * 250):
            with self.subTest(agent_id=agent_id):
                self.value["agent_id"] = agent_id
                before = copy.deepcopy((self.value, self.state))
                self.assertIs(self.native.validate_completion(self.value, self.state), self.value)
                self.assertEqual((self.value, self.state), before)

    def test_invalid_identifiers_are_rejected_without_mutation(self):
        invalid = ["a" * 257, "/root/" + "a" * 251, ":worker", ".worker",
                   "-worker", "", "worker name", "worker@host", None, 7]
        invalid += ["worker" + chr(code) + "tail" for code in list(range(32)) + [127]]
        for agent_id in invalid:
            with self.subTest(agent_id=agent_id):
                self.value["agent_id"] = agent_id
                before = copy.deepcopy((self.value, self.state))
                with self.assertRaisesRegex(ValueError, "agent_id"):
                    self.native.validate_completion(self.value, self.state)
                self.assertEqual((self.value, self.state), before)


if __name__ == "__main__":
    unittest.main()
