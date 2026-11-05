"""jobs close when the holder PID file vanishes between its existence check and its read."""
import offline_env  # noqa: F401
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "20260905-160000-12345-1"
# Hang guard only; the tests check observable state, not how fast the host schedules them.
GENEROUS = 60
# How long close's process group may take to finish exiting after close itself has returned.
LEFTOVER_GRACE = 5

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

    def tearDown(self):
        self.scratch.cleanup()

    def close(self, env):
        # Its own process group, so cleanup also reaches the receipt poller close starts.
        return subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)

    def reap(self, closer, body_failed=False):
        """Kill what is left of close's process group, reap it, and check nothing in the group survived.

        With body_failed the test body already raised: only clean up, and never let a
        cleanup error replace the body's own exception.
        """
        pgid = closer.pid  # start_new_session: the leader's PID is the group ID
        if body_failed:
            try:
                self._killpg(pgid)
                closer.wait(timeout=GENEROUS)
            except Exception:
                pass
            for stream in (closer.stdout, closer.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
            return
        closer.wait(timeout=GENEROUS)
        for stream in (closer.stdout, closer.stderr):
            stream.close()
        # A member that is still finishing its exit just after close returned (its
        # receipt poller) is not a leftover, so a group that empties within the grace
        # passes. One still present after the grace is a leftover: it is killed whether
        # or not the leader is alive, the group must then be gone, and the test fails.
        # Zombies get no special case. closer.wait() reaps the leader, and orphaned
        # members are reaped by launchd/init, but with no bound on when: a member still
        # a zombie at the end of the grace fails the test as a leftover. Not seen so far.
        grace = time.monotonic() + LEFTOVER_GRACE
        while time.monotonic() < grace:
            if not self._group_exists(pgid):
                return
            time.sleep(.02)
        # Waking up late is not evidence that the group outlived the grace: look again.
        if not self._group_exists(pgid) or not self._killpg(pgid):
            return
        deadline = time.monotonic() + GENEROUS
        while self._group_exists(pgid):
            self.assertLess(time.monotonic(), deadline, f"process group {pgid} survived SIGKILL")
            time.sleep(.02)
        self.fail(f"process group {pgid} outlived jobs close by more than {LEFTOVER_GRACE}s")

    # On macOS killpg() answers EPERM, not ESRCH, while the group's last members are
    # still exiting: polling right after SIGKILL saw EPERM before ESRCH in 300 of 300
    # runs. EPERM therefore means "still present"; both loops are bounded, so it
    # cannot hang.
    @staticmethod
    def _group_exists(pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _killpg(pgid):
        """SIGKILL the group; False when it was already gone."""
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def receipt_poller_running(self, closer, end):
        """Whether close's direct child polling for the exit receipt is in the process table now."""
        receipt = str(self.job / "exit")
        ps = subprocess.run(["ps", "-A", "-ww", "-o", "ppid=,command="], capture_output=True,
                            text=True, timeout=max(.1, end - time.monotonic()))
        # An empty listing from a failed ps says nothing about the poller; never read it as absent.
        if ps.returncode != 0:
            self.fail(f"ps failed with exit {ps.returncode}: {ps.stderr.strip()!r}")
        for line in ps.stdout.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[0] == str(closer.pid) and receipt in fields[1]:
                return True
        return False

    def wait_for_receipt_poller(self, closer, timeout=GENEROUS):
        end = time.monotonic() + timeout
        while True:
            if closer.poll() is not None:
                out, err = closer.communicate()
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
            out, err = closer.communicate(timeout=GENEROUS)
        except BaseException:
            self.reap(closer, body_failed=True)
            raise
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
            out, err = closer.communicate(timeout=GENEROUS)
        except BaseException:
            self.reap(closer, body_failed=True)
            raise
        self.reap(closer)
        self.assertFalse(polled, "close waited for the exit receipt instead of failing at once")
        self.assertEqual((closer.returncode, out, err), (1, "", "live inbox holder is unavailable\n"))
        self.assertTrue(self.holder.exists())


if __name__ == "__main__":
    unittest.main()
