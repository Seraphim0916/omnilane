"""native.lock must never make a reader wait, and must still serialize writers."""
import datetime
import fcntl
import json
import os
from pathlib import Path
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

    def start(self, argv, stdout=subprocess.DEVNULL):
        process = subprocess.Popen(argv, env=self.env, stdin=subprocess.DEVNULL, stdout=stdout,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
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

    def wait_until(self, condition, message):
        deadline = time.monotonic() + BOUND
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

        self.wait_until(lambda: pending_bytes() > 0, "result never reached its pipe")
        seen = -1
        while seen != pending_bytes():
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
        self.assertEqual(os.read(read_end, 5), b"held\n")
        return process

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
        job_id, directory = self.job()
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
        process = self.start(self.native_argv("cancel", job_id), stdout=write_end)
        os.close(write_end)
        path = directory / "native.json"
        self.wait_until(lambda: json.loads(path.read_text())["state"] == "cancelled", "cancel never recorded")
        time.sleep(0.2)
        self.assertIsNone(process.poll(), "cancel finished, so its output never blocked")
        with open(directory / "native.lock", "r+") as lock:
            deadline = time.monotonic() + BOUND
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.assertLess(time.monotonic(), deadline, "cancel kept the lock while printing")
                    time.sleep(0.02)

    def test_readers_pass_a_stopped_holder_and_a_writer_waits_for_it(self):
        job_id, directory = self.job()
        holder = self.hold_lock(directory)
        self.assertEqual(self.run_native("read-state", job_id).stdout, "pending\tnull\t\n")
        self.assertEqual(self.run_native("list-state", job_id).stdout, "pending\n")
        listed = self.cli("--json", "list")
        self.assertEqual([row["state"] for row in json.loads(listed.stdout)["jobs"]], ["pending"])
        writer = self.start(self.native_argv("cancel", job_id))
        with self.assertRaises(subprocess.TimeoutExpired):
            writer.wait(timeout=1)
        self.assertEqual(json.loads((directory / "native.json").read_text())["state"], "pending")
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait(timeout=BOUND)
        self.assertEqual(writer.wait(timeout=BOUND), 0)
        self.assertEqual(json.loads((directory / "native.json").read_text())["state"], "cancelled")

    def test_a_killed_holder_leaves_the_job_usable(self):
        job_id, directory = self.job()
        holder = self.hold_lock(directory)
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait(timeout=BOUND)
        recorded = self.run_native("complete-native", job_id, self.completion_file(job_id))
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(self.run_native("read-state", job_id).stdout, "done\t0\t\n")

    def test_competing_writers_leave_one_whole_record(self):
        for _ in range(5):
            job_id, directory = self.job()
            holder = self.hold_lock(directory)
            writers = {}
            for agent in ("agent-a", "agent-b"):
                path = self.completion_file(job_id, agent_id=agent)
                writers[agent] = self.start(self.native_argv("complete-native", job_id, path))
            time.sleep(0.2)
            self.assertEqual([w.poll() for w in writers.values()], [None, None])
            os.killpg(holder.pid, signal.SIGKILL)
            holder.wait(timeout=BOUND)
            codes = {agent: writer.wait(timeout=BOUND) for agent, writer in writers.items()}
            self.assertEqual(sorted(codes.values()), [0, 2])
            winner = next(agent for agent, code in codes.items() if code == 0)
            state = json.loads((directory / "native.json").read_text())
            self.assertEqual((state["state"], state["agent_id"], state["completion"]["agent_id"]),
                             ("completed", winner, winner))
            self.assertEqual([p.name for p in directory.iterdir() if p.name.startswith(".")], [])


if __name__ == "__main__":
    unittest.main()
