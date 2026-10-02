"""Completed goal history remains useful after normal job artifact cleanup."""
import json
from pathlib import Path
import subprocess
import time
import unittest

import test_goal_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]


class CompletedGoalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery.GoalRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def check_history(self, exit_code, cleanup, *, consume_before_status=False,
                      prune_consumed=False):
        case = self.fixture
        release = case.root / "release"
        case.env["FAKE_RELEASE"] = str(release)
        gate = case.root / "gate.sh"
        gate.write_text('#!/bin/sh\nwhile [ ! -f "$FAKE_RELEASE" ]; do sleep .02; done\n'
                        f'printf "completed fixture\\n" > "$5"\nexit {exit_code}\n')
        gate.chmod(0o755)
        (case.home / "routing.local.yaml").write_text(f'probe: exec "{gate}" -\n')
        try:
            dispatched = case.run_goal("dispatch", case.goal_id, "--mode", "work", "probe", "completed history")
            self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
            job_id = dispatched.stdout.strip()
            record_path = next((case.goal / "jobs").glob("job-*.json"))
            before = json.loads(record_path.read_text())
            self.assertEqual((before["state"], before["exit"]), ("running", None))
        finally:
            release.touch()
        inbox_path = case.home / "inbox" / (job_id + ".json")
        deadline = time.monotonic() + 10
        while not inbox_path.exists():
            self.assertLess(time.monotonic(), deadline, "fixture did not complete")
            time.sleep(.02)
        if consume_before_status:
            # The normal prompt hook moves the completion before the goal has
            # observed it. The real exit file still supplies outcome and time.
            def consume():
                consumed = subprocess.run(["bash", str(ROOT / "hooks/report-completions.sh")],
                                         env=dict(case.env, CLAUDE_PROJECT_DIR=str(case.root)),
                                         input="{}", text=True, capture_output=True, timeout=15)
                self.assertEqual(consumed.returncode, 0, consumed.stderr)
                self.assertIn(f"exit={exit_code}", consumed.stdout)

            consume()
            self.assertFalse(inbox_path.exists())
            consumed_path = case.home / "inbox" / "consumed" / inbox_path.name
            self.assertTrue(consumed_path.is_file())
            if prune_consumed:
                completion = json.loads(consumed_path.read_text())
                for ordinal in range(200):
                    newer_id = f"29991001-000000-1234-{ordinal:04d}"
                    # 199 ordinary newer consumed fixtures plus one incoming
                    # record exercise the hook's actual 200-record retention.
                    directory = consumed_path.parent if ordinal < 199 else inbox_path.parent
                    (directory / (newer_id + ".json")).write_text(
                        json.dumps(dict(completion, job_id=newer_id)))
                consume()
                self.assertFalse(consumed_path.exists())
                self.assertEqual(len(list(consumed_path.parent.glob("*.json"))), 200)
        self.assertEqual(case.run_goal("status", case.goal_id).returncode, 0)
        original = json.loads(record_path.read_text())
        failures = json.loads((case.goal / "failures.json").read_text())
        if cleanup == "rm":
            args = ["rm", job_id]
        else:
            args = ["prune", "--keep", "0", "--apply"]
        removed = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), *args],
                                 env=case.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse((case.home / "jobs" / job_id).exists())
        time.sleep(1.05)
        for _ in range(2):
            status = case.run_goal("status", case.goal_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            saved = json.loads(record_path.read_text())
            for field in ("state", "exit", "seconds", "finished", "failure_counted"):
                self.assertEqual(saved[field], original[field], field)
            self.assertIs(saved["artifacts_missing"], True)
            self.assertIn(f"exit={exit_code}", status.stdout)
            self.assertIn("artifacts=missing", status.stdout)
            budget = json.loads((case.goal / "budget.json").read_text())
            self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (1, 0))
            self.assertEqual(json.loads((case.goal / "failures.json").read_text()), failures)
        closed = case.run_goal("close", case.goal_id)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        report = (case.goal / "report.md").read_text()
        self.assertIn(f"exit={exit_code}", report)
        self.assertIn(f"seconds={original['seconds']}", report)
        self.assertIn("artifacts=missing", report)

    def test_incomplete_or_invalid_terminal_fields_are_not_assumed_complete(self):
        case = self.fixture
        path = case.goal / "jobs" / "job-00000001.json"
        base = {"job_id": "20261001-000000-1234-1", "ordinal": 1,
                "lane": "probe", "fingerprint": "a" * 64, "submitted_epoch": 1,
                "state": "done", "exit": 0, "seconds": 1,
                "finished": "2026-10-01T00:00:00Z", "failure_counted": False}
        for field, value in (("exit", None), ("exit", True), ("exit", 256),
                             ("seconds", -1), ("seconds", True),
                             ("seconds", 1 << 63), ("finished", "not a timestamp"),
                             ("finished", "2026-02-30T00:00:00Z")):
            with self.subTest(field=field, value=value):
                path.write_text(json.dumps(dict(base, **{field: value})))
                original = path.read_bytes()
                result = case.run_goal("status", case.goal_id)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), original)
        # A job with no recorded completion remains missing, without an exit.
        path.write_text(json.dumps(dict(base, state="running", exit=None)))
        result = case.run_goal("status", case.goal_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["state"], "missing")
        self.assertIsNone(saved["exit"])
        self.assertNotIn("artifacts_missing", saved)

    def test_success_history_survives_jobs_rm(self):
        self.check_history(0, "rm")

    def test_failure_history_survives_jobs_prune(self):
        self.check_history(7, "prune")

    def test_success_history_after_hook_consumes_completion(self):
        self.check_history(0, "rm", consume_before_status=True)

    def test_failure_history_after_hook_consumes_completion(self):
        self.check_history(7, "prune", consume_before_status=True)

    def test_history_after_consumed_completion_retention(self):
        self.check_history(7, "rm", consume_before_status=True, prune_consumed=True)


if __name__ == "__main__":
    unittest.main()
