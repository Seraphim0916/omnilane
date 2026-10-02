"""Goal reporting uses the existing offline job-status contract."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import unittest

import test_goal_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]


class GoalJobStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery.GoalRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.env = self.fixture.env
        self.goal = self.fixture.goal
        self.job_id = "20261001-000000-1234-1"
        self.job = self.fixture.home / "jobs" / self.job_id
        self.job.mkdir(parents=True)
        self.record_path = self.goal / "jobs" / "job-00000001.json"
        self.record_path.write_text(json.dumps({"job_id": self.job_id, "ordinal": 1,
            "lane": "probe", "fingerprint": "a" * 64, "submitted_epoch": int(time.time()) - 5,
            "failure_counted": False}))

    def status(self, expected, *, env=None):
        result = self.fixture.run_goal("status", self.fixture.goal_id, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.record_path.read_text())
        self.assertEqual(record["state"], expected)
        self.assertIsNone(record["exit"])
        self.assertIn("exit=" + expected, result.stdout)
        self.assertEqual(json.loads((self.goal / "budget.json").read_text())["spent_jobs"], 1)
        self.assertGreaterEqual(record["seconds"], 5)
        self.assertEqual(json.loads((self.goal / "failures.json").read_text()), {})
        return result

    def test_reaped_worker_is_dead_in_goal_status_and_report(self):
        child = subprocess.Popen(["true"], env=self.env)
        child.wait(timeout=3)
        (self.job / "pid").write_text(str(child.pid) + "\n")
        canonical = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), "--json", "status", self.job_id],
                                   env=self.env, text=True, capture_output=True, timeout=3)
        self.assertEqual(json.loads(canonical.stdout)["job"]["state"], "dead")
        self.status("dead")
        closed = self.fixture.run_goal("close", self.fixture.goal_id)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("exit=dead", (self.goal / "report.md").read_text())

    def test_live_worker_stays_running_without_counting_failure(self):
        (self.job / "pid").write_text(str(os.getpid()) + "\n")
        self.status("running")

    def test_missing_pid_retains_canonical_startup_running_state(self):
        self.status("running")

    def test_invalid_pid_matches_canonical_dead_state_without_exit(self):
        (self.job / "pid").write_text("not-a-pid\n")
        self.status("dead")

    def test_missing_job_is_reported_missing_without_inventing_exit(self):
        self.job.rmdir()
        self.status("missing")
        closed = self.fixture.run_goal("close", self.fixture.goal_id)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("exit=missing", (self.goal / "report.md").read_text())

    def faulty_status(self, fault):
        bins = self.root / "status-bin"
        bins.mkdir(exist_ok=True)
        hook = self.root / "status-hook.py"
        hook.write_text('''import json, os, subprocess, sys
sys.argv=sys.argv[1:]
source=sys.stdin.read()
if "def incomplete_job_state" in source:
    def fake_run(args, **kwargs):
        fault=os.environ["STATUS_FAULT"]
        if fault == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if fault == "invalid-json":
            return subprocess.CompletedProcess(args,0,"{broken","")
        state="running"; job_id=args[-1]; exit_code=None
        if fault == "wrong-id": job_id="20261001-000000-1234-999"
        if fault == "unknown-state": state="invented"
        if fault == "exit-on-dead": state="dead"; exit_code=143
        return subprocess.CompletedProcess(args, 2 if fault == "failed" else 0,
            json.dumps({"schema_version":1,"command":"status","ok":True,
                        "job":{"id":job_id,"state":state,"exit_code":exit_code}}),"")
    subprocess.run=fake_run
exec(compile(source,"<status fixture>","exec"),{"__name__":"__main__"})
''')
        wrapper = bins / "python3"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(hook)) + ' "$@"\n')
        wrapper.chmod(0o755)
        return dict(self.env, PATH=str(bins) + os.pathsep + self.env["PATH"], STATUS_FAULT=fault)

    def test_failed_or_inconsistent_status_is_unknown_not_running(self):
        for fault in ("timeout", "invalid-json", "wrong-id", "unknown-state", "exit-on-dead", "failed"):
            with self.subTest(fault=fault):
                self.status("unknown", env=self.faulty_status(fault))

    def test_completed_job_keeps_real_exit_and_failure_accounting(self):
        (self.job / "exit").write_text("9\n")
        result = self.fixture.run_goal("status", self.fixture.goal_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.record_path.read_text())
        self.assertEqual((record["state"], record["exit"]), ("done", 9))
        self.assertEqual(json.loads((self.goal / "failures.json").read_text()), {"a" * 64: 1})


if __name__ == "__main__":
    unittest.main()
