"""jobs close that runs before the live inbox holder has written its PID file.

The worker writes inbox.holder.pid only after it has started; a close that
lands earlier must keep polling for the holder within its 11s deadline,
signal it once it appears, and fail only if neither the holder nor the exit
receipt shows up in time. Real jobs.sh close; the holder is a fixture.
"""
import offline_env  # noqa: F401
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


# A virtual clock for the receipt wait in jobs.sh close: a Time::HiRes found
# first on PERL5LIB replaces time and sleep, so the 11s deadline passes at once
# and file events land at exact virtual times. FAKE_EVENTS is
# "seconds:path:content;..." relative to the first time() call; an event at
# "deadline" fires during the sleep that crosses FAKE_DEADLINE.
FAKE_HIRES = textwrap.dedent("""
    package Time::HiRes;
    use strict;
    my $now;
    my @events = map { [split /:/, $_, 3] } grep { length } split /;/, ($ENV{FAKE_EVENTS} // "");
    sub _fire {
        my ($from, $to) = @_;
        for my $event (@events) {
            next if $event->[3];
            my $at = $event->[0] eq "deadline" ? $ENV{FAKE_DEADLINE} : $event->[0];
            next unless $from < $at && $at <= $to;
            my $tmp = "$event->[1].fake-tmp";
            open(my $fh, ">", $tmp) or die "fake clock: $tmp: $!";
            print $fh $event->[2];
            close $fh;
            rename $tmp, $event->[1] or die "fake clock: rename: $!";
            $event->[3] = 1;
        }
    }
    # Whole milliseconds, so 110 sleeps of 0.1s land exactly on 11s.
    sub time () { $now //= 0; return 1000 + $now / 1000 }
    sub sleep {
        my ($seconds) = @_;
        $now //= 0;
        my $step = int($seconds * 1000 + 0.5);
        _fire($now / 1000, ($now + $step) / 1000);
        $now += $step;
        CORE::select(undef, undef, undef, 0.002);
        return $seconds;
    }
    sub import {
        my $caller = caller;
        no strict "refs";
        for my $name (@_[1 .. $#_]) { *{"${caller}::$name"} = \\&{$name} }
    }
    1;
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


    def fake_close(self, events):
        lib = self.home / "fake-perl" / "Time"
        lib.mkdir(parents=True)
        (lib / "HiRes.pm").write_text(FAKE_HIRES)
        env = dict(self.env, PERL5LIB=str(self.home / "fake-perl"), FAKE_DEADLINE="11",
                   FAKE_EVENTS=";".join(f"{at}:{path}:{content}" for at, path, content in events))
        return subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                              env=env, capture_output=True, text=True, timeout=20)

    def test_late_invalid_holder_pid_fails_at_once(self):
        # The PID file appears at 1s with junk; an exit receipt shows up at 2s.
        # Junk must fail right away with the existing message, not wait for
        # the receipt and report the job as already closed.
        result = self.fake_close([("1", self.job / "inbox.holder.pid", "abc\n"),
                                  ("2", self.job / "exit", "0\n")])
        self.assertEqual(result.returncode, 1, (result.stdout, result.stderr))
        self.assertEqual(result.stderr, "live inbox holder is unavailable\n")
        self.assertNotIn("already closed", result.stdout)
        self.assertFalse((self.job / "exit").exists(), "close kept waiting past the invalid PID file")

    def test_holder_appearing_after_the_deadline_is_not_signaled(self):
        # The holder PID is published during the sleep that crosses the 11s
        # deadline. Close must give up without signalling it.
        self.start_holder()
        result = self.fake_close([("deadline", self.job / "inbox.holder.pid", f"{self.holder.pid}\n")])
        self.assertEqual(result.returncode, 1, (result.stdout, result.stderr))
        self.assertEqual(result.stderr, "live inbox holder is unavailable\n")
        time.sleep(0.3)
        self.assertIsNone(self.holder.poll(), "holder was signalled after the deadline")
        self.assertFalse((self.job / "exit").exists(), "holder was signalled after the deadline")

    def test_fake_clock_still_signals_a_holder_before_the_deadline(self):
        # Guards the fake clock itself: a PID published at 10.5s is signalled.
        self.start_holder()
        result = self.fake_close([("10.5", self.job / "inbox.holder.pid", f"{self.holder.pid}\n")])
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIn(f"closed {JOB_ID}", result.stdout)


if __name__ == "__main__":
    unittest.main()
