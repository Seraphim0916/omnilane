import offline_env  # Activate suite isolation for direct file execution.
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class CompletionBackendTests(unittest.TestCase):
    def fixtures(self, backend, state_strategy, completion_strategy):
        state = dict(job_id="20261027-010000-1-1", vendor="codex",
                     model="fixture-model", effort="high", harness="codex")
        value = dict(schema_version=1, job_id=state["job_id"], agent_id="/root/worker",
                     runtime=dict(vendor="codex", model="fixture-model", effort="high",
                                  harness="codex", backend=backend),
                     outcome="success", result="verified", evidence=["local test"])
        if state_strategy is not None:
            state["agent_strategy"] = state_strategy
        if completion_strategy is not None:
            value["agent_strategy"] = completion_strategy
        return value, state

    def test_new_defaults_and_explicit_strategy_preserve_other_backends(self):
        for state_strategy in (None, "new"):
            for completion_strategy in (None, "new"):
                for backend in ("collaboration.spawn_agent", "host.custom_backend", " custom backend "):
                    with self.subTest(state=state_strategy, completion=completion_strategy, backend=backend):
                        value, state = self.fixtures(backend, state_strategy, completion_strategy)
                        before = copy.deepcopy((value, state))
                        self.assertIs(native.validate_completion(value, state), value)
                        self.assertEqual(value["runtime"]["backend"], backend)
                        self.assertEqual((value, state), before)

    def test_new_defaults_and_explicit_strategy_reject_reuse_backend(self):
        for state_strategy in (None, "new"):
            for completion_strategy in (None, "new"):
                with self.subTest(state=state_strategy, completion=completion_strategy):
                    value, state = self.fixtures("collaboration.followup_task", state_strategy, completion_strategy)
                    before = copy.deepcopy((value, state))
                    with self.assertRaisesRegex(ValueError, "^reuse backend on new-agent job$"):
                        native.validate_completion(value, state)
                    self.assertEqual((value, state), before)


if __name__ == "__main__":
    unittest.main()
