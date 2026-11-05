"""Owned offline fixtures for a watchdog that survives child alarm changes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class CallTimeoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-call-timeout-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "tools", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.env["OMNILANE_JOB_SUPERVISED"] = "1"

    def run_call(self, code, seconds="1"):
        child = self.root / "child.py"
        child.write_text(code)
        started = time.monotonic()
        result = subprocess.run(
            ["bash", "-c", 'source "$1/scripts/lib/common.sh"; run_with_timeout "$2" "$3" "$4"',
             "fixture", str(ROOT), seconds, sys.executable, str(child)],
            env=self.env, text=True, capture_output=True, timeout=8)
        return result, time.monotonic() - started

    def test_child_clearing_alarm_cannot_disable_deadline(self):
        result, elapsed = self.run_call("import signal,time\nsignal.alarm(0)\ntime.sleep(4)\n")
        self.assertEqual(142, result.returncode, result.stderr)
        self.assertLess(elapsed, 6)

    def test_child_ignoring_alarm_and_term_is_escalated(self):
        result, elapsed = self.run_call(
            "import signal,time\nsignal.signal(signal.SIGALRM,signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(4)\n")
        self.assertEqual(142, result.returncode, result.stderr)
        self.assertGreaterEqual(elapsed, 1.9)
        self.assertLess(elapsed, 6)

    def test_normal_success_and_failure_preserve_output_and_status(self):
        for code in (0, 7):
            with self.subTest(code=code):
                result, _ = self.run_call(f"print('owned output')\nraise SystemExit({code})\n", "5")
                self.assertEqual(code, result.returncode, result.stderr)
                self.assertEqual("owned output\n", result.stdout)

    def test_stopped_watchdog_rechecks_deadline_after_successful_child_exit(self):
        # Freeze only our watchdog after its owned child is ready. The child
        # finishes normally while the watchdog cannot observe the deadline.
        child = self.root / "child.py"
        ready = self.root / "ready"
        child.write_text("import pathlib,sys,time\n"
                         "pathlib.Path(sys.argv[1]).write_text('ready')\n"
                         "time.sleep(1.3)\nprint('complete', flush=True)\n")
        process = subprocess.Popen(
            ["perl", str(ROOT / "scripts/lib/call-timeout.pl"), "1",
             sys.executable, str(child), str(ready)],
            env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "owned child failed to start")
            process.send_signal(signal.SIGSTOP)
            time.sleep(1.6)
            process.send_signal(signal.SIGCONT)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(142, process.returncode, stderr)
            self.assertEqual("complete\n", stdout)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGCONT)
                process.kill()
                process.communicate(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def test_child_stays_in_callers_process_group(self):
        result, _ = self.run_call("import os\nprint(os.getpgrp())\n", "5")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(os.getpgrp(), int(result.stdout))

    def test_missing_command_preserves_exec_failure(self):
        result = subprocess.run(
            ["perl", str(ROOT / "scripts/lib/call-timeout.pl"), "1", str(self.root / "missing")],
            env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(127, result.returncode)

    def test_parent_term_stops_owned_child_and_preserves_signal_status(self):
        child = self.root / "child.py"
        child.write_text("import os,signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                         "print(os.getpid(),flush=True)\ntime.sleep(4)\n")
        process = subprocess.Popen(
            ["perl", str(ROOT / "scripts/lib/call-timeout.pl"), "10", sys.executable, str(child)],
            env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            child_pid = int(process.stdout.readline())
            process.terminate()
            stdout, stderr = process.communicate(timeout=4)
            self.assertEqual(143, process.returncode, stderr)
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], env=self.env,
                                   text=True, capture_output=True, timeout=2)
            self.assertTrue(not state.stdout.strip() or state.stdout.lstrip().startswith("Z"))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def test_inherited_ignored_sigchld_preserves_exit_and_timeout(self):
        launcher = ('import os,signal,sys; signal.signal(signal.SIGCHLD,signal.SIG_IGN); '
                    'os.execvp("perl", ["perl"] + sys.argv[1:])')
        cases = (("5", "raise SystemExit(7)", 7),
                 ("1", "import signal,time; signal.alarm(0); time.sleep(4)", 142))
        for seconds, code, expected in cases:
            with self.subTest(expected=expected):
                result = subprocess.run(
                    [sys.executable, "-c", launcher, str(ROOT / "scripts/lib/call-timeout.pl"),
                     seconds, sys.executable, "-c", code],
                    env=self.env, text=True, capture_output=True, timeout=8)
                self.assertEqual(expected, result.returncode, result.stderr)

    def test_interrupted_wait_retries_and_preserves_child_status(self):
        # Inject one EINTR at the actual Perl wait seam; subsequent waits use
        # the real owned child. No unrelated PID or extra signal is involved.
        launcher = r'''
use Errno qw(EINTR);
BEGIN {
    *CORE::GLOBAL::waitpid = sub {
        if (!$main::injected++) { $! = EINTR; return -1; }
        return CORE::waitpid($_[0], $_[1]);
    };
}
END { print STDERR "injected-waitpid\n" if $main::injected; }
do shift @ARGV;
die $@ if $@;
'''
        result = subprocess.run(
            ["perl", "-e", launcher, str(ROOT / "scripts/lib/call-timeout.pl"), "5",
             sys.executable, "-c", "raise SystemExit(7)"],
            env=self.env, text=True, capture_output=True, timeout=8)
        self.assertEqual(7, result.returncode, result.stderr)
        self.assertIn("injected-waitpid", result.stderr)
