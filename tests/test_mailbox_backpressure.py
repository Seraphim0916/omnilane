"""Exercise the real send CLI against local FIFOs, without provider processes."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "20261026-120000-123-456"
HARD_TIMEOUT = 12


class MailboxBackpressureTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".wt-tmp"
        scratch.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="mailbox-", dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        job = self.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        (job / "meta.json").write_text(
            '{"lane":"test","vendor":"codex","session_mode":"live"}\n'
        )
        (job / "inbox.ready").touch()
        self.fifo = job / "inbox.fifo"
        os.mkfifo(self.fifo)
        self.reader = None
        self.process = None
        self.addCleanup(self.cleanup_processes)
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.home),
            "OMNILANE_HOME": str(self.home),
            "TMPDIR": str(self.home),
            "LC_ALL": "C",
            "FOREMAN_SESSION": "mailbox-test",
        }

    def group_exists(self):
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def cleanup_processes(self):
        if self.process is not None:
            # Every sender gets a private group. Never use name-based pkill.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=2)
        if self.reader is not None:
            os.close(self.reader)
            self.reader = None

    def open_reader(self):
        # This descriptor models a reader that stays alive but does not consume.
        # Popen closes it in the sender, so closing here really removes the reader.
        self.reader = os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK)

    def fill_fifo(self):
        writer = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        count = 0
        try:
            # Discover capacity instead of assuming Linux or macOS buffer sizes.
            while count < 16 * 1024 * 1024:
                try:
                    count += os.write(writer, b"x" * 1024)
                except BlockingIOError:
                    # Fill any remaining space so even a short message blocks.
                    while True:
                        try:
                            count += os.write(writer, b"x")
                        except BlockingIOError:
                            return count
            self.fail("FIFO did not reach backpressure within fixture limit")
        finally:
            os.close(writer)

    def start_send(self):
        self.output_path = self.home / "stdout"
        self.trace_path = self.home / "stderr"
        self.started = time.monotonic()
        # Trace only the isolated fixture environment, never inherited secrets.
        # Files keep a leaked child's open output pipe from hiding CLI exit.
        with self.output_path.open("w") as output, self.trace_path.open("w") as trace:
            self.process = subprocess.Popen(
                ["/bin/bash", "-x", str(ROOT / "scripts/jobs.sh"), "send", JOB_ID, "hello"],
                env=self.env, cwd=ROOT, start_new_session=True, close_fds=True,
                stdout=output, stderr=trace, text=True,
            )

    def wait_for_blocked_writer(self):
        # Observe the real writer PID and its printf attempt without requiring ps
        # (process enumeration is denied in the macOS execution sandbox).
        deadline = self.started + 3
        while time.monotonic() < deadline:
            trace = self.trace_path.read_text()
            match = re.search(r"^\+ writer_pid=(\d+)$", trace, re.MULTILINE)
            if match and re.search(r"^\+ printf .*codex-user", trace, re.MULTILINE):
                self.writer_pid = int(match[1])
                os.kill(self.writer_pid, 0)
                self.assertIsNone(self.process.poll(), "send exited before backpressure")
                return
            time.sleep(0.1)
        self.fail("did not observe a persistent FIFO writer child")

    def finish(self, expected):
        try:
            self.process.wait(
                timeout=max(0.01, self.started + HARD_TIMEOUT - time.monotonic())
            )
        except subprocess.TimeoutExpired:
            self.fail("send exceeded outer hard timeout (12s)")
        elapsed = time.monotonic() - self.started
        out = self.output_path.read_text()
        err = self.trace_path.read_text()
        self.assertLess(elapsed, HARD_TIMEOUT)
        self.assertEqual(self.process.returncode, expected, (out, err))
        if expected:
            self.assertNotIn("sent " + JOB_ID, out)
        else:
            self.assertEqual(out.strip(), "sent " + JOB_ID)
        # Check BEFORE cleanup: cleanup must never make a leaking sender pass.
        deadline = time.monotonic() + 1
        while self.group_exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.group_exists(), "sender left processes behind")
        if hasattr(self, "writer_pid"):
            with self.assertRaises(ProcessLookupError, msg="writer still exists"):
                os.kill(self.writer_pid, 0)
        return elapsed

    def test_no_reader_times_out_and_reaps_writer(self):
        self.start_send()
        self.wait_for_blocked_writer()
        self.assertGreaterEqual(self.finish(124), 4)

    def test_reader_stops_consuming_full_fifo(self):
        self.open_reader()
        self.assertGreater(self.fill_fifo(), 0)
        self.start_send()
        self.wait_for_blocked_writer()
        self.assertGreaterEqual(self.finish(124), 4)

    def test_reader_disappears_during_blocked_write(self):
        self.open_reader()
        self.assertGreater(self.fill_fifo(), 0)
        # Transfer the sole read end to a real process. Its normal exit, rather
        # than the test closing a descriptor, removes the reader mid-send.
        reader_process = subprocess.Popen(
            [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, pass_fds=(self.reader,), env=self.env,
        )

        def cleanup_reader():
            if reader_process.poll() is None:
                reader_process.kill()
            reader_process.wait(timeout=2)
            reader_process.stdin.close()

        self.addCleanup(cleanup_reader)
        os.close(self.reader)
        self.reader = None
        self.start_send()
        self.wait_for_blocked_writer()
        reader_process.communicate(b"q", timeout=2)
        self.assertEqual(reader_process.returncode, 0)
        # SIGPIPE proves the writer opened the FIFO before its reader vanished;
        # a writer stuck opening it instead would hit the five-second timeout.
        self.finish(128 + signal.SIGPIPE)

    def test_normal_send_delivers_complete_message(self):
        self.open_reader()
        self.start_send()
        received = b""
        deadline = self.started + HARD_TIMEOUT
        while not received.endswith(b"\n") and time.monotonic() < deadline:
            try:
                received += os.read(self.reader, 4096)
            except BlockingIOError:
                pass
            time.sleep(0.01)
        self.assertTrue(received.endswith(b"\n"), "message was not fully delivered")
        self.finish(0)
        self.assertEqual(json.loads(received), {"type": "codex-user", "text": "hello"})


if __name__ == "__main__":
    unittest.main()
