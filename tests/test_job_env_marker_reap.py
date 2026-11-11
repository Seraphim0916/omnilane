"""Cancel and whole-job timeout must also stop job processes that left the job's process group.

The fixture worker starts a writer in its own group and a helper that calls setsid, the
shape CLIs use for detached helpers. Group signalling alone never reaches the helper; the
supervisor finds it by the OMNILANE_JOB_ID marker every job process inherits.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
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
helper_args = ([os.environ['FIXTURE_HELPER_SCRIPT'], str(root / 'helper.log'), str(root)]
               if os.environ.get('FIXTURE_HELPER_SCRIPT') else ['-c', loop, str(root / 'helper.log')])
helper = subprocess.Popen([sys.executable, *helper_args], start_new_session=True)
time.sleep(0.2)
ids = {'worker': [os.getpid(), os.getpgrp()], 'writer': [writer.pid, os.getpgid(writer.pid)],
       'helper': [helper.pid, os.getpgid(helper.pid)]}
if os.environ.get('FIXTURE_NESTED'):
    # A background job dispatched from inside this job: it must outlive this job's cancel.
    child_env = dict(os.environ, FIXTURE_TAG=os.environ['FIXTURE_TAG'] + '-child')
    child_env.pop('FIXTURE_NESTED')
    # Workers refuse nested dispatch (OMNILANE_DEPTH=1); a process that resets the depth still
    # inherits this job's environment, which is the case the marker must not follow.
    child_env['OMNILANE_DEPTH'] = '0'
    for key in [k for k in child_env if k.startswith('OMNILANE_AA_')]:
        child_env.pop(key)
    child = subprocess.run([os.environ['FIXTURE_BASH'], os.environ['FIXTURE_DISPATCH'], '--background', '--single-shot', '--operator-asserted-human',
                            'fixture', 'nested background child'], env=child_env, text=True,
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=20)
    (root / 'child-job').write_text(child.stdout.strip() + chr(10) + 'rc=' + str(child.returncode) + chr(10) + child.stderr[-800:])
(root / 'ids.tmp').write_text(json.dumps(ids))
(root / 'ids.tmp').replace(root / 'ids.json')
signal.pause()
"""


RESPAWN_HELPER = """import json, os, signal, subprocess, sys, time
from pathlib import Path
log, root = sys.argv[1], Path(sys.argv[2])
loop = ("import sys, time\\n"
        "while True:\\n"
        "    open(sys.argv[1], 'a').write('x\\\\n')\\n"
        "    time.sleep(0.1)\\n")
def on_term(*_):
    # Hand the work to a new marked process, then leave: the PID that got TERM is gone.
    child = subprocess.Popen([sys.executable, '-c', loop, str(root / 'respawn.log')], start_new_session=True)
    (root / 'respawn.tmp').write_text(json.dumps([child.pid, os.getpgid(child.pid)]))
    (root / 'respawn.tmp').replace(root / 'respawn.json')
    os._exit(0)
signal.signal(signal.SIGTERM, on_term)
while True:
    open(log, 'a').write('x\\n')
    time.sleep(0.1)
"""


def alive(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], text=True, capture_output=True, timeout=5)
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"),
                     "job_marker.py reads process environments only on macOS and Linux")
class JobEnvMarkerReapTests(unittest.TestCase):
    def setUp(self):
        try:
            check = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'pid='], capture_output=True, text=True)
        except OSError:
            self.skipTest('process inspection denied by this execution environment')
        if check.returncode or check.stdout.strip() != str(os.getpid()):
            self.skipTest('supervised runtime requires process inspection; denied in this sandbox')
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
        self.decoys = []
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
        for proc in self.decoys:
            proc.wait(timeout=5)

    def dispatch(self, tag, *extra, env_extra=None):
        env = dict(self.env, FIXTURE_TAG=tag, **(env_extra or {}))
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

    def decoy(self, name, argv_text, env_extra):
        log = self.root / f"{name}.log"
        loop = "import sys, time\nwhile True:\n    open(sys.argv[1], 'a').write('x\\n')\n    time.sleep(0.1)\n"
        env = dict(self.env, **env_extra)
        proc = subprocess.Popen(["python3", "-c", loop, str(log), *argv_text.split()], env=env,
                                start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.recorded.append((proc.pid, proc.pid))
        self.decoys.append(proc)
        return proc.pid

    def test_marker_text_outside_the_environment_does_not_match(self):
        job_id, ids = self.dispatch("target")
        decoys = {
            # The marker only as a command argument.
            "argv": self.decoy("argv", f"--note OMNILANE_JOB_ID={job_id}", {}),
            # A different real marker, with the target marker as argument text on the same line.
            "two-markers": self.decoy("two", f"NOTE=hello OMNILANE_JOB_ID={job_id} world",
                                      {"OMNILANE_JOB_ID": "other-job"}),
            # A marker that only starts with the target ID (job-12 versus job-123).
            "prefix": self.decoy("prefix", "x", {"OMNILANE_JOB_ID": job_id + "3"}),
        }
        time.sleep(0.3)
        self.assertEqual(self.jobs("cancel", job_id).returncode, 0)
        self.wait_exit(job_id)
        self.assert_reaped(job_id, ids, 143)
        killed = [name for name, pid in decoys.items() if not alive(pid)]
        self.assertEqual(killed, [], "processes outside the job were stopped: " + ", ".join(killed))

    def test_background_child_job_survives_parent_cancel(self):
        # macOS hides the environment of platform binaries such as /bin/bash, so prefer a
        # non-system bash there: only then would a leaked parent marker be visible at all.
        bash = shutil.which("bash", path="/opt/homebrew/bin:/usr/local/bin") or shutil.which("bash")
        parent_id, parent = self.dispatch("parent", env_extra={
            "FIXTURE_NESTED": "1", "FIXTURE_DISPATCH": str(ROOT / "scripts/dispatch.sh"),
            "FIXTURE_BASH": bash})
        child_file = self.fixtures / "parent" / "child-job"
        deadline = time.monotonic() + 20
        while not child_file.exists():
            self.assertLess(time.monotonic(), deadline, "nested dispatch did not report")
            time.sleep(0.05)
        child_id = child_file.read_text().split("\n", 1)[0]
        self.assertRegex(child_id, r"^\d{8}-\d{6}-\d+-\d+$", child_file.read_text())
        child_ids_path = self.fixtures / "parent-child" / "ids.json"
        while not child_ids_path.exists():
            self.assertLess(time.monotonic(), deadline, "nested child worker did not start")
            time.sleep(0.05)
        child = json.loads(child_ids_path.read_text())
        self.recorded.extend(tuple(v) for v in child.values())
        controller = int((self.home / "jobs" / child_id / "pid").read_text())
        self.recorded.append((controller, controller))
        self.assertEqual(self.jobs("cancel", parent_id).returncode, 0)
        self.wait_exit(parent_id)
        self.assert_reaped(parent_id, parent, 143)
        stopped = [name for name in ("worker", "writer", "helper") if not alive(child[name][0])]
        if not alive(controller):
            stopped.append("controller")
        self.assertEqual(stopped, [], "background child job lost: " + ", ".join(stopped))
        self.assertEqual(self.status(child_id)["state"], "running")
        self.assertEqual(self.jobs("cancel", child_id).returncode, 0)
        self.wait_exit(child_id)
        self.assert_reaped(child_id, child, 143)

    def test_replacement_started_after_term_is_reported_as_remaining(self):
        script = self.root / "respawn-helper.py"
        script.write_text(RESPAWN_HELPER, encoding="utf-8")
        job_id, ids = self.dispatch("respawn", env_extra={"FIXTURE_HELPER_SCRIPT": str(script)})
        self.assertEqual(self.jobs("cancel", job_id).returncode, 0)
        self.wait_exit(job_id)
        replacement_path = self.fixtures / "respawn" / "respawn.json"
        deadline = time.monotonic() + 10
        while not replacement_path.exists():
            self.assertLess(time.monotonic(), deadline, "helper did not start its replacement")
            time.sleep(0.05)
        replacement = json.loads(replacement_path.read_text())
        self.recorded.append(tuple(replacement))
        self.assertEqual(self.wait_gone([ids["helper"][0]]), [], "the helper that got TERM is still running")
        self.assertTrue(alive(replacement[0]), "the replacement should still be running for this check")
        job = self.status(job_id)
        self.assertEqual(job["exit_code"], 143)
        # The replacement was found by the last listing, so it must count as remaining.
        self.assertEqual(job.get("leftovers"), {"reaped": 1, "remaining": 1}, job)
        plain = self.jobs("status", job_id)
        self.assertIn("leftovers=1", plain.stdout)

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
