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
  exit-early) exit 7 ;;
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
                env=run_env, stdout=stdout, stderr=stderr, start_new_session=True)
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

    def assert_clean_end(self):
        stderr = self.stderr_path.read_text()
        self.assertGreaterEqual(self.process.returncode, 0, "worker died of a signal: " + stderr)
        self.assertNotIn("Interrupted system call", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertFalse((self.job / "inbox.ready").exists(), stderr)
        self.assertFalse((self.job / "runner-inbox.fifo").exists(), stderr)
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


if __name__ == "__main__":
    unittest.main()
