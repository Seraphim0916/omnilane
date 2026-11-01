"""Opening the runner inbox survives USR1 and a runner that never reads it."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
# dispatch.sh always starts the worker with /bin/bash; 3.2 on macOS fails the open
# with EINTR while newer Bash blocks again, so both must converge.
SHELLS = list(dict.fromkeys(["/bin/bash", shutil.which("bash") or "/bin/bash"]))
CLOSE_DEADLINE = 11  # jobs close's own deadline; see the budget note in job-worker.sh

RUNNER = r'''#!/bin/bash
: "${PROBE_ROOT:?}"
report() {
  printf 'fixture result\n' > "$6"
  printf '{"type":"result","is_error":false,"result":"fixture result"}\n' > "$6.events.jsonl"
}
consume() {
  exec 9< "$OMNILANE_INBOX"
  IFS= read -r line <&9 || line="<EOF>"
  printf '%s\n' "$line" >> "$PROBE_ROOT/received"
}
: > "$PROBE_ROOT/started"
case "$PROBE_SCENARIO" in
  never) while :; do sleep 0.05; done ;;
  never-ignoring-term)
    trap '' TERM
    while :; do sleep 0.05; done ;;
  helper-freezes-after-ready)
    # The helper has said ready and holds both ends, then stops answering.
    worker=$PPID
    group=$(ps -o pgid= -p "$worker" | tr -d ' ')
    until pgrep -g "$group" -f O_NONBLOCK > /dev/null; do sleep 0.01; done
    kill -STOP "$worker"
    exec 9< "$OMNILANE_INBOX"
    sleep 0.1
    kill -STOP "$(pgrep -g "$group" -f O_NONBLOCK)"
    kill -CONT "$worker"
    IFS= read -r line <&9 && printf '%s\n' "$line" >> "$PROBE_ROOT/received"
    exit 0 ;;
  exit-early) exit 7 ;;
  exit-zero) exit 0 ;;
  reader-leaves-before-worker-opens)
    # Freeze the worker so the helper declares ready and this reader is gone
    # before the worker can act on it. The helper must exist first: it is the
    # only thing that can let this open return.
    worker=$PPID
    group=$(ps -o pgid= -p "$worker" | tr -d ' ')
    until pgrep -g "$group" -f O_NONBLOCK > /dev/null; do sleep 0.01; done
    kill -STOP "$worker"
    exec 9< "$OMNILANE_INBOX"
    sleep 0.1
    exec 9<&-
    : > "$PROBE_ROOT/reader-left"
    kill -CONT "$worker"
    sleep 0.5
    exit 0 ;;
  immediate) consume; report "$@"; exit 0 ;;
  late)
    until [[ -e "$PROBE_ROOT/release" ]]; do sleep 0.02; done
    consume; report "$@"; exit 0 ;;
  until-eof)
    consume
    cat <&9 > /dev/null
    exit 0 ;;
esac
exit 9
'''


def restore_hup():
    # nohup leaves SIGHUP ignored, and a child cannot undo an inherited ignore.
    signal.signal(signal.SIGHUP, signal.SIG_DFL)


class FifoOpenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-fifo-open-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists(), "offline guard triggered"))
        self.runtime = self.root / "runtime"
        shutil.copytree(ROOT / "scripts/lib", self.runtime / "scripts/lib")
        shutil.copytree(ROOT / "config", self.runtime / "config")
        runners = self.runtime / "scripts/runners"
        runners.mkdir()
        for vendor in ("claude", "codex"):
            runner = runners / f"run-{vendor}.sh"
            runner.write_text(RUNNER)
            runner.chmod(0o755)
        # Only answers the live-surface probe; the fake runner above never calls it.
        codex = self.root / "fake-codex"
        codex.write_text('#!/bin/bash\nprintf \'{"id":1,"result":{}}\\n\'\nexec cat > /dev/null\n')
        codex.chmod(0o755)
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("offline fifo open fixture\n")
        self.env.update(PROBE_ROOT=str(self.root), OMNILANE_HOME=str(self.root / "home"),
                        OMNILANE_SESSION_MODE="live", OMNILANE_LIVE_REQUIRED="1",
                        OMNILANE_IDLE_TIMEOUT="0", OMNILANE_TIMEOUT="20", CODEX_BIN=str(codex))
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

    def start(self, shell, scenario, vendor="claude", model="claude-opus-5", effort="high", **env):
        self.kill_group()
        for name in ("started", "received", "release"):
            (self.root / name).unlink(missing_ok=True)
        self.job = Path(tempfile.mkdtemp(dir=self.root, prefix="job-"))
        self.stderr_path = self.job / "stderr"
        run_env = dict(self.env, PROBE_SCENARIO=scenario, **env)
        with self.stderr_path.open("w") as stderr, (self.job / "stdout").open("w") as stdout:
            self.process = subprocess.Popen(
                [shell, str(self.runtime / "scripts/lib/job-worker.sh"), vendor, "work",
                 str(self.root), model, effort, str(self.prompt), str(self.job / "out.txt")],
                env=run_env, stdout=stdout, stderr=stderr, start_new_session=True,
                preexec_fn=restore_hup)
        deadline = time.monotonic() + 8
        while not (self.root / "started").exists():
            if time.monotonic() > deadline or self.process.poll() is not None:
                self.fail("runner never started: " + self.stderr_path.read_text())
            time.sleep(0.01)

    def finish(self, timeout):
        """The worker's exit code; a hung worker fails here rather than hanging the suite."""
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(f"worker did not finish within {timeout}s: " + self.stderr_path.read_text())

    def helper_argv(self):
        """The inbox helper's command line, or None once it is gone."""
        listing = subprocess.run(["ps", "-A", "-ww", "-o", "pgid=,pid=,command="],
                                 capture_output=True, text=True, check=True).stdout
        for row in listing.splitlines():
            pgid, pid, command = row.split(None, 2)
            if int(pgid) == self.process.pid and command.startswith("perl") and "O_NONBLOCK" in command:
                return int(pid), command
        return None

    def await_helper(self):
        deadline = time.monotonic() + 5
        while (helper := self.helper_argv()) is None:
            self.assertLess(time.monotonic(), deadline, "helper never started")
            time.sleep(0.01)
        return helper

    def assert_clean_end(self):
        stderr = self.stderr_path.read_text()
        self.assertGreaterEqual(self.process.returncode, 0, "worker died of a signal: " + stderr)
        self.assertNotIn("Interrupted system call", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertNotRegex(stderr, "Terminated.*perl")
        self.assertFalse((self.job / "inbox.ready").exists(), stderr)
        self.assertFalse((self.job / "runner-inbox.fifo").exists(), stderr)
        self.assertFalse((self.job / "runner-inbox.fifo.opened").exists(), stderr)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.killpg(self.process.pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                pass
            time.sleep(0.02)
        self.fail("process group outlived the worker: " + stderr)

    def received(self):
        path = self.root / "received"
        return path.read_text().splitlines() if path.exists() else []

    def test_prompt_reaches_a_reader_that_arrives_late(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "late")
                time.sleep(0.3)
                (self.root / "release").touch()
                self.assertEqual(self.finish(10), 0, self.stderr_path.read_text())
                self.assert_clean_end()
                self.assertEqual(len(self.received()), 1)
                self.assertIn("offline fifo open fixture", self.received()[0])

    def test_reader_never_sees_a_false_eof_from_the_open(self):
        for shell in SHELLS:
            for attempt in range(5):
                with self.subTest(shell=shell, attempt=attempt):
                    self.start(shell, "immediate")
                    self.assertEqual(self.finish(10), 0, self.stderr_path.read_text())
                    self.assertNotIn("<EOF>", self.received())
                    self.assertEqual(len(self.received()), 1)

    def test_usr1_while_opening_closes_cleanly_within_budget(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never")
                time.sleep(0.2)
                began = time.monotonic()
                self.process.send_signal(signal.SIGUSR1)
                self.finish(CLOSE_DEADLINE)
                # No message was sent, so the turn-finishing grace must not apply.
                self.assertLess(time.monotonic() - began, 3)
                self.assert_clean_end()

    def test_usr1_releases_a_reader_blocked_in_open(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "late")
                time.sleep(0.2)
                self.process.send_signal(signal.SIGUSR1)
                (self.root / "release").touch()
                self.finish(CLOSE_DEADLINE)
                self.assert_clean_end()

    def test_usr1_racing_a_prompt_open_never_kills_the_worker_mid_open(self):
        for shell in SHELLS:
            for delay in (0, 0.01, 0.05):
                with self.subTest(shell=shell, delay=delay):
                    self.start(shell, "until-eof")
                    time.sleep(delay)
                    self.process.send_signal(signal.SIGUSR1)
                    self.finish(CLOSE_DEADLINE)
                    self.assert_clean_end()
                    received = self.received()
                    self.assertLessEqual(len(received), 1, received)
                    for line in received:
                        if line != "<EOF>":
                            self.assertIn("offline fifo open fixture", line)

    def test_usr1_after_delivery_keeps_the_single_delivered_prompt(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "until-eof")
                deadline = time.monotonic() + 8
                while not self.received():
                    self.assertLess(time.monotonic(), deadline, "prompt never delivered")
                    time.sleep(0.01)
                self.process.send_signal(signal.SIGUSR1)
                self.finish(CLOSE_DEADLINE)
                self.assert_clean_end()
                self.assertEqual(len(self.received()), 1)
                self.assertIn("offline fifo open fixture", self.received()[0])

    def test_reader_leaving_before_the_worker_opens_neither_hangs_nor_succeeds(self):
        for shell in SHELLS:
            for attempt in range(3):
                with self.subTest(shell=shell, attempt=attempt):
                    (self.root / "reader-left").unlink(missing_ok=True)
                    self.start(shell, "reader-leaves-before-worker-opens")
                    rc = self.finish(CLOSE_DEADLINE)
                    self.assertTrue((self.root / "reader-left").exists(), "sync point not reached")
                    self.assertNotEqual(rc, 0, self.stderr_path.read_text())
                    self.assertEqual(self.received(), [])
                    self.assert_clean_end()

    def test_runner_exit_zero_before_opening_is_a_failure(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "exit-zero")
                rc = self.finish(6)
                self.assertEqual(rc, 1, self.stderr_path.read_text())
                self.assertIn("prompt was not delivered", self.stderr_path.read_text())
                self.assert_clean_end()

    def test_open_deadline_defaults_to_the_call_timeout(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never", OMNILANE_TIMEOUT="1")
                self.assertNotEqual(self.finish(1 + CLOSE_DEADLINE), 0)
                self.assertIn("did not open its inbox within 1s", self.stderr_path.read_text())
                self.assert_clean_end()

    def test_a_timeout_too_long_for_shell_arithmetic_does_not_end_the_open(self):
        for shell in SHELLS:
            for value in ("9223372036854775807", "9" * 400):
                with self.subTest(shell=shell, digits=len(value)):
                    self.start(shell, "late", OMNILANE_TIMEOUT=value)
                    self.await_helper()
                    time.sleep(5)
                    self.assertIsNone(self.process.poll(), self.stderr_path.read_text())
                    (self.root / "release").touch()
                    self.assertEqual(self.finish(10), 0, self.stderr_path.read_text())
                    self.assertEqual(len(self.received()), 1)

    def test_a_very_long_timeout_still_reaches_the_helper(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never", OMNILANE_TIMEOUT="10000000")
                self.assertIn(" 10000000 ", self.await_helper()[1])

    def test_frozen_helper_before_open_ends_at_the_worker_own_deadline(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never", OMNILANE_RUNNER_OPEN_TIMEOUT="1")
                helper, _ = self.await_helper()
                os.kill(helper, signal.SIGSTOP)
                self.assertNotEqual(self.finish(14), 0)
                self.assertIn("inbox helper stopped responding", self.stderr_path.read_text())

    def test_frozen_helper_before_open_does_not_delay_close(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never")
                helper, _ = self.await_helper()
                os.kill(helper, signal.SIGSTOP)
                time.sleep(0.2)
                began = time.monotonic()
                self.process.send_signal(signal.SIGUSR1)
                self.finish(CLOSE_DEADLINE)
                self.assertLess(time.monotonic() - began, 4)

    def test_brief_helper_stall_is_not_mistaken_for_its_exit(self):
        # Bash 3.2 answers a read timeout and EOF with the same status.
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "late")
                helper, _ = self.await_helper()
                os.kill(helper, signal.SIGSTOP)
                time.sleep(2.5)
                self.assertIsNone(self.process.poll(), self.stderr_path.read_text())
                os.kill(helper, signal.SIGCONT)
                (self.root / "release").touch()
                self.assertEqual(self.finish(10), 0, self.stderr_path.read_text())
                self.assertEqual(len(self.received()), 1)
                self.assert_clean_end()

    def test_killed_helper_is_reported_as_what_happened(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never")
                helper, _ = self.await_helper()
                os.kill(helper, signal.SIGKILL)
                self.assertNotEqual(self.finish(CLOSE_DEADLINE), 0)
                stderr = self.stderr_path.read_text()
                self.assertIn("inbox helper exited", stderr)
                self.assertNotIn("did not open its inbox within", stderr)
                self.assert_clean_end()

    def test_helper_frozen_after_open_never_gets_the_prompt_written(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "helper-freezes-after-ready")
                self.assertNotEqual(self.finish(5 + CLOSE_DEADLINE), 0)
                stderr = self.stderr_path.read_text()
                self.assertIn("inbox helper did not exit", stderr)
                self.assertEqual(self.received(), [])

    def test_marker_path_is_refused_when_already_present(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                job = Path(tempfile.mkdtemp(dir=self.root, prefix="job-"))
                outside = self.root / "outside-marker"
                (job / "runner-inbox.fifo.opened").symlink_to(outside)
                result = subprocess.run(
                    [shell, str(self.runtime / "scripts/lib/job-worker.sh"), "claude", "work",
                     str(self.root), "claude-opus-5", "high", str(self.prompt), str(job / "out.txt")],
                    env=dict(self.env, PROBE_SCENARIO="immediate"), capture_output=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(outside.exists())
                self.assertFalse((self.root / "started").exists())

    def test_helper_does_not_outlive_a_killed_worker(self):
        for shell in SHELLS:
            for name in ("SIGTERM", "SIGHUP", "SIGKILL"):
                with self.subTest(shell=shell, signal=name):
                    self.start(shell, "never")
                    helper, _ = self.await_helper()
                    self.process.send_signal(getattr(signal, name))
                    self.finish(5)
                    deadline = time.monotonic() + 2
                    while self.helper_argv() is not None:
                        self.assertLess(time.monotonic(), deadline, f"helper {helper} survived {name}")
                        time.sleep(0.01)

    def test_runner_that_never_opens_hits_the_deadline(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never", OMNILANE_RUNNER_OPEN_TIMEOUT="1")
                rc = self.finish(1 + CLOSE_DEADLINE)
                self.assertNotEqual(rc, 0)
                self.assertIn("did not open its inbox within 1s", self.stderr_path.read_text())
                self.assert_clean_end()

    def test_runner_exit_before_opening_reports_its_own_status(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "exit-early")
                self.assertEqual(self.finish(6), 7, self.stderr_path.read_text())
                self.assert_clean_end()

    def test_codex_close_while_opening_skips_the_writer_only_drain(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.start(shell, "never", vendor="codex", model="fake", effort="medium")
                time.sleep(0.2)
                self.process.send_signal(signal.SIGUSR1)
                self.finish(CLOSE_DEADLINE)
                self.assert_clean_end()


class RealCloseOfAnUnopenedInboxTests(unittest.TestCase):
    """Real jobs.sh close, job-worker.sh and supervisor; only the runner is fake."""

    JOB_ID = "20261101-150000-12345-1"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-fifo-close-")
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
        self.job = self.root / "home/jobs" / self.JOB_ID
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text('{"lane":"triage","vendor":"claude","session_mode":"live"}')
        prompt = self.root / "prompt.txt"
        prompt.write_text("offline fifo close fixture\n")
        self.env.update(PROBE_ROOT=str(self.root), OMNILANE_HOME=str(self.root / "home"),
                        OMNILANE_SESSION_MODE="live", OMNILANE_LIVE_REQUIRED="1",
                        OMNILANE_IDLE_TIMEOUT="0", OMNILANE_TIMEOUT="60",
                        WORKER_PROMPT=str(prompt), WORKER_ROOT=str(self.runtime),
                        WORKER_DIR=str(self.root), JOB_DIR=str(self.job))
        self.driver = None
        self.addCleanup(self.kill_job)

    def kill_job(self):
        if self.driver is None:
            return
        listing = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,command="],
                                 capture_output=True, text=True, check=True).stdout
        for row in listing.splitlines():
            pid, command = row.split(None, 1)
            if str(self.root) in command:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.driver.wait(timeout=5)

    def start_job(self, shell, scenario, delay=0):
        # What dispatch.sh does for a background job: pid file, supervisor, exit receipt.
        script = r"""
printf '%s\n' "$$" > "$JOB_DIR/pid"
sleep "$1"
perl "$WORKER_ROOT/scripts/lib/job-timeout.pl" --no-deadline "$2" \
  "$WORKER_ROOT/scripts/lib/job-worker.sh" claude work "$WORKER_DIR" claude-opus-5 high \
  "$WORKER_PROMPT" "$JOB_DIR/out.txt" > "$JOB_DIR/stdout" 2> "$JOB_DIR/stderr"
printf '%s\n' "$?" > "$JOB_DIR/exit"
"""
        self.driver = subprocess.Popen(["/bin/bash", "-c", script, "driver", str(delay), shell],
                                       env=dict(self.env, PROBE_SCENARIO=scenario),
                                       start_new_session=True)
        deadline = time.monotonic() + 8
        while not (self.job / "pid").exists():
            self.assertLess(time.monotonic(), deadline, "driver never started")
            time.sleep(0.01)

    def await_runner_started(self):
        deadline = time.monotonic() + 8
        while not ((self.root / "started").exists() and (self.job / "inbox.holder.pid").exists()):
            self.assertLess(time.monotonic(), deadline, "runner never started")
            time.sleep(0.01)
        time.sleep(0.1)

    def close(self):
        began = time.monotonic()
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", self.JOB_ID],
                                env=self.env, capture_output=True, text=True, timeout=20)
        return result, time.monotonic() - began

    def assert_finished_cleanly(self):
        self.driver.wait(timeout=12)
        self.assertEqual((self.job / "exit").read_text().strip(), "1")
        stderr = (self.job / "stderr").read_text()
        self.assertNotIn("Interrupted system call", stderr)
        self.assertFalse((self.job / "inbox.holder.pid").exists(), stderr)
        self.assertFalse((self.job / "runner-inbox.fifo").exists(), stderr)
        self.assertFalse((self.job / "runner-inbox.fifo.opened").exists(), stderr)

    def test_close_of_a_runner_that_never_opens_returns_quickly(self):
        for shell in SHELLS:
            for scenario in ("never", "never-ignoring-term"):
                with self.subTest(shell=shell, scenario=scenario):
                    self.reset_job()
                    self.start_job(shell, scenario)
                    self.await_runner_started()
                    result, took = self.close()
                    self.assertEqual(result.returncode, 1, result)
                    self.assertIn(f"closed {self.JOB_ID} (result_events=0 exit=1)", result.stdout)
                    self.assertLess(took, 3, result)
                    self.assert_finished_cleanly()

    def reset_job(self):
        self.kill_job()
        self.driver = None
        for name in ("pid", "exit", "stderr", "stdout", "out.txt", "events.jsonl", "out.txt.events.jsonl",
                     "inbox.holder.pid", "inbox.ready", "inbox.fifo", "runner-inbox.fifo"):
            (self.job / name).unlink(missing_ok=True)
        (self.root / "started").unlink(missing_ok=True)

    def test_a_late_signal_may_miss_the_receipt_but_the_job_still_ends(self):
        # jobs close keeps its 11s for a holder PID that appears late. Whether the
        # receipt lands in the remainder is a race, so either answer is correct.
        self.start_job("/bin/bash", "never", delay=10.7)
        result, took = self.close()
        self.assertIn(result.returncode, (1, 124), result)
        if result.returncode == 1:
            self.assertIn("closed", result.stdout)
        self.assertLess(took, 12.5, result)
        self.assert_finished_cleanly()


FAKE_CLI = r'''#!/bin/bash
# Codex and Grok: answers the live-surface probe, then hangs before the inbox with
# a grandchild. Gemini: run-gemini.sh has already opened the inbox as this stdin, and
# the CLI runs until its EOF.
case "$*" in
  *--input-format*)
    sleep 300 &
    echo $! > "$PROBE_ROOT/grandchild"
    : > "$PROBE_ROOT/started"
    cat > /dev/null
    exit 0 ;;
  *app-server*|*agent*stdio*) ;;
  *) echo "fake 1.0"; exit 0 ;;
esac
count_file="$PROBE_ROOT/cli-runs"
runs=$(cat "$count_file" 2>/dev/null || echo 0)
echo $((runs + 1)) > "$count_file"
if [[ "$runs" -eq 0 ]]; then
  IFS= read -r _
  printf '{"id":1,"result":{}}\n'
  exec cat > /dev/null
fi
if [[ "${FAKE_IGNORE_TERM:-0}" == 1 ]]; then trap '' TERM; fi
sleep 300 &
echo $! > "$PROBE_ROOT/grandchild"
: > "$PROBE_ROOT/started"
while :; do wait; sleep 0.05; done
'''


class RealRunnerCleanupTests(unittest.TestCase):
    """A close before the inbox opens must let the real runner's EXIT cleanup run."""

    JOB_ID = "20261101-160000-12345-1"

    def run_job(self, vendor, ignore_term=False, mode="work", model="fake", effort="medium",
                hold_before_inbox=False):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-fifo-clean-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        env, violations = isolated_environment(root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists(), "offline guard triggered"))
        runtime = root / "runtime"
        shutil.copytree(ROOT / "scripts/lib", runtime / "scripts/lib")
        shutil.copytree(ROOT / "scripts/runners", runtime / "scripts/runners")
        shutil.copytree(ROOT / "config", runtime / "config")
        if hold_before_inbox:
            # run-gemini.sh opens the inbox moments after its cleanup trap is set;
            # hold it in that window so a close lands there every time.
            runner = runtime / f"scripts/runners/run-{vendor}.sh"
            mark = 'LIVE_INBOX="${OMNILANE_INBOX:-}"\n'
            source = runner.read_text()
            self.assertEqual(source.count(mark), 1)
            # exec keeps it a foreground child of the runner, under a PID we can check.
            hold = """/bin/bash -c 'echo $$ > "$PROBE_ROOT/held-sleep"; : > "$PROBE_ROOT/started"; exec sleep 300'\n"""
            runner.write_text(source.replace(mark, hold + mark))
        job = root / "home/jobs" / self.JOB_ID
        job.mkdir(parents=True)
        (job / "meta.json").write_text(
            f'{{"lane":"triage","vendor":"{vendor}","session_mode":"live"}}')
        workdir = root / "work"
        workdir.mkdir()
        cli = root / "fake-cli"
        cli.write_text(FAKE_CLI)
        cli.chmod(0o755)
        (Path(env["HOME"]) / ".gemini").mkdir(parents=True, exist_ok=True)
        prompt = root / "prompt.txt"
        prompt.write_text("offline runner cleanup fixture\n")
        env.update(PROBE_ROOT=str(root), OMNILANE_HOME=str(root / "home"),
                   OMNILANE_SESSION_MODE="live", OMNILANE_LIVE_REQUIRED="1",
                   OMNILANE_IDLE_TIMEOUT="0", OMNILANE_TIMEOUT="60", OMNILANE_JOB_SUPERVISED="1",
                   CODEX_BIN=str(cli), AGY_BIN=str(cli), GROK_BIN=str(cli),
                   FAKE_IGNORE_TERM="1" if ignore_term else "0")
        script = r"""
printf '%s\n' "$$" > "$JOB_DIR/pid"
perl "$WORKER_ROOT/scripts/lib/job-timeout.pl" --no-deadline /bin/bash \
  "$WORKER_ROOT/scripts/lib/job-worker.sh" "$VENDOR" "$MODE" "$WORK" "$MODEL" "$EFFORT" \
  "$PROMPT" "$JOB_DIR/out.txt" > "$JOB_DIR/stdout" 2> "$JOB_DIR/stderr"
printf '%s\n' "$?" > "$JOB_DIR/exit"
"""
        driver = subprocess.Popen(
            ["/bin/bash", "-c", script],
            env=dict(env, JOB_DIR=str(job), WORKER_ROOT=str(runtime), VENDOR=vendor, MODE=mode,
                     WORK=str(workdir), MODEL=model, EFFORT=effort, PROMPT=str(prompt)),
            start_new_session=True)

        def kill_everything():
            # Group first: killing the supervisor alone would orphan a sleep or cat
            # whose command line does not name this test's directory.
            groups = self.job_groups(root) | {driver.pid}
            for pid, pgid, command in self.live_processes():
                if str(root) in command:
                    groups.add(pgid)
            groups.discard(os.getpgrp())
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            for pid in self.recorded_pids(root):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            driver.wait(timeout=5)
            self.await_nothing_left(root, groups)
        self.addCleanup(kill_everything)
        return root, job, workdir, env, driver

    def close_when_started(self, root, job, env, driver, ready):
        deadline = time.monotonic() + 15
        while not (ready() and (job / "inbox.holder.pid").exists()):
            if driver.poll() is not None or time.monotonic() > deadline:
                self.fail("job never reached the open: " + (job / "stderr").read_text())
            time.sleep(0.01)
        self.job_groups(root).add(os.getpgid(int((job / "inbox.holder.pid").read_text())))
        time.sleep(0.1)
        began = time.monotonic()
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", self.JOB_ID],
                                env=env, capture_output=True, text=True, timeout=20)
        took = time.monotonic() - began
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("exit=1", result.stdout, result)
        driver.wait(timeout=12)
        return result, took

    @staticmethod
    def live_processes():
        listing = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,pgid=,stat=,command="],
                                 capture_output=True, text=True, check=True).stdout
        processes = []
        for row in listing.splitlines():
            pid, pgid, stat, command = (row.split(None, 3) + [""])[:4]
            if not stat.startswith("Z"):
                processes.append((int(pid), int(pgid), command))
        return processes

    def job_groups(self, root):
        return self.__dict__.setdefault("_job_groups", {}).setdefault(str(root), set())

    @staticmethod
    def recorded_pids(root):
        return {int(path.read_text()) for path in (root / "grandchild", root / "held-sleep")
                if path.exists()}

    def await_nothing_left(self, root, groups):
        pids = self.recorded_pids(root)
        deadline = time.monotonic() + 3
        while True:
            left = [row for row in self.live_processes()
                    if str(root) in row[2] or row[0] in pids or row[1] in groups]
            if not left:
                return
            self.assertLess(time.monotonic(), deadline, left)
            time.sleep(0.05)

    def assert_nothing_left(self, root):
        self.await_nothing_left(root, self.job_groups(root))

    def test_codex_temp_directory_is_removed(self):
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term):
                root, job, workdir, env, driver = self.run_job("codex", ignore_term)
                result, took = self.close_when_started(
                    root, job, env, driver, (root / "started").exists)
                self.assertLess(took, 4, result)
                self.assert_nothing_left(root)
                self.assertEqual(list((workdir / ".omnilane-tmp").iterdir())
                                 if (workdir / ".omnilane-tmp").exists() else [], [])

    def assert_gemini_cleanup(self, **options):
        root, job, workdir, env, driver = self.run_job("gemini", **options)

        def owned_files():
            return [name for _, _, names in os.walk(workdir) for name in names
                    if name in ("agent.md", ".omnilane-owned.json")]
        seen_while_running = []

        def prepared():
            seen_while_running[:] = owned_files()
            return (root / "started").exists() and bool(seen_while_running)
        result, took = self.close_when_started(root, job, env, driver, prepared)
        self.assertLess(took, 4, result)
        if options.get("hold_before_inbox"):
            self.assertTrue((root / "held-sleep").exists())
        self.assert_nothing_left(root)
        self.assertEqual(sorted(seen_while_running), [".omnilane-owned.json", "agent.md"])
        self.assertEqual(owned_files(), [], (job / "stderr").read_text())

    def test_gemini_workspace_policy_is_cleaned_up_on_close(self):
        self.assert_gemini_cleanup()

    def test_gemini_workspace_policy_is_cleaned_up_before_the_inbox_opens(self):
        self.assert_gemini_cleanup(hold_before_inbox=True)

    def test_grok_sysops_leaves_no_process(self):
        # Only sysops has a live Grok path, and it has no EXIT cleanup to protect.
        root, job, workdir, env, driver = self.run_job("grok", mode="sysops", model="grok-4.7", effort="-")
        result, took = self.close_when_started(root, job, env, driver, (root / "started").exists)
        self.assertLess(took, 4, result)
        self.assert_nothing_left(root)


if __name__ == "__main__":
    unittest.main()
