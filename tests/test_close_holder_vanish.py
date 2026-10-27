"""jobs close when the holder PID file vanishes between its existence check and its read."""
import offline_env  # noqa: F401
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "20260905-160000-12345-1"
# Hang guard only; the tests check observable state, not how fast the host schedules them.
GENEROUS = 60

# Stands in for the worker exiting at the worst moment: jobs close has seen the
# holder PID file, then the file is gone when cat opens it.
CAT_SHIM = """#!/bin/bash
for arg in "$@"; do
  if [[ "$arg" == "$CLOSE_TEST_HOLDER" && -f "$arg" ]]; then
    /bin/mv "$arg" "$arg.vanished"
  fi
done
exec /bin/cat "$@"
"""


class CloseHolderVanishTests(unittest.TestCase):
    def setUp(self):
        # offline_env points tempfile at the suite's private temporary root, outside the checkout.
        self.scratch = tempfile.TemporaryDirectory()
        base = Path(self.scratch.name)
        self.job = base / "home" / "jobs" / JOB_ID
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text('{"lane":"hardest-coding","vendor":"codex","session_mode":"live"}')
        self.holder = self.job / "inbox.holder.pid"
        shims = base / "shims"
        shims.mkdir()
        (shims / "cat").write_text(CAT_SHIM)
        (shims / "cat").chmod(0o755)
        self.env = dict(os.environ, OMNILANE_HOME=str(base / "home"),
                        CLOSE_TEST_HOLDER=str(self.holder))
        self.shim_env = dict(self.env, PATH=f"{shims}:{os.environ['PATH']}")
        self.readers, self.stops = [], []

    def tearDown(self):
        self.scratch.cleanup()

    def close(self, env):
        # Its own process group, so cleanup also reaches the receipt poller close starts.
        return subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)

    def drain(self, closer, timeout=GENEROUS):
        """Observe close's own exit and its output pipes separately; every wait is bounded.

        communicate() would wait for whatever still holds stdout, so a child left behind by
        an early close would be waited out instead of caught. A group that outlives its
        leader by a second (a receipt poller would live 11s) is killed and fails the test."""
        chunks, stop = {}, threading.Event()
        readers = [threading.Thread(target=read_pipe, args=(s, stop, chunks), daemon=True)
                   for s in (closer.stdout, closer.stderr)]
        self.readers += readers
        self.stops.append(stop)
        for reader in readers:
            reader.start()
        try:
            closer.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(closer.pid)
            closer.wait(timeout=5)
            self.fail(f"jobs close hung past {timeout}s")
        finally:
            leftover = not group_gone(closer.pid, timeout=1)
            kill_group(closer.pid)
        if leftover:
            self.fail(f"jobs close exited but left processes in group {closer.pid}")
        for reader in readers:
            reader.join(timeout=5)
            if reader.is_alive():
                stop.set()
                self.fail("jobs close output pipe stayed open after its group was gone")
        return chunks[closer.stdout], chunks[closer.stderr]

    def reap(self, closer):
        """Kill close's whole process group unconditionally, reap it, and check nothing survived.

        A process that left the group can still hold close's pipes: the readers are stopped rather
        than waited out, and the streams are closed only once no reader is left using them."""
        kill_group(closer.pid)
        closer.wait(timeout=GENEROUS)
        for stop in self.stops:
            stop.set()
        for reader in self.readers:
            reader.join(timeout=GENEROUS)
        if any(reader.is_alive() for reader in self.readers):
            self.fail("a pipe reader did not stop")
        for stream in (closer.stdout, closer.stderr):
            if not stream.closed:
                stream.close()
        if not group_gone(closer.pid, timeout=GENEROUS):
            kill_group(closer.pid)
            self.fail(f"process group {closer.pid} survived jobs close")

    def receipt_poller_running(self, closer, end):
        """Whether close's direct child polling for the exit receipt is in the process table now."""
        receipt = str(self.job / "exit")
        listing = subprocess.run(["ps", "-A", "-ww", "-o", "ppid=,command="], capture_output=True,
                                 text=True, timeout=max(.1, end - time.monotonic())).stdout
        for line in listing.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[0] == str(closer.pid) and receipt in fields[1]:
                return True
        return False

    def wait_for_receipt_poller(self, closer, timeout=GENEROUS):
        end = time.monotonic() + timeout
        while True:
            if closer.poll() is not None:
                try:
                    out, err = self.drain(closer, timeout=5)
                except AssertionError as error:
                    out, err = "", str(error)
                self.fail(("close returned before waiting for the exit receipt", closer.returncode, out, err))
            if self.receipt_poller_running(closer, end):
                return
            self.assertLess(time.monotonic(), end, "close never reached the receipt wait")
            time.sleep(.02)

    def test_holder_vanishing_during_read_waits_for_the_receipt(self):
        self.holder.write_text("999999\n")
        closer = self.close(self.shim_env)
        try:
            self.wait_for_receipt_poller(closer)
            # The race really happened: the file existed, then vanished at read time.
            self.assertFalse(self.holder.exists())
            self.assertTrue(self.holder.with_name("inbox.holder.pid.vanished").exists())
            receipt = self.job / ".exit.tmp"
            receipt.write_text("0\n")
            os.replace(receipt, self.job / "exit")
            out, err = self.drain(closer)
        finally:
            self.reap(closer)
        self.assertEqual((closer.returncode, out, err), (0, "already closed (exit 0)\n", ""))

    def test_holder_file_that_stays_invalid_fails_at_once(self):
        self.holder.write_text("not-a-pid\n")
        closer = self.close(self.env)
        try:
            # "At once" means close never reaches the 11s receipt wait: watch its children
            # until it exits instead of timing it, so host load cannot fail or pass this.
            # Relies on the receipt poll being jobs close's only long wait; if close gains another, check that too.
            end = time.monotonic() + GENEROUS
            polled = False
            while closer.poll() is None:
                polled = polled or self.receipt_poller_running(closer, end)
                self.assertLess(time.monotonic(), end, "close with an invalid holder file hung")
                time.sleep(.02)
            out, err = self.drain(closer)
        finally:
            self.reap(closer)
        self.assertFalse(polled, "close waited for the exit receipt instead of failing at once")
        self.assertEqual((closer.returncode, out, err), (1, "", "live inbox holder is unavailable\n"))
        self.assertTrue(self.holder.exists())

    def leaky_close(self, marker):
        """A stand-in close that exits at once but leaves a child holding its stdout;
        the child writes `marker` only if it is allowed to finish on its own."""
        return subprocess.Popen(["/bin/bash", "-c", f'(/bin/sleep 30; : > "{marker}") & echo started; exit 0'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)

    def test_a_left_over_child_fails_the_drain_instead_of_being_waited_out(self):
        marker = Path(self.scratch.name) / "leftover-finished"
        closer = self.leaky_close(marker)
        try:
            with self.assertRaisesRegex(AssertionError, "left processes in group"):
                self.drain(closer)
            self.assertTrue(group_gone(closer.pid, timeout=GENEROUS))
        finally:
            self.reap(closer)
        self.assertFalse(marker.exists(), "the left-over child was waited out")

    def test_an_early_exit_with_a_pipe_holder_does_not_hang_the_receipt_wait(self):
        marker = Path(self.scratch.name) / "leftover-finished"
        closer = self.leaky_close(marker)
        try:
            closer.wait(timeout=GENEROUS)
            with self.assertRaisesRegex(AssertionError, "close returned before waiting for the exit receipt"):
                self.wait_for_receipt_poller(closer)
        finally:
            self.reap(closer)
        self.assertFalse(marker.exists(), "the left-over child was waited out")

    def test_a_pipe_holder_that_left_the_group_cannot_hang_the_cleanup(self):
        pidfile = Path(self.scratch.name) / "escaped.pid"
        script = 'import os, sys, time; os.setsid(); open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(600)'
        closer = subprocess.Popen(["/bin/bash", "-c", '"$PY" -c "$SCRIPT" "$PIDFILE" & echo started; exit 0'],
                                  env=dict(os.environ, PY=sys.executable, SCRIPT=script, PIDFILE=str(pidfile)),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  start_new_session=True)
        escaped = None
        try:
            with self.assertRaisesRegex(AssertionError, "output pipe stayed open"):
                self.drain(closer)
            escaped = int(pidfile.read_text())
            reaped = threading.Event()
            threading.Thread(target=lambda: (self.reap(closer), reaped.set()), daemon=True).start()
            self.assertTrue(reaped.wait(GENEROUS), "cleanup hung on a pipe held outside the group")
            # It finished while the holder still runs: the cleanup was bounded, not waited out.
            os.kill(escaped, 0)
        finally:
            if escaped is not None:
                os.kill(escaped, signal.SIGKILL)
            self.reap(closer)


def read_pipe(stream, stop, chunks):
    """Read a pipe until EOF or until stop is set, in short polls on its descriptor.

    stream.read() blocks for as long as anything holds the write end, holding the stream's
    buffer lock, and closing the stream would then wait for that lock as well."""
    fd, data = stream.fileno(), []
    while not stop.is_set():
        if not select.select([fd], [], [], .05)[0]:
            continue
        block = os.read(fd, 65536)
        if not block:
            break
        data.append(block)
    chunks[stream] = b"".join(data).decode(stream.encoding)


def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # gone, or (EPERM on macOS) already dying


def group_gone(pgid, timeout):
    """True once no process is left in the group; SIGKILL needs a scheduling turn to land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # macOS answers EPERM for a group whose members are still dying; poll again
        time.sleep(.02)
    return False


if __name__ == "__main__":
    unittest.main()
