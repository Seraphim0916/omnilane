"""Offline fault injection for recoverable goal-ledger publication."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class GoalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-goal-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env, self.violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = self.root / "home"
        self.home.mkdir()
        self.env["OMNILANE_HOME"] = str(self.home)
        opened = self.run_goal("open", "recover offline ledger", "--workdir", str(self.root))
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.goal_id = opened.stdout.strip()
        self.goal = self.home / "goals" / self.goal_id

    def run_goal(self, *args, env=None):
        return subprocess.run(["bash", str(ROOT / "scripts/lib/goal-loop.sh"), *args],
                              env=env or self.env, text=True, capture_output=True, timeout=15)

    def interrupted_environment(self, filename, *, closed_only=False):
        bins = self.root / "fault-bin"
        bins.mkdir(exist_ok=True)
        hook = self.root / "fault.py"
        hook.write_text('''import json, os, sys
sys.argv = sys.argv[1:]
source = sys.stdin.read()
original = os.replace
def replace(source, destination):
    if (os.path.basename(destination) == os.environ["FAULT_RECORD"]
            and (not os.environ.get("FAULT_CLOSED_ONLY")
                 or json.load(open(source))["status"] == "closed")):
        raise OSError("injected interrupted publication")
    return original(source, destination)
os.replace = replace
exec(compile(source, "<goal fixture>", "exec"), {"__name__": "__main__"})
''')
        wrapper = bins / "python3"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(hook)) + ' "$@"\n')
        wrapper.chmod(0o755)
        return dict(self.env, PATH=str(bins) + os.pathsep + self.env["PATH"], FAULT_RECORD=filename,
                    FAULT_CLOSED_ONLY="1" if closed_only else "")

    def failed_jobs(self):
        fingerprint = hashlib.sha256(b"probe\0fail offline").hexdigest()
        for ordinal in (1, 2):
            job_id = f"20261001-000000-1234-{ordinal}"
            job = self.home / "jobs" / job_id
            job.mkdir(parents=True)
            (job / "exit").write_text("9\n")
            record = {"job_id": job_id, "ordinal": ordinal, "lane": "probe",
                      "fingerprint": fingerprint, "submitted_epoch": 1,
                      "failure_counted": False}
            (self.goal / "jobs" / f"job-{ordinal:08d}.json").write_text(json.dumps(record))
        return fingerprint

    def test_interrupted_failure_publication_recovers_fuse_without_double_counting(self):
        fingerprint = self.failed_jobs()
        failed = self.run_goal("status", self.goal_id,
                               env=self.interrupted_environment("failures.json"))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected interrupted publication", failed.stderr)
        for _ in range(3):
            refreshed = self.run_goal("status", self.goal_id)
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertEqual(json.loads((self.goal / "failures.json").read_text()).get(fingerprint), 2)
        refused = self.run_goal("dispatch", self.goal_id, "probe", "fail offline")
        self.assertEqual(refused.returncode, 75, refused.stderr)
        self.assertIn("failure fuse tripped", refused.stderr)

    def test_existing_counted_failure_remains_history_when_exit_is_later_zero(self):
        fingerprint = self.failed_jobs()
        first = self.run_goal("status", self.goal_id)
        self.assertEqual(first.returncode, 0, first.stderr)
        for path in (self.home / "jobs").glob("*/exit"):
            path.write_text("0\n")
        for _ in range(2):
            refreshed = self.run_goal("status", self.goal_id)
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertEqual(json.loads((self.goal / "failures.json").read_text())[fingerprint], 2)

    def test_failure_count_survives_pruned_job_artifacts(self):
        fingerprint = self.failed_jobs()
        first = self.run_goal("status", self.goal_id)
        self.assertEqual(first.returncode, 0, first.stderr)
        shutil.rmtree(self.home / "jobs")
        for _ in range(2):
            refreshed = self.run_goal("status", self.goal_id)
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertEqual(json.loads((self.goal / "failures.json").read_text())[fingerprint], 2)

    def test_interrupted_job_marker_publication_counts_each_failure_once_on_retry(self):
        fingerprint = self.failed_jobs()
        failed = self.run_goal("status", self.goal_id,
                               env=self.interrupted_environment("job-00000002.json"))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected interrupted publication", failed.stderr)
        temporary = list((self.goal / "jobs").glob("job-00000002.json.tmp.*"))
        self.assertEqual(len(temporary), 1)
        for _ in range(2):
            retried = self.run_goal("status", self.goal_id)
            self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertEqual(json.loads((self.goal / "failures.json").read_text())[fingerprint], 2)
            self.assertEqual(json.loads((self.goal / "budget.json").read_text())["spent_jobs"], 2)
            self.assertTrue(temporary[0].exists())  # Ignored, never consumed as a third job.

    def test_interrupted_final_seal_can_retry_after_report_is_published(self):
        failed = self.run_goal("close", self.goal_id, "--summary", "recover seal",
                               env=self.interrupted_environment("budget.json", closed_only=True))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected interrupted publication", failed.stderr)
        self.assertTrue((self.goal / "report.md").is_file())
        self.assertEqual(json.loads((self.goal / "budget.json").read_text())["status"], "open")
        retried = self.run_goal("close", self.goal_id, "--summary", "recover seal")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn("recover seal", (self.goal / "report.md").read_text())
        self.assertEqual(json.loads((self.goal / "budget.json").read_text())["status"], "closed")

    def test_report_publication_failure_leaves_goal_open_and_retryable(self):
        failed = self.run_goal("close", self.goal_id, "--summary", "verified summary",
                               env=self.interrupted_environment("report.md"))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected interrupted publication", failed.stderr)
        self.assertEqual(json.loads((self.goal / "budget.json").read_text())["status"], "open")
        retried = self.run_goal("close", self.goal_id, "--summary", "verified summary")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn("verified summary", (self.goal / "report.md").read_text())
        self.assertEqual(json.loads((self.goal / "budget.json").read_text())["status"], "closed")

    def test_malformed_note_does_not_seal_goal_before_report_validation(self):
        (self.goal / "notes.jsonl").write_text('{"incomplete":')
        failed = self.run_goal("close", self.goal_id)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(json.loads((self.goal / "budget.json").read_text())["status"], "open")
        (self.goal / "notes.jsonl").write_text("")
        retried = self.run_goal("close", self.goal_id)
        self.assertEqual(retried.returncode, 0, retried.stderr)


if __name__ == "__main__":
    unittest.main()
