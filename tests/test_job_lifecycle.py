"""Exercise CLI cancellation with real workers and bounded, offline fixtures."""
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


class JobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omnilane-lifecycle-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env, self.violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        self.bins = self.root / "bin"
        self.bins.mkdir()
        self.env.update(OMNILANE_HOME=str(self.home), FIXTURE_ROOT=str(self.root),
                        PATH=str(self.bins) + os.pathsep + self.env["PATH"])
        self.worker_pid = None
        self.addCleanup(self.stop_worker)

    def executable(self, name, source):
        path = self.bins / name
        path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
        path.chmod(0o755)
        return path

    def stop_worker(self):
        (self.root / "publish.release").touch()
        owned = []
        if self.worker_pid:
            owned.append((self.worker_pid, self.worker_pid))
        for name in ("gate.started", "child.started"):
            path = self.root / name
            if path.exists():
                try:
                    identity = json.loads(path.read_text())
                except (ValueError, OSError):
                    continue
                owned.append((identity["pid"], identity["pgid"]))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid, pgid in owned:
                try:
                    # Only groups recorded by this fixture may be signalled.
                    if pgid != os.getpgrp() and os.getpgid(pid) == pgid:
                        os.killpg(pgid, sig)
                except ProcessLookupError:
                    pass
            time.sleep(0.1)
        for pid, _ in owned:
            self.assert_process_stopped({"pid": pid})

    def remember_worker(self, job_dir):
        self.wait_for_file(job_dir / "pid")
        self.worker_pid = int((job_dir / "pid").read_text())
        try:
            self.assertEqual(os.getpgid(self.worker_pid), self.worker_pid)
        except ProcessLookupError:
            self.fail("fixture worker exited before its gate started")

    def wait_for_file(self, path, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.02)
        self.fail("fixture did not reach " + path.name)

    def job(self, action, job_id, *, env=None):
        return subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), action, job_id],
                              env=env or self.env, text=True, capture_output=True, timeout=15)

    def assert_process_stopped(self, identity):
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(identity["pid"])],
                                env=self.env, text=True, capture_output=True, timeout=5)
        # An orphan zombie cannot execute; its host init process owns reaping.
        self.assertTrue(not result.stdout.strip() or result.stdout.lstrip().startswith("Z"),
                        f"fixture process {identity['pid']} survived cancellation: {result.stdout.strip()}")

    def tree_gate(self, ignore_term=False, finish=False):
        child = self.executable("child", """import json, os, signal
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
ready = Path(os.environ['FIXTURE_ROOT'], 'child.started')
ready.with_suffix('.tmp').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
ready.with_suffix('.tmp').replace(ready)
signal.pause()
""".replace("signal.SIG_IGN", "signal.SIG_IGN" if ignore_term else "signal.SIG_DFL"))
        return self.executable("gate", f"""import json, os, signal, subprocess, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT'])
signal.signal(signal.SIGTERM, {'signal.SIG_IGN' if ignore_term else 'signal.SIG_DFL'})
subprocess.Popen([{str(child)!r}])
deadline = time.monotonic() + 5
while not (root / 'child.started').exists():
    if time.monotonic() >= deadline:
        raise SystemExit(98)
    time.sleep(0.02)
ready = root / 'gate.started'
ready.with_suffix('.tmp').write_text(json.dumps({{'pid': os.getpid(), 'pgid': os.getpgrp()}}))
ready.with_suffix('.tmp').replace(ready)
{'raise SystemExit(7)' if finish else 'signal.pause()'}
""")

    def check_background_tree_cancel(self, ignore_term, vendor="exec"):
        gate = self.tree_gate(ignore_term=ignore_term)
        if vendor == "exec":
            route = f'fixture: exec "{gate}" -\n'
        else:
            route = f'fixture: {vendor} fake-model high\n'
            self.env["CLAUDE_BIN" if vendor == "claude" else "AGY_BIN"] = str(gate)
            (Path(self.env["HOME"]) / ".gemini").mkdir(exist_ok=True)
        (self.home / "routing.local.yaml").write_text(route)
        dispatched = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--background",
             "--single-shot" if vendor == "exec" else "--live",
             "fixture", "offline process-group fixture"],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        job_id = dispatched.stdout.strip()
        self.assertRegex(job_id, r"^\d{8}-\d{6}-\d+-\d+$")
        job_dir = self.home / "jobs" / job_id
        self.remember_worker(job_dir)
        self.wait_for_file(self.root / "gate.started")
        gate_identity = json.loads((self.root / "gate.started").read_text())
        child_identity = json.loads((self.root / "child.started").read_text())
        self.assertIsNone(json.loads((job_dir / "meta.json").read_text())["job_timeout"])
        cancelled = self.job("cancel", job_id)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr + cancelled.stdout)
        self.assert_process_stopped(gate_identity)
        self.assert_process_stopped(child_identity)
        self.assertEqual((job_dir / "exit").read_text().strip(), "143")
        record = self.home / "inbox" / (job_id + ".json")
        self.assertEqual(json.loads(record.read_text())["exit"], 143)

    def test_background_cancel_stops_default_process_tree(self):
        self.check_background_tree_cancel(ignore_term=False)

    def test_background_cancel_escalates_term_ignoring_descendants(self):
        self.check_background_tree_cancel(ignore_term=True)

    def test_live_claude_cancel_stops_term_ignoring_descendants(self):
        self.check_background_tree_cancel(ignore_term=True, vendor="claude")

    def test_live_gemini_cancel_stops_term_ignoring_descendants(self):
        self.check_background_tree_cancel(ignore_term=True, vendor="gemini")

    def test_no_deadline_supervisor_preserves_exit_and_cleans_leftover_child(self):
        gate = self.tree_gate(ignore_term=True, finish=True)
        result = subprocess.run(
            ["perl", str(ROOT / "scripts/lib/job-timeout.pl"), "--no-deadline", str(gate)],
            env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assert_process_stopped(json.loads((self.root / "child.started").read_text()))

    def test_background_watchdog_bypasses_nested_timeout_and_cleans_children(self):
        gate = self.tree_gate()
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        for name in ("timeout", "gtimeout"):
            self.executable(name, """import os
from pathlib import Path
Path(os.environ['FIXTURE_ROOT'], 'nested-timeout.invoked').touch()
raise SystemExit(97)
""")
        dispatched = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--background", "--single-shot",
             "--timeout", "1", "fixture", "offline watchdog fixture"],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        job_dir = self.home / "jobs" / dispatched.stdout.strip()
        self.remember_worker(job_dir)
        self.wait_for_file(job_dir / "exit")
        self.assertFalse((self.root / "nested-timeout.invoked").exists())
        self.assertEqual((job_dir / "exit").read_text().strip(), "142")
        self.assertIsNone(json.loads((job_dir / "meta.json").read_text())["job_timeout"])
        for name in ("gate.started", "child.started"):
            self.assert_process_stopped(json.loads((self.root / name).read_text()))

    def test_background_missing_supervisor_support_fails_before_job_creation(self):
        gate = self.executable("gate", "raise SystemExit('fixture must not launch')\n")
        self.executable("perl", "raise SystemExit(127)\n")
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        dispatched = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--background", "fixture", "offline fixture"],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(dispatched.returncode, 2, dispatched.stderr)
        self.assertIn("perl lacks whole-job timeout support", dispatched.stderr)
        self.assertFalse((self.home / "jobs").exists())

    def check_completion_signal_reentry(self, signum, consume):
        gate = self.executable("gate", """import sys
from pathlib import Path
Path(sys.argv[5]).write_text('completed offline fixture\\n')
""")
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        self.env["FIXTURE_REAL_MV"] = str(Path(self.env["OMNILANE_TEST_UTIL_PATH"]) / "mv")
        self.executable("mv", """import os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT'])
result = subprocess.run([os.environ['FIXTURE_REAL_MV'], *sys.argv[1:]])
if result.returncode:
    raise SystemExit(result.returncode)
if Path(sys.argv[-1]).parent == Path(os.environ['OMNILANE_HOME']) / 'inbox':
    with (root / 'publish.count').open('a') as handle:
        handle.write('published\\n')
    if not (root / 'publish.waiting').exists():
        (root / 'publish.waiting').touch()
        deadline = time.monotonic() + 10
        while not (root / 'publish.release').exists():
            if time.monotonic() >= deadline:
                raise SystemExit(98)
            time.sleep(0.02)
""")
        dispatched = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--background", "--single-shot",
             "--workdir", str(self.root), "fixture", "offline completion reentry"],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        job_id = dispatched.stdout.strip()
        job_dir = self.home / "jobs" / job_id
        self.wait_for_file(self.root / "publish.waiting")
        self.remember_worker(job_dir)
        record = self.home / "inbox" / (job_id + ".json")
        original = record.read_bytes()
        self.assertEqual(json.loads(original)["exit"], 0)
        if consume:
            consumed = self.home / "inbox" / "consumed"
            consumed.mkdir()
            record.rename(consumed / record.name)
        os.kill(self.worker_pid, signum)
        (self.root / "publish.release").touch()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(self.worker_pid)],
                                   env=self.env, text=True, capture_output=True, timeout=5)
            if not state.stdout.strip() or state.stdout.lstrip().startswith("Z"):
                break
            time.sleep(0.02)
        self.assert_process_stopped({"pid": self.worker_pid})
        self.assertEqual((job_dir / "exit").read_text().strip(), "0")
        self.assertEqual((self.root / "publish.count").read_text().splitlines(), ["published"])
        if consume:
            self.assertFalse(record.exists(), "consumed completion was republished")
            self.assertEqual((consumed / record.name).read_bytes(), original)
        else:
            self.assertEqual(record.read_bytes(), original)

    def test_term_during_finalization_does_not_republish_consumed_success(self):
        self.check_completion_signal_reentry(signal.SIGTERM, consume=True)

    def test_hup_during_finalization_preserves_pending_success(self):
        self.check_completion_signal_reentry(signal.SIGHUP, consume=False)

    def test_cancel_allows_completion_publication_during_term_grace(self):
        self.check_completion_grace()

    def test_cancel_still_kills_stuck_signal_immune_finalizer_within_bound(self):
        self.check_completion_grace(stuck=True)

    def check_completion_grace(self, stuck=False):
        gate = self.executable("gate", """import json, os, signal
from pathlib import Path
ready = Path(os.environ['FIXTURE_ROOT'], 'gate.started')
ready.with_suffix('.tmp').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
ready.with_suffix('.tmp').replace(ready)
signal.pause()
""")
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        # Pause the actual completion rename after finish_job has written exit.
        # Other mv calls retain their real behavior, including atomic PID writes.
        self.env["FIXTURE_REAL_MV"] = str(Path(self.env["OMNILANE_TEST_UTIL_PATH"]) / "mv")
        self.executable("mv", """import os, sys, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT'])
if Path(sys.argv[-1]).parent == Path(os.environ['OMNILANE_HOME']) / 'inbox':
    (root / 'publisher.pid').write_text(str(os.getpid()))
    (root / 'publish.waiting').touch()
    deadline = time.monotonic() + 10
    while not (root / 'publish.release').exists():
        if time.monotonic() >= deadline:
            sys.exit(98)
        time.sleep(0.02)
os.execv(os.environ['FIXTURE_REAL_MV'], ['mv', *sys.argv[1:]])
""")
        dispatched = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--background", "--single-shot",
             "--job-timeout", "30", "fixture", "offline cancellation fixture"],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        job_id = dispatched.stdout.strip()
        self.assertRegex(job_id, r"^\d{8}-\d{6}-\d+-\d+$")
        job_dir = self.home / "jobs" / job_id
        self.remember_worker(job_dir)
        self.wait_for_file(self.root / "gate.started")

        # Release the publisher during the second grace wait. On the buggy
        # path this is the post-SIGKILL wait, so publication has already died.
        cancel_bins = self.root / "cancel-bin"
        cancel_bins.mkdir()
        sleep = cancel_bins / "sleep"
        sleep.write_text("""#!/usr/bin/env python3
import os, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT'])
count_file = root / 'cancel.waits'
count = int(count_file.read_text()) + 1 if count_file.exists() else 1
count_file.write_text(str(count))
if count >= 2 and not os.environ.get("FIXTURE_STUCK_PUBLISHER"):
    (root / 'publish.release').touch()
else:
    deadline = time.monotonic() + 5
    while not (root / 'publish.waiting').exists():
        if time.monotonic() >= deadline:
            raise SystemExit('publisher did not reach the cancellation barrier')
        time.sleep(0.02)
time.sleep(1)
""")
        sleep.chmod(0o755)
        cancel_env = dict(self.env, PATH=str(cancel_bins) + os.pathsep + self.env["PATH"])
        if stuck:
            cancel_env["FIXTURE_STUCK_PUBLISHER"] = "1"
        began = time.monotonic()
        cancelled = self.job("cancel", job_id, env=cancel_env)
        elapsed = time.monotonic() - began
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr + cancelled.stdout)
        self.assertTrue((self.root / "publish.waiting").exists(), "completion publisher never started")
        self.assertEqual((job_dir / "exit").read_text().strip(), "143")
        record = self.home / "inbox" / (job_id + ".json")
        if stuck:
            self.assertLess(elapsed, 9, "cancel exceeded bounded TERM/KILL grace")
            self.assert_process_stopped({"pid": self.worker_pid})
            self.assert_process_stopped({"pid": int((self.root / "publisher.pid").read_text())})
            self.assertFalse(record.exists())
            return
        self.assertTrue(record.exists(), "cancel killed the completion publisher during TERM grace")
        self.assertEqual(json.loads(record.read_text())["exit"], 143)
        original = record.read_bytes()
        repeated = self.job("cancel", job_id)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already finished (exit 143)", repeated.stdout)
        self.assertEqual(record.read_bytes(), original)
        self.assertFalse(self.violations.exists())


if __name__ == "__main__":
    unittest.main()
