import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


class PreviewQuotaTests(unittest.TestCase):
    def test_preview_preserves_quota_without_reading_or_allocating(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("native_b291", root / "scripts/lib/native.py")
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        quota = dict(allowed=True, reason="合成額度說明", remaining=0,
                     details={"limits": [1, None, False], "extra": "preserved"})
        for decision in (quota, None):
            with self.subTest(has_quota=decision is not None):
                args = SimpleNamespace(dry_run=True, task="-")
                if decision is not None:
                    args.quota_decision = json.dumps(decision, ensure_ascii=False)
                plan = dict(task="-", state="planned", job_id=None, task_id=None,
                            agent_id=None, provider_invoked=False, job_state_created=False)
                expected = copy.deepcopy(plan)
                if decision is not None:
                    expected["quota_preflight"] = copy.deepcopy(decision)
                output = io.StringIO()
                with contextlib.ExitStack() as stack:
                    guards = []
                    for name in ("datetime", "secrets", "stamp", "jobs_root", "publish_job"):
                        guard = stack.enter_context(mock.patch.object(native, name))
                        guard.side_effect = AssertionError("preview called " + name)
                        if name == "datetime":
                            guard.datetime.now.side_effect = AssertionError("allocated timestamp")
                        if name == "secrets":
                            guard.randbelow.side_effect = AssertionError("allocated job ID")
                        guards.append(guard)
                    stdin = stack.enter_context(mock.patch.object(native.sys, "stdin"))
                    stdin.read.side_effect = AssertionError("preview read stdin")
                    stack.enter_context(contextlib.redirect_stdout(output))
                    self.assertEqual(native.publish_plan(args, plan, {}, {}, None), 0)
                    self.assertEqual(json.loads(output.getvalue()), expected)
                    self.assertEqual(plan, expected)
                    self.assertEqual(stdin.mock_calls, [])
                    for guard in guards:
                        self.assertEqual(guard.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
