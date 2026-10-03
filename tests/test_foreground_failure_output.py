"""Foreground failure output survives normal dispatch/retry with offline gates."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/omnilane"
JOB_ID = r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+"


class ForegroundFailureOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-foreground-output-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.bins = self.root / "bin"
        self.home.mkdir()
        self.bins.mkdir()
        self.env.update(OMNILANE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1",
                        PATH=str(self.bins) + os.pathsep + self.env["PATH"])
        self.background_jobs = []
        # Clean up detached workers before the temporary home disappears, even
        # when a background assertion fails before completion publication.
        self.addCleanup(self.stop_background_workers)

    def gate(self, exit_code):
        self.output = f"harmless offline foreground output, exit {exit_code}\n"
        gate = self.bins / "fake-exec"
        gate.write_text("#!/usr/bin/env bash\n"
                        f"printf '%s\\n' '{self.output.rstrip()}' > \"$5\"\n"
                        f"exit {exit_code}\n")
        gate.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        return gate

    def cli(self, *args):
        return subprocess.run(["bash", str(CLI), *args], env=self.env,
                              capture_output=True, text=True, timeout=20)

    def dispatch(self, exit_code, *flags):
        gate = self.gate(exit_code)
        jobs = self.home / "jobs"
        before = set(jobs.iterdir()) if jobs.exists() else set()
        result = self.cli("dispatch", "--workdir", str(self.root), *flags,
                          "fixture", "Print a harmless fixed offline diagnostic")
        created = set(jobs.iterdir()) - before
        if "--background" in flags:
            self.background_jobs.extend(created)
        self.assertEqual(len(created), 1, result.stderr)
        job = created.pop()
        metadata = json.loads((job / "meta.json").read_text())
        self.assertEqual(metadata["vendor"], "exec")
        self.assertEqual(metadata["model"], str(gate))
        return result, job

    def assert_durable_result(self, job, exit_code):
        self.assertEqual((job / "out.txt").read_text(), self.output)
        self.assertEqual((job / "exit").read_text(), f"{exit_code}\n")
        record = json.loads((self.home / "inbox" / (job.name + ".json")).read_text())
        self.assertEqual(record["job_id"], job.name)
        self.assertEqual(record["exit"], exit_code)
        self.assertEqual(record["tail"], self.output)

    def check_foreground(self, exit_code, supervised=False):
        flags = ("--job-timeout", "10") if supervised else ()
        result, job = self.dispatch(exit_code, *flags)
        self.assert_durable_result(job, exit_code)
        metadata = json.loads((job / "meta.json").read_text())
        self.assertEqual(metadata["job_timeout"], 10 if supervised else None)
        self.assertEqual(result.returncode, exit_code, result.stderr)
        self.assertEqual(result.stdout, self.output)

    def test_foreground_failure_returns_output_and_exit(self):
        self.check_foreground(7)

    def test_foreground_success_returns_output_and_exit(self):
        self.check_foreground(0)

    def test_supervised_foreground_failure_returns_output_and_exit(self):
        self.check_foreground(7, supervised=True)

    def test_supervised_foreground_success_returns_output_and_exit(self):
        self.check_foreground(0, supervised=True)

    def check_retry(self, exit_code):
        original, job = self.dispatch(exit_code)
        self.assertEqual(original.returncode, exit_code, original.stderr)
        self.assert_durable_result(job, exit_code)
        before = set((self.home / "jobs").iterdir())
        retry = self.cli("jobs", "retry", job.name)
        created = set((self.home / "jobs").iterdir()) - before
        self.assertEqual(len(created), 1, retry.stderr)
        retried_job = created.pop()
        self.assertEqual((retried_job / "task.txt").read_bytes(), (job / "task.txt").read_bytes())
        self.assert_durable_result(retried_job, exit_code)
        self.assertEqual(retry.returncode, exit_code, retry.stderr)
        self.assertEqual(retry.stdout, self.output)

    def test_retry_failure_returns_output_and_exit(self):
        self.check_retry(7)

    def test_retry_success_returns_output_and_exit(self):
        self.check_retry(0)

    def process_running(self, pid):
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], env=self.env,
                                capture_output=True, text=True, timeout=5)
        state = result.stdout.strip()
        # A zombie cannot execute; the host init process owns reaping it.
        return bool(state) and not state.startswith("Z")

    def wait_background(self, job):
        record = self.home / "inbox" / (job.name + ".json")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (job / "pid").exists() and record.exists():
                pid = int((job / "pid").read_text())
                if not self.process_running(pid):
                    return
            time.sleep(0.02)
        self.fail("offline background worker did not finish and publish completion")

    def stop_background_workers(self):
        for job in self.background_jobs:
            pid_file = job / "pid"
            # The public ID can arrive before the detached worker writes pid.
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists(), "owned background worker never published its pid")
            pid = int(pid_file.read_text())
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if not self.process_running(pid):
                    break
                try:
                    # Dispatch's own detached worker leads its process group.
                    # Signal only a group recorded by this fixture's real job.
                    if os.getpgid(pid) == pid and pid != os.getpgrp():
                        os.killpg(pid, sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and self.process_running(pid):
                    time.sleep(0.02)
            self.assertFalse(self.process_running(pid), "owned background worker survived cleanup")

    def test_background_failure_keeps_job_id_only_and_durable_result(self):
        result, job = self.dispatch(7, "--background", "--single-shot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(job.name, "^" + JOB_ID + "$")
        self.assertEqual(result.stdout, job.name + "\n")
        self.wait_background(job)
        self.assert_durable_result(job, 7)

    def test_finalization_failure_still_stops_before_success_record(self):
        # An ordinary utility failure in finish_job must remain fatal. This
        # catches fixes that put run_job in `if`/`||` and disable its errexit.
        find = self.bins / "find"
        find.write_text("#!/usr/bin/env bash\n"
                        "printf '%s\\n' 'offline fixture: finalization utility failed' >&2\n"
                        "exit 23\n")
        find.chmod(0o755)
        result, job = self.dispatch(0)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("offline fixture: finalization utility failed", result.stderr)
        self.assertEqual((job / "out.txt").read_text(), self.output)
        self.assertFalse((job / "exit").exists())
        self.assertFalse((self.home / "inbox" / (job.name + ".json")).exists())


if __name__ == "__main__":
    unittest.main()
