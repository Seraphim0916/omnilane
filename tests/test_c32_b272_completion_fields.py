import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class CompletionFieldsTests(unittest.TestCase):
    def setUp(self):
        self.state = {"job_id": "20261027-000000-1-1", "vendor": "codex",
                      "model": "fixture-model", "effort": "high", "harness": "codex"}
        self.value = {
            "schema_version": 1, "job_id": self.state["job_id"], "agent_id": "worker",
            "runtime": {key: self.state[key] for key in
                        ("vendor", "model", "effort", "harness")},
            "outcome": "success", "result": "done", "evidence": ["fixture evidence"],
        }
        self.value["runtime"]["backend"] = "collaboration.spawn_agent"

    def assert_rejected_unchanged(self, value):
        before = copy.deepcopy(value)
        state_before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "^missing or unknown JSON fields$"):
            native.validate_completion(value, self.state)
        self.assertEqual(value, before)
        self.assertEqual(self.state, state_before)

    def test_each_required_runtime_field(self):
        for key in ("vendor", "model", "effort", "harness", "backend"):
            with self.subTest(missing=key):
                value = copy.deepcopy(self.value)
                del value["runtime"][key]
                self.assert_rejected_unchanged(value)

    def test_unknown_runtime_field(self):
        self.value["runtime"]["unexpected"] = "extra"
        self.assert_rejected_unchanged(self.value)

    def test_complete_runtime_accepts_reordered_fields(self):
        original = copy.deepcopy(self.value)
        self.value["runtime"] = dict(reversed(list(self.value["runtime"].items())))
        order = list(self.value["runtime"])
        self.assertNotEqual(order, list(original["runtime"]))
        state_before = copy.deepcopy(self.state)
        result = native.validate_completion(self.value, self.state)
        self.assertEqual(result, original)
        self.assertEqual(self.value, original)
        self.assertEqual(list(self.value["runtime"]), order)
        self.assertEqual(self.state, state_before)


if __name__ == "__main__":
    unittest.main()
