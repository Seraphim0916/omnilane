import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class InheritedEffortTests(unittest.TestCase):
    def setUp(self):
        self.state = {"job_id": "20261027-000000-1-1", "vendor": "codex",
                      "model": "fixture-model", "effort": "high",
                      "harness": "codex", "inherit": True}

    def completion(self, effort):
        return {
            "schema_version": 1, "job_id": self.state["job_id"], "agent_id": "worker",
            "runtime": {"vendor": "codex", "model": "fixture-model", "effort": effort,
                        "harness": "codex", "backend": "collaboration.spawn_agent"},
            "outcome": "success", "result": "done", "evidence": ["fixture evidence"],
        }

    def test_inherited_effort_accepts_unknown_and_256_characters(self):
        for effort in ("unknown", "x" * 256):
            with self.subTest(effort=effort):
                self.assertNotEqual(effort, self.state["effort"])
                result = native.validate_completion(self.completion(effort), self.state)
                self.assertEqual(result["runtime"]["effort"], effort)

    def test_inherited_effort_still_requires_bounded_nonempty_text(self):
        for effort in ("", "   ", None, 1, True, [], {}, "x" * 257):
            with self.subTest(effort=effort):
                with self.assertRaisesRegex(ValueError, "^invalid runtime effort$"):
                    native.validate_completion(self.completion(effort), self.state)

    def test_inherited_effort_rejects_control_characters(self):
        for effort in ("high\n", "hi\tgh", "hi\x00gh", "hi\x7fgh"):
            with self.subTest(effort=effort):
                with self.assertRaisesRegex(ValueError, "^control character in runtime effort$"):
                    native.validate_completion(self.completion(effort), self.state)

    def test_non_inherited_effort_still_requires_equality(self):
        for inherit in (False, None):
            with self.subTest(inherit=inherit):
                if inherit is None:
                    self.state.pop("inherit", None)
                else:
                    self.state["inherit"] = inherit
                with self.assertRaisesRegex(ValueError, "^runtime effort mismatch$"):
                    native.validate_completion(self.completion("unknown"), self.state)
                result = native.validate_completion(self.completion("high"), self.state)
                self.assertEqual(result["runtime"]["effort"], "high")


if __name__ == "__main__":
    unittest.main()
