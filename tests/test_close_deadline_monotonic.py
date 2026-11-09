"""jobs close keeps its 11s receipt wait on the monotonic clock.

The real jobs.sh runs with a Time::HiRes shim on PERL5LIB that loads the real
module and then makes Time::HiRes::time jump one hour forward on every call. A
wait measured on the wall clock sees its deadline pass on the second reading and
gives up before the receipt is written; a wait on CLOCK_MONOTONIC is unaffected.
"""
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
# Hang guard only; the test checks observable state, not how fast the host schedules it.
GENEROUS = 60

# Loads the real Time::HiRes, then replaces its wall-clock time() with one that
# jumps forward an hour per call. clock_gettime, CLOCK_MONOTONIC and sleep stay real.
WALL_JUMP_SHIM = r"""package Time::HiRes;
no warnings;
BEGIN {
  (my $dir = __FILE__) =~ s{/Time/HiRes\.pm\z}{};
  my $self = $INC{'Time/HiRes.pm'};
  delete $INC{'Time/HiRes.pm'};
  local @INC = grep { $_ ne $dir } @INC;
  require Time::HiRes;
  $INC{'Time/HiRes.pm'} = $self;
}
my $real = \&Time::HiRes::time;
my $calls = 0;
*Time::HiRes::time = sub () { $calls++; $real->() + 3600 * $calls };
1;
"""


class CloseDeadlineMonotonicTests(unittest.TestCase):
    def setUp(self):
        # offline_env points tempfile at the suite's private temporary root, outside the checkout.
        self.scratch = tempfile.TemporaryDirectory()
        base = Path(self.scratch.name)
        self.job = base / "home" / "jobs" / JOB_ID
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text('{"lane":"hardest-coding","vendor":"codex","session_mode":"live"}')
        shim = base / "perl" / "Time" / "HiRes.pm"
        shim.parent.mkdir(parents=True)
        shim.write_text(WALL_JUMP_SHIM)
        self.perl_lib = str(base / "perl")
        self.env = dict(os.environ, OMNILANE_HOME=str(base / "home"), PERL5LIB=self.perl_lib)

    def tearDown(self):
        self.scratch.cleanup()

    def test_shim_makes_the_wall_clock_jump(self):
        # Guards the test itself: without a working shim it would prove nothing.
        out = subprocess.run(
            ["perl", "-MTime::HiRes=time,clock_gettime,CLOCK_MONOTONIC", "-e",
             'my $w = time; my $m = clock_gettime(CLOCK_MONOTONIC); '
             'my $w2 = time; my $m2 = clock_gettime(CLOCK_MONOTONIC); '
             'printf "%d %d\n", ($w2 - $w) >= 3000, ($m2 - $m) < 5'],
            env=self.env, capture_output=True, text=True, timeout=GENEROUS)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "1 1\n", ""))

    def poller_running(self, closer):
        ps = subprocess.run(["ps", "-A", "-ww", "-o", "pgid=,command="],
                            capture_output=True, text=True, timeout=GENEROUS)
        self.assertEqual(ps.returncode, 0, ps.stderr)
        exit_path = str(self.job / "exit")
        for line in ps.stdout.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[0] == str(closer.pid) and "perl" in fields[1] and exit_path in fields[1]:
                return True
        return False

    def test_wall_clock_jumps_do_not_end_the_receipt_wait(self):
        # No holder PID file and no worker PID: close takes the missing-holder path
        # and waits up to 11s for the exit receipt.
        closer = subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, start_new_session=True)
        try:
            end = time.monotonic() + GENEROUS
            while not self.poller_running(closer):
                if closer.poll() is not None:
                    out, err = closer.communicate(timeout=GENEROUS)
                    self.fail(("close gave up before the receipt was written", closer.returncode, out, err))
                self.assertLess(time.monotonic(), end, "close never reached the receipt wait")
                time.sleep(.02)
            # Let the poller read the clock many times; each wall-clock reading jumps an hour.
            time.sleep(1)
            self.assertIsNone(closer.poll(), "close stopped waiting while the wall clock jumped")
            receipt = self.job / ".exit.tmp"
            receipt.write_text("0\n")
            os.replace(receipt, self.job / "exit")
            out, err = closer.communicate(timeout=GENEROUS)
        finally:
            try:
                os.killpg(closer.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            if closer.poll() is None:
                closer.wait(timeout=GENEROUS)
            for stream in (closer.stdout, closer.stderr):
                if stream and not stream.closed:
                    stream.close()
        self.assertEqual((closer.returncode, out, err), (0, "already closed (exit 0)\n", ""))


if __name__ == "__main__":
    unittest.main()
