"""A close without a runner writer frees a reader blocked in open(2) and publishes nothing."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


# Use measured Opus 5.5: these fixtures test lifecycle, not bound transport proof.
ROOT = Path(__file__).resolve().parents[1]
SHELLS = list(dict.fromkeys(["/bin/bash", shutil.which("bash") or "/bin/bash"]))
# Only reached when something is wrong, so a slow machine cannot fail a correct worker.
EVENT_LIMIT = 60

# The child really blocks in open(2) and survives TERM, so only a writer can free it,
# and it outlives a runner that gets killed.
RUNNER = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import sys
import time

root = Path(os.environ["PROBE_ROOT"])
clock = Path(os.environ["FAKE_CLOCK_DIR"])


def await_file(path):
    while not path.exists():
        time.sleep(0.005)


child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    await_file(root / "enter-open")
    (clock / "reader-entering").touch()
    fd = os.open(os.environ["OMNILANE_INBOX"], os.O_RDONLY)
    (clock / "reader-opened").touch()
    while True:
        data = os.read(fd, 65536)
        if not data:
            break
        with open(root / "received", "ab") as received:
            received.write(data)
    (root / "reader-eof").touch()
    os._exit(0)
signal.signal(signal.SIGTERM, lambda *_: (root / "runner-term").touch())
os.waitpid(child, 0)
sys.exit(0)
'''

# Stands in for Time::HiRes on PERL5LIB, so job-worker.sh's own Perl runs unchanged.
# It tells its callers apart by their arguments. A close timer's clock stands still
# until clock-run appears, and a release cannot miss a reader on its way into open(2).
FAKE_HIRES = r"""
package Time::HiRes;
use strict;
my $dir = $ENV{FAKE_CLOCK_DIR};
my $kind = @ARGV == 3 ? "helper" : (@ARGV == 1 && $ARGV[0] =~ /\A[0-9.]+\z/ ? "timer" : "release");
my $number = 1;
$number++ until mkdir("$dir/$kind-$number");
my $ticks = 0;
sub touch { open(my $file, ">", "$dir/$_[0]") or die $!; close $file }
sub await {
    my ($name) = @_;
    my $give_up = CORE::time() + 60;
    CORE::select(undef, undef, undef, 0.002) until -e "$dir/$name" || CORE::time() >= $give_up;
}
BEGIN {
    *CORE::GLOBAL::sysopen = sub (*$$;$) {
        my $give_up = CORE::time() + 60;
        while (1) {
            my $opened = CORE::sysopen($_[0], $_[1], $_[2]);
            return $opened if $opened || $kind ne "release" || !$!{ENXIO} || !-e "$dir/reader-entering"
                || -e "$dir/reader-opened" || CORE::time() >= $give_up;
            CORE::select(undef, undef, undef, 0.002);
        }
    };
}
sub CLOCK_MONOTONIC () { 1 }
sub clock_gettime {
    return CORE::time() if $kind ne "timer";
    return -e "$dir/clock-run" ? 1000000 + $ticks : 1000;
}
sub sleep {
    my ($seconds) = @_;
    if ($kind eq "timer") {
        $ticks++;
        $seconds = 0.005;
    } elsif ($kind eq "release" && -e "$dir/reader-entering") {
        await("reader-opened");
    } elsif ($kind eq "helper" && -e "$dir/hold-helper" && @ARGV == 3 && -e $ARGV[2]) {
        await("never");
    }
    CORE::select(undef, undef, undef, $seconds);
    return $seconds;
}
sub import {
    my $caller = caller;
    no strict "refs";
    for my $name (@_[1 .. $#_]) { *{"${caller}::$name"} = \&{$name} }
    if ($kind eq "release" && $number == 1 && -e "$dir/hold-first-release") {
        touch("first-release-starting");
        await("reader-entering");
    }
    if ($kind eq "timer") { touch("timer-$number-started") }
}
1;
"""

# inbox.ready exists only between publishing and this removal, so look just before it.
RM = r'''#!/bin/bash
for path in "$@"; do
  if [[ "$path" == */inbox.ready && -e "$path" ]]; then : > "$PROBE_ROOT/ready-published"; fi
done
exec /bin/rm "$@"
'''


class ReaderReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-reader-release-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists(), "offline guard triggered"))
        self.runtime = self.root / "runtime"
        shutil.copytree(ROOT / "scripts/lib", self.runtime / "scripts/lib")
        shutil.copytree(ROOT / "config", self.runtime / "config")
        runner = self.runtime / "scripts/runners/run-claude.sh"
        runner.parent.mkdir()
        runner.write_text(RUNNER)
        runner.chmod(0o755)
        library = self.root / "fake-perl/Time"
        library.mkdir(parents=True)
        (library / "HiRes.pm").write_text(FAKE_HIRES)
        tools = self.root / "tools"
        tools.mkdir()
        (tools / "rm").write_text(RM)
        (tools / "rm").chmod(0o755)
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("offline reader release fixture\n")
        self.env.update(PROBE_ROOT=str(self.root), OMNILANE_HOME=str(self.root / "home"),
                        OMNILANE_SESSION_MODE="live", OMNILANE_LIVE_REQUIRED="1",
                        OMNILANE_IDLE_TIMEOUT="0", OMNILANE_TIMEOUT="600",
                        PERL5LIB=str(self.root / "fake-perl"),
                        PATH=str(tools) + os.pathsep + self.env["PATH"])
        self.process = None
        self.addCleanup(self.kill_group)

    def kill_group(self):
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.process.wait(timeout=5)

    def start(self, shell, name, *clock_files, enter_open=False):
        self.kill_group()
        self.base = self.root / name
        self.clock = self.base / "clock"
        self.clock.mkdir(parents=True)
        for path in ("enter-open", "received", "reader-eof", "runner-term", "ready-published"):
            (self.root / path).unlink(missing_ok=True)
        for path in clock_files:
            (self.clock / path).touch()
        if enter_open:
            (self.root / "enter-open").touch()
        self.job = self.base / "job"
        self.job.mkdir()
        self.stderr_path = self.job / "stderr"
        with self.stderr_path.open("w") as stderr, (self.job / "stdout").open("w") as stdout:
            self.process = subprocess.Popen(
                [shell, str(self.runtime / "scripts/lib/job-worker.sh"), "claude", "work",
                 str(self.root), "claude-opus-5-5", "high", str(self.prompt), str(self.job / "out.txt")],
                env=dict(self.env, FAKE_CLOCK_DIR=str(self.clock)),
                stdout=stdout, stderr=stderr, start_new_session=True)

    def await_path(self, path, why):
        deadline = time.monotonic() + EVENT_LIMIT
        while not path.exists():
            if time.monotonic() > deadline:
                self.fail(f"{why}: {path.name} never appeared: " + self.stderr_path.read_text())
            time.sleep(0.005)

    def await_first(self, why, *paths):
        deadline = time.monotonic() + EVENT_LIMIT
        while True:
            for path in paths:
                if path.exists():
                    return path
            if time.monotonic() > deadline:
                self.fail(why + ": " + self.stderr_path.read_text())
            time.sleep(0.005)

    def await_helper(self):
        self.await_path(self.clock / "helper-1", "the inbox helper never started")

    def finish(self):
        try:
            return self.process.wait(timeout=EVENT_LIMIT)
        except subprocess.TimeoutExpired:
            self.fail("worker did not finish: " + self.stderr_path.read_text())

    def close_with_reader_blocked_at_the_first_release(self, shell, name):
        self.start(shell, name, "hold-first-release")
        self.await_helper()
        self.process.send_signal(signal.SIGUSR1)
        first = self.await_first("the close never released or waited",
                                 self.clock / "first-release-starting", self.clock / "timer-1-started")
        self.assertEqual(first.name, "first-release-starting",
                         "the close grace started before a reader in open(2) was released")
        (self.root / "enter-open").touch()
        self.await_path(self.root / "reader-eof", "the first release did not free the reader")
        self.finish()

    def test_no_writer_publishes_no_inbox_and_sends_no_prompt(self):
        for index, shell in enumerate(SHELLS):
            with self.subTest(shell=shell):
                self.close_with_reader_blocked_at_the_first_release(shell, f"no-writer-{index}")
                self.assertFalse((self.root / "ready-published").exists(),
                                 "inbox.ready was published with no runner writer")
                self.assertFalse((self.root / "received").exists(), "a reader got a prompt")
                self.assertFalse((self.job / "inbox.ready").exists())

    def test_first_release_frees_a_reader_before_the_grace_starts(self):
        for index, shell in enumerate(SHELLS):
            with self.subTest(shell=shell):
                self.close_with_reader_blocked_at_the_first_release(shell, f"first-{index}")
                self.assertFalse((self.root / "runner-term").exists(), "a freed runner still got TERM")

    def test_second_release_frees_a_reader_that_arrives_after_the_first(self):
        for index, shell in enumerate(SHELLS):
            with self.subTest(shell=shell):
                self.start(shell, f"second-{index}")
                self.await_helper()
                self.process.send_signal(signal.SIGUSR1)
                self.await_path(self.clock / "timer-1-started", "the close grace never started")
                # The first release has returned, since the worker waits for it.
                self.assertTrue((self.clock / "release-1").exists(), "no first release")
                (self.root / "enter-open").touch()
                self.await_path(self.clock / "reader-entering", "the reader never tried to open")
                (self.clock / "clock-run").touch()
                self.finish()
                self.await_path(self.root / "reader-eof",
                                "a reader in open(2) outlived the worker and its killed runner")
                self.assertTrue((self.clock / "release-2").exists(), "no second release")

    def test_signal_after_the_release_marker_removes_the_marker(self):
        # The worker waits about 5s for a helper that does not leave, and then removes
        # the marker itself. A slow machine can miss that wait, so that run says nothing.
        for index, shell in enumerate(SHELLS):
            with self.subTest(shell=shell):
                for attempt in range(5):
                    self.start(shell, f"marker-{index}-{attempt}", "hold-helper", enter_open=True)
                    marker = self.job / "runner-inbox.fifo.opened"
                    self.await_path(marker, "the release marker never appeared")
                    self.process.send_signal(signal.SIGTERM)
                    self.finish()
                    if "did not exit" not in self.stderr_path.read_text():
                        break
                else:
                    self.fail("the worker's helper wait always ended before the signal")
                self.assertFalse(marker.exists(), "the release marker outlived the worker")


if __name__ == "__main__":
    unittest.main()
