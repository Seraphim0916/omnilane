"""Cancel and whole-job timeout must also stop job processes that left the job's process group.

The fixture worker starts a writer in its own group and a helper that calls setsid, the
shape CLIs use for detached helpers. Group signalling alone never reaches the helper; the
supervisor finds it by the OMNILANE_JOB_ID marker every job process inherits.
"""
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

WORKER = """import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT']) / os.environ['FIXTURE_TAG']
root.mkdir(parents=True, exist_ok=True)
sys.stdin.read()
loop = ("import sys, time\\n"
        "while True:\\n"
        "    open(sys.argv[1], 'a').write('x\\\\n')\\n"
        "    time.sleep(0.1)\\n")
writer = subprocess.Popen([sys.executable, '-c', loop, str(root / 'writer.log')])
helper = subprocess.Popen([sys.executable, '-c', loop, str(root / 'helper.log')], start_new_session=True)
time.sleep(0.2)
ids = {'worker': [os.getpid(), os.getpgrp()], 'writer': [writer.pid, os.getpgid(writer.pid)],
       'helper': [helper.pid, os.getpgid(helper.pid)]}
(root / 'ids.tmp').write_text(json.dumps(ids))
(root / 'ids.tmp').replace(root / 'ids.json')
signal.pause()
"""


def alive(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], text=True, capture_output=True, timeout=5)
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


class JobEnvMarkerReapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omnilane-marker-reap-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env, self.violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        self.fixtures = self.root / "fixtures"
        bins = self.root / "bin"
        bins.mkdir()
        worker = bins / "marker-worker"
        worker.write_text("#!/usr/bin/env python3\n" + WORKER, encoding="utf-8")
        worker.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{worker}" -\n')
        self.env.update(OMNILANE_HOME=str(self.home), FIXTURE_ROOT=str(self.fixtures), OMNILANE_INBOX="0")
        self.recorded = []
        self.addCleanup(self.stop_recorded)

    def stop_recorded(self):
        # Only processes this fixture recorded are signalled, and only while their group still matches.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid, pgid in self.recorded:
                try:
                    if pgid != os.getpgrp() and os.getpgid(pid) == pgid:
                        os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            time.sleep(0.2)

    def dispatch(self, tag, *extra):
        env = dict(self.env, FIXTURE_TAG=tag)
        done = subprocess.run(["bash", str(ROOT / "scripts/dispatch.sh"), "--background", "--single-shot", *extra,
                               "fixture", "offline marker fixture " + tag],
                              env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        job_id = done.stdout.strip()
        self.assertRegex(job_id, r"^\d{8}-\d{6}-\d+-\d+$")
        ids_path = self.fixtures / tag / "ids.json"
        deadline = time.monotonic() + 15
        while not ids_path.exists():
            self.assertLess(time.monotonic(), deadline, "fixture worker did not start")
            time.sleep(0.05)
        ids = json.loads(ids_path.read_text())
        self.recorded.extend(tuple(v) for v in ids.values())
        self.assertNotEqual(ids["helper"][1], ids["worker"][1], "helper must lead a group of its own")
        return job_id, ids

    def jobs(self, *args):
        return subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), *args],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def wait_gone(self, pids, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not any(alive(pid) for pid in pids):
                return []
            time.sleep(0.1)
        return [pid for pid in pids if alive(pid)]

    def wait_exit(self, job_id, timeout=20):
        exit_path = self.home / "jobs" / job_id / "exit"
        deadline = time.monotonic() + timeout
        while not exit_path.exists():
            self.assertLess(time.monotonic(), deadline, "job did not record an exit")
            time.sleep(0.1)
        # The supervisor sweeps before the worker publishes its exit; give it a moment either way.
        time.sleep(0.5)

    def status(self, job_id):
        result = self.jobs("--json", "status", job_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["job"]

    def assert_reaped(self, job_id, ids, exit_code):
        survivors = self.wait_gone([ids[name][0] for name in ("worker", "writer", "helper")])
        self.assertEqual(survivors, [], f"processes of job {job_id} survived: {survivors}")
        job = self.status(job_id)
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["exit_code"], exit_code)
        self.assertEqual(job.get("leftovers"), {"reaped": 1, "remaining": 0}, job)

    def test_cancel_stops_setsid_helper_and_spares_control(self):
        job_id, ids = self.dispatch("cancelled")
        control_id, control = self.dispatch("control")
        cancelled = self.jobs("cancel", job_id)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr + cancelled.stdout)
        self.wait_exit(job_id)
        self.assert_reaped(job_id, ids, 143)
        for name in ("worker", "writer", "helper"):
            self.assertTrue(alive(control[name][0]), f"control job {name} was stopped by another job's cancel")
        self.assertEqual(self.status(control_id)["state"], "running")
        self.assertEqual(self.jobs("cancel", control_id).returncode, 0)
        self.wait_exit(control_id)
        self.assert_reaped(control_id, control, 143)

    def test_job_timeout_stops_setsid_helper_and_spares_control(self):
        control_id, control = self.dispatch("control")
        job_id, ids = self.dispatch("timeout", "--job-timeout", "3")
        self.wait_exit(job_id)
        self.assert_reaped(job_id, ids, 124)
        for name in ("worker", "writer", "helper"):
            self.assertTrue(alive(control[name][0]), f"control job {name} was stopped by another job's timeout")
        self.assertEqual(self.jobs("cancel", control_id).returncode, 0)
        self.wait_exit(control_id)
        self.assert_reaped(control_id, control, 143)

    def test_helper_writes_stop_after_cancel(self):
        job_id, ids = self.dispatch("writes")
        helper_log = self.fixtures / "writes" / "helper.log"
        self.assertEqual(self.jobs("cancel", job_id).returncode, 0)
        self.wait_exit(job_id)
        self.wait_gone([ids["helper"][0]])
        before = helper_log.stat().st_size
        time.sleep(0.5)
        self.assertEqual(helper_log.stat().st_size, before, "helper kept writing after cancel")


if __name__ == "__main__":
    unittest.main()
