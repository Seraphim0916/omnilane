"""jobs close that runs before the live inbox holder has written its PID file.

The worker writes inbox.holder.pid only after it has started; a close that
lands earlier must keep polling for the holder within its 11s deadline,
signal it once it appears, and fail only if neither the holder nor the exit
receipt shows up in time. Real jobs.sh close; the holder is a fixture.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JOB_ID = "20261026-074500-12345-1"

# On SIGUSR1 the fixture holder writes the exit receipt the way the supervisor
# does (temp file, then rename) and stops; it never runs a provider.
HOLDER = textwrap.dedent("""
    import os, pathlib, signal, sys, time
    job = pathlib.Path(sys.argv[1])
    def close(*_):
        tmp = job / ".exit.tmp"
        tmp.write_text("0\\n")
        os.replace(tmp, job / "exit")
        os._exit(0)
    signal.signal(signal.SIGUSR1, close)
    (job.parent.parent / "holder-ready").write_text("1")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(0.05)
""")


class CloseBeforeHolderPidTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(exist_ok=True)
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="close-early-", dir=scratch))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.job = self.home / "jobs" / JOB_ID
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text('{"lane":"triage","vendor":"codex","session_mode":"live"}')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_")}
        self.env["OMNILANE_HOME"] = str(self.home)
        self.holder = None

    def tearDown(self):
        if self.holder and self.holder.poll() is None:
            self.holder.kill()
            self.holder.wait(timeout=5)

    def start_holder(self):
        self.holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.job)])
        end = time.monotonic() + 5
        while not (self.home / "holder-ready").exists():
            self.assertLess(time.monotonic(), end, "fixture holder did not start")
            time.sleep(0.02)

    def publish_pid(self):
        tmp = self.job / "inbox.holder.pid.tmp"
        tmp.write_text(f"{self.holder.pid}\n")
        os.replace(tmp, self.job / "inbox.holder.pid")

    def close(self):
        return subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_holder_pid_written_after_close_started(self):
        self.start_holder()
        closer = self.close()
        time.sleep(1.0)
        self.assertIsNone(closer.poll(), "close returned before the holder PID existed")
        self.publish_pid()
        started = time.monotonic()
        out, err = closer.communicate(timeout=15)
        self.assertEqual(closer.returncode, 0, (out, err))
        self.assertIn(f"closed {JOB_ID}", out)
        self.assertLess(time.monotonic() - started, 5, "close did not signal the holder once it appeared")
        self.holder.wait(timeout=5)

    def test_holder_already_present_is_unchanged(self):
        self.start_holder()
        self.publish_pid()
        closer = self.close()
        out, err = closer.communicate(timeout=15)
        self.assertEqual(closer.returncode, 0, (out, err))
        self.assertIn(f"closed {JOB_ID}", out)

    def test_holder_that_never_appears_still_fails_at_the_deadline(self):
        started = time.monotonic()
        closer = self.close()
        out, err = closer.communicate(timeout=20)
        elapsed = time.monotonic() - started
        self.assertEqual(closer.returncode, 1, (out, err))
        self.assertIn("live inbox holder is unavailable", err)
        self.assertGreaterEqual(elapsed, 10.5, elapsed)
        self.assertFalse((self.job / "exit").exists())

    def test_exit_receipt_without_holder_is_already_closed(self):
        closer = self.close()
        time.sleep(0.5)
        tmp = self.job / ".exit.tmp"
        tmp.write_text("0\n")
        os.replace(tmp, self.job / "exit")
        out, err = closer.communicate(timeout=15)
        self.assertEqual(closer.returncode, 0, (out, err))
        self.assertEqual(out, "already closed (exit 0)\n")


if __name__ == "__main__":
    unittest.main()
