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
import threading
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
# "deadline" fires during the sleep that crosses FAKE_DEADLINE. With
# FAKE_KILL_LOG set, every PID looks alive to Perl (a reused PID) and USR1 is
# logged instead of sent; FAKE_KILL_FIRST_DEAD makes the first probe say dead.
FAKE_HIRES = textwrap.dedent("""
    package Time::HiRes;
    use strict;
    my $now;
    BEGIN {
        *CORE::GLOBAL::kill = sub {
            return CORE::kill($_[0], @_[1 .. $#_]) unless $ENV{FAKE_KILL_LOG};
            my ($signal, @pids) = @_;
            return 0 if $signal eq "0" && $ENV{FAKE_KILL_FIRST_DEAD} && !$Time::HiRes::probed++;
            if ($signal eq "USR1") {
                open(my $log, ">>", $ENV{FAKE_KILL_LOG}) or die $!;
                print $log "$signal @pids\\n";
                close $log;
            }
            return scalar @pids;
        };
        *CORE::GLOBAL::readline = sub (*) {
            my $value = CORE::readline($_[0]);
            if ($ENV{FAKE_READ_ACTION}) {
                open(my $marker, ">", $ENV{FAKE_READ_MARKER}) or die $!;
                print $marker "read";
                close $marker;
                if ($ENV{FAKE_READ_ACTION} eq "deadline") {
                    $now = 1000 * $ENV{FAKE_DEADLINE};
                } else {
                    unlink $ENV{FAKE_HOLDER} or die $!;
                }
            }
            return $value;
        };
    }
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


def start_time(pid):
    env = dict(os.environ, TZ="UTC", LC_ALL="C")
    return subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], env=env, capture_output=True,
                          text=True, timeout=5, check=True).stdout.strip()


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

    def write_holder_start(self, pid):
        (self.job / "inbox.holder.pid.start").write_text(start_time(pid) + "\n")

    def sleeper(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.kill)
        return process

    def publish_pid(self):
        self.write_holder_start(self.holder.pid)
        tmp = self.job / "inbox.holder.pid.tmp"
        tmp.write_text(f"{self.holder.pid}\n")
        os.replace(tmp, self.job / "inbox.holder.pid")

    def close(self):
        return subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for_receipt_poller(self, closer):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            self.assertIsNone(closer.poll(), "close returned before entering its receipt wait")
            listing = subprocess.run(["ps", "-A", "-ww", "-o", "ppid=,command="],
                                     capture_output=True, text=True, timeout=5).stdout
            for line in listing.splitlines():
                fields = line.split(None, 1)
                if (len(fields) == 2 and fields[0] == str(closer.pid)
                        and str(self.job / "exit") in fields[1]):
                    return
            time.sleep(0.02)
        self.fail("close never entered its receipt wait")

    def test_holder_pid_written_after_close_started(self):
        self.start_holder()
        closer = self.close()
        self.wait_for_receipt_poller(closer)
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
        self.wait_for_receipt_poller(closer)
        tmp = self.job / ".exit.tmp"
        tmp.write_text("0\n")
        os.replace(tmp, self.job / "exit")
        out, err = closer.communicate(timeout=15)
        self.assertEqual(closer.returncode, 0, (out, err))
        self.assertEqual(out, "already closed (exit 0)\n")


    def fake_close(self, events, read_action="", kill_log="", kill_first_dead=""):
        lib = self.home / "fake-perl" / "Time"
        lib.mkdir(parents=True)
        (lib / "HiRes.pm").write_text(FAKE_HIRES)
        env = dict(self.env, PERL5LIB=str(self.home / "fake-perl"), FAKE_DEADLINE="11",
                   FAKE_KILL_LOG=str(kill_log), FAKE_KILL_FIRST_DEAD=kill_first_dead,
                   FAKE_READ_ACTION=read_action, FAKE_READ_MARKER=str(self.home / "read-marker"),
                   FAKE_HOLDER=str(self.job / "inbox.holder.pid"),
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
        self.write_holder_start(self.holder.pid)
        result = self.fake_close([("10.5", self.job / "inbox.holder.pid", f"{self.holder.pid}\n")])
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIn(f"closed {JOB_ID}", result.stdout)

    def test_read_crossing_deadline_expires_unsignaled_with_status_one(self):
        self.start_holder()
        self.write_holder_start(self.holder.pid)
        result = self.fake_close([("10.5", self.job / "inbox.holder.pid", f"{self.holder.pid}\n")],
                                 read_action="deadline")
        self.assertTrue((self.home / "read-marker").exists())
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "live inbox holder is unavailable\n"))
        time.sleep(0.3)
        self.assertIsNone(self.holder.poll(), "holder was signalled after reading crossed the deadline")
        self.assertFalse((self.job / "exit").exists())

    def test_holder_removed_during_read_waits_for_receipt(self):
        result = self.fake_close([("1", self.job / "inbox.holder.pid", "invalid\n"),
                                  ("2", self.job / "exit", "0\n")], read_action="vanish")
        self.assertTrue((self.home / "read-marker").exists())
        self.assertFalse((self.job / "inbox.holder.pid").exists())
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "already closed (exit 0)\n", ""))

    def test_dead_holder_pid_reused_later_is_not_signaled(self):
        # jobs.sh sees the recorded PID dead; from then on Perl sees every PID
        # alive, as it would once the OS hands the PID to an unrelated process.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=5)
        (self.job / "inbox.holder.pid").write_text(f"{dead.pid}\n")
        kill_log = self.home / "kill-log"
        result = self.fake_close([], kill_log=kill_log)
        self.assertFalse(kill_log.exists(), "close signalled a PID it had already seen dead")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "live inbox holder is gone\n"))

    def test_fake_kill_log_sees_a_late_holder_signal(self):
        # Guards the reuse test above: the same fake kill does log a USR1 sent
        # to a PID published after close started.
        kill_log = self.home / "kill-log"
        sleeper = self.sleeper()
        self.write_holder_start(sleeper.pid)
        self.fake_close([("1", self.job / "inbox.holder.pid", f"{sleeper.pid}\n")], kill_log=kill_log)
        self.assertEqual(kill_log.read_text(), f"USR1 {sleeper.pid}\n")

    def test_late_holder_pid_seen_dead_is_not_probed_again(self):
        # The PID file appears at 1s. The first probe says dead; every later one
        # says alive, as it would once the PID is reused during the wait.
        kill_log = self.home / "kill-log"
        result = self.fake_close([("1", self.job / "inbox.holder.pid", "999999\n")],
                                 kill_log=kill_log, kill_first_dead="1")
        self.assertFalse(kill_log.exists(), "close signalled a late PID it had already seen dead")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "live inbox holder is unavailable\n"))

    def dead_pid(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=5)
        return dead.pid

    def timed_close(self, env=None):
        started = time.monotonic()
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID],
                                env=env or self.env, capture_output=True, text=True, timeout=20)
        return result, time.monotonic() - started

    def test_dead_holder_and_dead_receipt_writer_fails_at_once(self):
        (self.job / "inbox.holder.pid").write_text(f"{self.dead_pid()}\n")
        (self.job / "pid").write_text(f"{self.dead_pid()}\n")
        result, elapsed = self.timed_close()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "live inbox holder is gone\n"))
        self.assertLess(elapsed, 5, "close waited for a receipt nobody can write")

    def test_missing_holder_and_dead_receipt_writer_fails_at_once(self):
        (self.job / "pid").write_text(f"{self.dead_pid()}\n")
        result, elapsed = self.timed_close()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "live inbox holder is unavailable\n"))
        self.assertLess(elapsed, 5, "close waited for a receipt nobody can write")

    def test_dead_holder_with_live_receipt_writer_waits_for_the_receipt(self):
        writer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(writer.wait, 5)
        self.addCleanup(writer.kill)
        (self.job / "inbox.holder.pid").write_text(f"{self.dead_pid()}\n")
        (self.job / "pid").write_text(f"{writer.pid}\n")
        def publish_receipt():
            tmp = self.job / ".exit.tmp"
            tmp.write_text("137\n")
            os.replace(tmp, self.job / "exit")
        timer = threading.Timer(1.0, publish_receipt)
        timer.start()
        self.addCleanup(timer.join)
        result, elapsed = self.timed_close()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (137, "already closed (exit 137)\n", ""))
        self.assertGreaterEqual(elapsed, 0.9)

    def test_receipt_written_as_its_writer_dies_is_not_missed(self):
        # The receipt lands while close reads the writer's PID, which is already
        # dead. Close has to look for the receipt after that, not before.
        shims = self.home / "shims"
        shims.mkdir()
        (shims / "cat").write_text(textwrap.dedent("""\
            #!/bin/bash
            for arg in "$@"; do
              if [[ "$arg" == "$CLOSE_TEST_PID_FILE" ]]; then
                printf '0\\n' > "$CLOSE_TEST_RECEIPT.tmp"
                /bin/mv "$CLOSE_TEST_RECEIPT.tmp" "$CLOSE_TEST_RECEIPT"
              fi
            done
            exec /bin/cat "$@"
        """))
        (shims / "cat").chmod(0o755)
        (self.job / "inbox.holder.pid").write_text(f"{self.dead_pid()}\n")
        (self.job / "pid").write_text(f"{self.dead_pid()}\n")
        env = dict(self.env, PATH=f"{shims}:{os.environ['PATH']}",
                   CLOSE_TEST_PID_FILE=str(self.job / "pid"), CLOSE_TEST_RECEIPT=str(self.job / "exit"))
        result, _ = self.timed_close(env)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "already closed (exit 0)\n", ""))

    def test_receipt_written_as_its_writer_is_probed_is_not_missed(self):
        # The receipt lands while close probes the writer, which is then dead.
        # kill is a bash builtin, so PATH cannot intercept it; a function from
        # BASH_ENV can. Close has to look for the receipt after the probe.
        writer_pid = self.dead_pid()
        bash_env = self.home / "bash-env"
        bash_env.write_text(textwrap.dedent("""\
            kill() {
              if [[ "$1" == "-0" && "$2" == "$CLOSE_TEST_WRITER_PID" ]]; then
                printf '0\\n' > "$CLOSE_TEST_RECEIPT.tmp"
                /bin/mv "$CLOSE_TEST_RECEIPT.tmp" "$CLOSE_TEST_RECEIPT"
              fi
              builtin kill "$@"
            }
        """))
        (self.job / "inbox.holder.pid").write_text(f"{self.dead_pid()}\n")
        (self.job / "pid").write_text(f"{writer_pid}\n")
        env = dict(self.env, BASH_ENV=str(bash_env), CLOSE_TEST_WRITER_PID=str(writer_pid),
                   CLOSE_TEST_RECEIPT=str(self.job / "exit"))
        result, _ = self.timed_close(env)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "already closed (exit 0)\n", ""))

    def test_late_signal_past_the_deadline_says_the_worker_is_still_shutting_down(self):
        sleeper = self.sleeper()
        self.write_holder_start(sleeper.pid)
        result = self.fake_close([("1", self.job / "inbox.holder.pid", f"{sleeper.pid}\n")],
                                 kill_log=self.home / "kill-log")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (124, "", "close signal sent late (the holder PID appeared after close started); "
                                   "the worker is still shutting down, run jobs close again for its exit status\n"))

    def test_signal_at_start_past_the_deadline_keeps_its_message(self):
        ready = self.home / "holder-ready"
        self.holder = subprocess.Popen([sys.executable, "-c",
                                        "import pathlib, signal, sys, time\n"
                                        "signal.signal(signal.SIGUSR1, signal.SIG_IGN)\n"
                                        "pathlib.Path(sys.argv[1]).write_text('1')\n"
                                        "time.sleep(30)\n", str(ready)])
        end = time.monotonic() + 5
        while not ready.exists():
            self.assertLess(time.monotonic(), end, "fixture holder did not start")
            time.sleep(0.02)
        self.publish_pid()
        result = self.fake_close([])
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (124, "", "close signal sent, but the live worker did not terminate within 11s\n"))


if __name__ == "__main__":
    unittest.main()
