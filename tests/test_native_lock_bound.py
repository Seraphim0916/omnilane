"""native.lock must never make a reader wait, and must still serialize writers."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "scripts/lib/native.py"
BOUND = 20
# Larger than any pipe buffer plus Python's own, so the writer cannot finish unread.
LARGE_RESULT = "中" * 40000
HOLD_LOCK = ("import fcntl, sys, time\n"
             "lock = open(sys.argv[1], 'r+')\n"
             "fcntl.flock(lock, fcntl.LOCK_EX)\n"
             "print('held', flush=True)\n"
             "time.sleep(120)\n")
# Runs native.py as is and reports each lock step, so a test can tell a writer that is
# waiting for the lock from one that has not got that far. argv: events go pause native args...
TRACED_WRITER = """
import fcntl, os, runpy, sys, time
events, go, pause, native = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
def note(word):
    os.write(events, word + b"\\n")
note(b"ready")
if go >= 0:
    os.read(go, 1)
real_flock, real_replace = fcntl.flock, os.replace
def flock(stream, operation):
    note(b"flock")
    real_flock(stream, operation)
    note(b"locked")
def replace(source, target):
    time.sleep(pause)
    note(b"replace")
    real_replace(source, target)
fcntl.flock, os.replace = flock, replace
sys.path.insert(0, os.path.dirname(native))
sys.argv = [native] + sys.argv[5:]
runpy.run_path(native, run_name="__main__")
"""


class NativeLockBoundTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-native-lock-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        self.env["OMNILANE_HOME"] = str(self.home)
        self.next_id = 1

    def job(self):
        job_id = f"20261101-000000-1234-{self.next_id}"
        self.next_id += 1
        directory = self.home / "jobs" / job_id
        directory.mkdir(parents=True)
        start = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = dict(job_id=job_id, executor="native", state="pending", agent_id=None,
                     executor_reason="fixture", vendor="codex", model="fixture-model",
                     effort="high", harness="fixture-harness", created=start, timeout=600)
        (directory / "native.json").write_text(json.dumps(state))
        (directory / "meta.json").write_text(json.dumps(
            {"lane": "fixture", "vendor": "codex", "started": start, "timeout": 600}))
        (directory / "native.lock").touch()
        return job_id, directory

    def completion_file(self, job_id, agent_id="fixture-agent", result="fixture result"):
        state = json.loads((self.home / "jobs" / job_id / "native.json").read_text())
        runtime = {k: state[k] for k in ("vendor", "model", "effort", "harness")}
        runtime["backend"] = "fixture-backend"
        path = self.root / f"{job_id}-{agent_id}.json"
        path.write_text(json.dumps(dict(schema_version=1, job_id=job_id, agent_id=agent_id, runtime=runtime,
                                        outcome="success", result=result, evidence=["fixture:observed"])))
        return path

    def native_argv(self, *args):
        return [sys.executable, "-u", str(NATIVE), "job", "--home", str(self.home), *map(str, args)]

    def run_native(self, *args):
        return subprocess.run(self.native_argv(*args), env=self.env, stdin=subprocess.DEVNULL, text=True,
                              capture_output=True, timeout=BOUND)

    def cli(self, *args):
        return subprocess.run(["bash", str(ROOT / "bin/omnilane"), "jobs", *args], env=self.env,
                              stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=BOUND)

    def start(self, argv, stdout=subprocess.DEVNULL, pass_fds=()):
        process = subprocess.Popen(argv, env=self.env, stdin=subprocess.DEVNULL, stdout=stdout,
                                   stderr=subprocess.DEVNULL, start_new_session=True, pass_fds=pass_fds)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=BOUND)

    def pipe(self):
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        return read_end, write_end

    def next_line(self, read_end, deadline):
        """One line from a child, or b"" at end of file; a child that stops fails the test."""
        line = b""
        while not line.endswith(b"\n"):
            ready, _, _ = select.select([read_end], [], [], max(0, deadline - time.monotonic()))
            self.assertTrue(ready, f"child stopped reporting after {line!r}")
            chunk = os.read(read_end, 1)
            if not chunk:
                break
            line += chunk
        return line

    def wait_until(self, condition, message, deadline=None):
        deadline = time.monotonic() + BOUND if deadline is None else deadline
        while not condition():
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(0.02)

    def stuck_result(self, job_id):
        """`jobs result` writing into a pipe nobody reads, already stuck when this returns."""
        read_end, write_end = self.pipe()
        process = self.start(["bash", str(ROOT / "bin/omnilane"), "jobs", "result", job_id], stdout=write_end)
        os.close(write_end)

        def pending_bytes():
            return int.from_bytes(fcntl.ioctl(read_end, termios.FIONREAD, b"\0\0\0\0"), sys.byteorder)

        deadline = time.monotonic() + BOUND
        self.wait_until(lambda: pending_bytes() > 0, "result never reached its pipe", deadline)
        seen = -1
        while seen != pending_bytes():
            self.assertLess(time.monotonic(), deadline, "result kept writing into its pipe")
            seen = pending_bytes()
            time.sleep(0.2)
        self.assertIsNone(process.poll(), "result finished, so the pipe never held it up")
        return process

    def completed_with_large_result(self):
        job_id, directory = self.job()
        result = self.cli("complete-native", job_id, str(self.completion_file(job_id, result=LARGE_RESULT)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return job_id, directory

    def hold_lock(self, directory):
        read_end, write_end = self.pipe()
        process = self.start([sys.executable, "-c", HOLD_LOCK, str(directory / "native.lock")], stdout=write_end)
        os.close(write_end)
        self.assertEqual(self.next_line(read_end, time.monotonic() + BOUND), b"held\n")
        return process

    def traced_writer(self, events, *args, go=-1, pause=0):
        argv = [sys.executable, "-u", "-c", TRACED_WRITER, str(events), str(go), str(pause), str(NATIVE),
                "job", "--home", str(self.home), *map(str, args)]
        return self.start(argv, pass_fds=(events,) if go < 0 else (events, go))

    def test_list_and_readers_return_while_a_result_is_stuck_in_a_pipe(self):
        stuck_id, _ = self.completed_with_large_result()
        other_id, _ = self.job()
        self.stuck_result(stuck_id)
        observed = self.run_native("read-state", stuck_id)
        self.assertEqual((observed.returncode, observed.stdout), (0, "done\t0\t\n"))
        listed = self.cli("--json", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        states = {row["id"]: row["state"] for row in json.loads(listed.stdout)["jobs"]}
        self.assertEqual(states, {stuck_id: "done", other_id: "pending"})
        self.assertEqual(json.loads(self.cli("--json", "status", stuck_id).stdout)["job"]["state"], "done")

    def test_a_stuck_result_does_not_hold_a_writer(self):
        job_id, _ = self.completed_with_large_result()
        self.stuck_result(job_id)
        refused = self.run_native("cancel", job_id)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("already terminal", refused.stderr)

    def test_a_writer_releases_the_lock_before_it_prints(self):
        for action in ("cancel", "complete-native"):
            with self.subTest(action=action):
                job_id, directory = self.job()
                args = [action, job_id] + ([self.completion_file(job_id)] if action == "complete-native" else [])
                read_end, write_end = self.pipe()
                # A chunk at a time first, because a large write is what makes macOS grow a pipe.
                os.set_blocking(write_end, False)
                for size in (65536, 4096, 512, 1):
                    try:
                        while True:
                            os.write(write_end, b"x" * size)
                    except BlockingIOError:
                        pass
                os.set_blocking(write_end, True)
                process = self.start(self.native_argv(*args), stdout=write_end)
                os.close(write_end)
                path = directory / "native.json"
                self.wait_until(lambda: json.loads(path.read_text())["state"] != "pending", "never recorded")
                time.sleep(0.2)
                self.assertIsNone(process.poll(), f"{action} finished, so its output never blocked")
                with open(directory / "native.lock", "r+") as lock:
                    deadline = time.monotonic() + BOUND
                    while True:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            self.assertLess(time.monotonic(), deadline, f"{action} kept the lock while printing")
                            time.sleep(0.02)

    def test_readers_pass_a_stopped_holder_and_a_writer_waits_for_it(self):
        job_id, directory = self.job()
        holder = self.hold_lock(directory)
        self.assertEqual(self.run_native("read-state", job_id).stdout, "pending\tnull\t\n")
        self.assertEqual(self.run_native("list-state", job_id).stdout, "pending\n")
        listed = self.cli("--json", "list")
        self.assertEqual([row["state"] for row in json.loads(listed.stdout)["jobs"]], ["pending"])
        read_end, write_end = self.pipe()
        writer = self.traced_writer(write_end, "cancel", job_id)
        os.close(write_end)
        deadline = time.monotonic() + BOUND
        self.assertEqual(self.next_line(read_end, deadline), b"ready\n")
        self.assertEqual(self.next_line(read_end, deadline), b"flock\n")
        ready, _, _ = select.select([read_end], [], [], 0.3)
        self.assertEqual(ready, [], "writer went on without waiting for the holder")
        self.assertIsNone(writer.poll())
        self.assertEqual(json.loads((directory / "native.json").read_text())["state"], "pending")
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait(timeout=BOUND)
        deadline = time.monotonic() + BOUND
        self.assertEqual([self.next_line(read_end, deadline) for _ in range(3)], [b"locked\n", b"replace\n", b""])
        self.assertEqual(writer.wait(timeout=BOUND), 0)
        self.assertEqual(json.loads((directory / "native.json").read_text())["state"], "cancelled")

    def test_a_killed_holder_leaves_the_job_usable(self):
        # Guards behaviour that predates the bounded readers: it also passes without them.
        job_id, directory = self.job()
        holder = self.hold_lock(directory)
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait(timeout=BOUND)
        recorded = self.run_native("complete-native", job_id, self.completion_file(job_id))
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(self.run_native("read-state", job_id).stdout, "done\t0\t\n")

    def test_competing_writers_leave_one_whole_record(self):
        # Guards the writer lock, which predates the bounded readers: this also passes
        # without them. All writers leave the gate together and each pauses between
        # reading the record and replacing it, so without the lock several would succeed.
        for _ in range(3):
            job_id, directory = self.job()
            events_read, events_write = self.pipe()
            go_read, go_write = self.pipe()
            writers = {}
            for agent in ("agent-a", "agent-b", "agent-c", "agent-d"):
                path = self.completion_file(job_id, agent_id=agent)
                writers[agent] = self.traced_writer(events_write, "complete-native", job_id, path,
                                                    go=go_read, pause=0.3)
            os.close(events_write)
            deadline = time.monotonic() + BOUND
            self.assertEqual([self.next_line(events_read, deadline) for _ in writers], [b"ready\n"] * len(writers))
            os.close(go_write)
            codes = {agent: writer.wait(timeout=BOUND) for agent, writer in writers.items()}
            self.assertEqual(sorted(codes.values()), [0, 2, 2, 2])
            winner = next(agent for agent, code in codes.items() if code == 0)
            state = json.loads((directory / "native.json").read_text())
            self.assertEqual((state["state"], state["agent_id"], state["completion"]["agent_id"]),
                             ("completed", winner, winner))
            self.assertEqual([p.name for p in directory.iterdir() if p.name.startswith(".")], [])


if __name__ == "__main__":
    unittest.main()
