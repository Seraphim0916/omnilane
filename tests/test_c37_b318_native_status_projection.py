import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


NATIVE = Path(__file__).resolve().parents[1] / "scripts/lib/native.py"
JOB_ID = "20261027-100000-123-456"


class NativeStatusProjectionTests(unittest.TestCase):
    def test_status_projection_and_result_receipt_in_both_modes(self):
        for inherit in (False, True):
            with self.subTest(inherit=inherit), tempfile.TemporaryDirectory() as directory:
                job = Path(directory) / "jobs" / JOB_ID
                job.mkdir(parents=True)
                (job / "native.lock").write_text("")
                completion = {"result": "COMPLETION_SENTINEL 完成", "evidence": ["fixture"]}
                expected = {
                    "id": JOB_ID, "state": "done", "native_state": "completed",
                    "executor": "native", "executor_reason": "exact-capability-match",
                    "exit_code": 0, "agent_id": "fixture-agent", "vendor": "codex",
                    "model": "fixture-model", "effort": "high", "harness": "codex",
                    "agent_strategy": "reuse", "existing_agent_id": "fixture-existing",
                    "preserve_existing_context": True,
                }
                state = dict(expected, job_id=JOB_ID, state="completed",
                             task="TASK_SENTINEL 任務", aa_policy={"text": "POLICY_SENTINEL 政策"},
                             completion=completion, unknown="UNKNOWN_SENTINEL", inherit=inherit)
                if inherit:
                    state["caller_identity_verified"] = False
                    expected.update(inherit=True, caller_identity_verified=False,
                                    satisfies_lane_target=False)
                (job / "native.json").write_text(json.dumps(state), encoding="utf-8")
                for json_mode in (False, True):
                    for action in ("status", "result"):
                        with self.subTest(json=json_mode, action=action):
                            command = [sys.executable, "-B", str(NATIVE), "job", "--home", directory]
                            if json_mode:
                                command.append("--json")
                            result = subprocess.run(command + [action, JOB_ID],
                                                    capture_output=True, text=True, timeout=15)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stderr, "")
                            for marker in ("TASK_SENTINEL", "POLICY_SENTINEL", "UNKNOWN_SENTINEL"):
                                self.assertNotIn(marker, result.stdout)
                            self.assertEqual("COMPLETION_SENTINEL" in result.stdout, action == "result")
                            if json_mode:
                                summary = dict(expected)
                                if action == "result":
                                    summary["completion"] = completion
                                self.assertEqual(json.loads(result.stdout), {
                                    "schema_version": 1, "command": action, "ok": True, "job": summary})
                            elif action == "status":
                                self.assertEqual(json.loads(result.stdout), expected)
                            else:
                                self.assertEqual(result.stdout, completion["result"] + "\n")
                print("status rc 0 task exposed False policy exposed False completion exposed False")
                print("result rc 0 completion exposed True")


if __name__ == "__main__":
    unittest.main()
