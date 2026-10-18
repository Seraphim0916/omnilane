"""Exercise live worker close ordering with offline, non-normalizing runners."""

import json
import fcntl
import os
from pathlib import Path
import shutil
import signal
import select
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
WORKER_SOURCE = ROOT / "scripts/lib/job-worker.sh"


class JobWorkerCloseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-worker-close-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.runtime = self.root / "runtime"
        shutil.copytree(ROOT / "scripts/lib", self.runtime / "scripts/lib")
        shutil.copyfile(WORKER_SOURCE, self.runtime / "scripts/lib/job-worker.sh")
        shutil.copytree(ROOT / "config", self.runtime / "config")
        runner = self.runtime / "scripts/runners/run-claude.sh"
        runner.parent.mkdir()
        # Publish vendor events but never normalize out.txt, even on FIFO EOF.
        # This models a runner stopped before it can commit its result output.
        runner.write_text("""#!/usr/bin/env bash
set -euo pipefail
{
  IFS= read -r _
  cat "$FIXTURE_EVENTS" >> "${6}.events.jsonl"
  while IFS= read -r _; do :; done
} < "$OMNILANE_INBOX"
""")
        runner.chmod(0o755)
        self.job = self.root / "job"
        self.job.mkdir()
        self.output = self.job / "out.txt"
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("offline close fixture\n")
        self.events = self.root / "events.jsonl"
        self.home = self.root / "omnilane-home"
        self.home.mkdir()
        self.env.update(
            OMNILANE_HOME=str(self.home),
            OMNILANE_SESSION_MODE="live",
            OMNILANE_LIVE_REQUIRED="1",
            OMNILANE_IDLE_TIMEOUT="1",
            FIXTURE_EVENTS=str(self.events),
            PYTHONDONTWRITEBYTECODE="1",
        )

    def close_worker(self, events, operator=False):
        self.events.write_text("".join(json.dumps(event) + "\n" for event in events))
        if operator:
            self.env["OMNILANE_IDLE_TIMEOUT"] = "0"
        process = subprocess.Popen(
            ["bash", str(self.runtime / "scripts/lib/job-worker.sh"),
             "claude", "work", str(self.root), "claude-opus-5", "high",
             str(self.prompt), str(self.output)],
            env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            if operator:
                deadline = time.monotonic() + 5
                while not (self.job / "inbox.ready").exists():
                    self.assertIsNone(process.poll(), "worker exited before mailbox readiness")
                    self.assertLess(time.monotonic(), deadline, "mailbox never became ready")
                    time.sleep(0.02)
                process.send_signal(signal.SIGUSR1)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
        self.assertEqual(stdout, "")
        self.assertFalse((self.job / "inbox.ready").exists())
        self.assertFalse((self.job / "inbox.fifo").exists())
        return process.returncode, stderr

    def timeout_worker(self, *, forwarded=False, events=None, output=True):
        self.env["OMNILANE_IDLE_TIMEOUT"] = "0"
        self.env["OMNILANE_TIMEOUT"] = "3"
        self.events.write_text("".join(json.dumps(event) + "\n" for event in (
            self.successful_results()[:1] if events is None else events
        )))
        self.env["FIXTURE_FORWARD"] = "1" if forwarded else "0"
        self.env["FIXTURE_OUTPUT"] = "1" if output else "0"
        runner = self.runtime / "scripts/runners/run-claude.sh"
        runner.write_text("""#!/usr/bin/env bash
set -euo pipefail
{
  IFS= read -r _
  cat "$FIXTURE_EVENTS" >> "${6}.events.jsonl"
  if [[ "$FIXTURE_OUTPUT" == 1 ]]; then printf 'earlier result\n' > "$6"; fi
  if [[ "$FIXTURE_FORWARD" == 1 ]]; then IFS= read -r _; fi
  exit 142
} < "$OMNILANE_INBOX"
""")
        process = subprocess.Popen(
            ["bash", str(self.runtime / "scripts/lib/job-worker.sh"), "claude", "work",
             str(self.root), "claude-opus-5", "high", str(self.prompt), str(self.output)],
            env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            if forwarded:
                deadline = time.monotonic() + 5
                while not (self.job / "inbox.ready").exists():
                    self.assertIsNone(process.poll(), "worker exited before readiness")
                    self.assertLess(time.monotonic(), deadline, "mailbox never ready")
                    time.sleep(0.02)
                with (self.job / "inbox.fifo").open("w") as inbox:
                    inbox.write('{"type":"user","message":{"role":"user","content":[]}}\n')
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
        self.assertEqual(stdout, "")
        self.assertFalse((self.job / "inbox.ready").exists())
        return process.returncode, stderr

    def test_call_timeout_after_completed_turn_succeeds(self):
        rc, stderr = self.timeout_worker()
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(),
                         "earlier result\n\nclosed by per-call timeout after 3s; last turn completed\n")

    def test_call_timeout_with_unanswered_forwarded_turn_stays_142(self):
        rc, stderr = self.timeout_worker(forwarded=True)
        self.assertEqual(rc, 142, stderr)
        self.assertEqual(self.output.read_text(), "earlier result\n")

    def test_call_timeout_does_not_count_normalized_result(self):
        event = dict(self.successful_results()[0], normalized_by="fixture")
        rc, stderr = self.timeout_worker(events=[event])
        self.assertEqual(rc, 142, stderr)

    def test_call_timeout_recovers_empty_claude_output(self):
        rc, stderr = self.timeout_worker(output=False)
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(self.output.read_text().startswith("earlier result\n"))

    def test_call_timeout_requires_usable_output(self):
        event = {"type": "result", "is_error": False}
        rc, stderr = self.timeout_worker(events=[event], output=False)
        self.assertEqual(rc, 142, stderr)
        self.assertFalse(self.output.exists())

    def review_timeout(self, *, vendor="claude", second=None, duplicate=False,
                       pending=False, partial=False, forward_failure=False,
                       runner_rc=142, real_runner=False, unset_timeout=False,
                       commit_output=True):
        """Rendezvous at mailbox publication, never a wall-clock race.

        The copied worker's test-only seam publishes readiness on a pipe. For
        pending input it stops BEFORE the loop and reaps the controlled runner
        on continuation, so the first kill -0 observes the runner's actual exit.
        No classification/forwarding code is replaced by this seam.
        """
        self.env.update(OMNILANE_IDLE_TIMEOUT="0", OMNILANE_TIMEOUT="30")
        if pending and not partial:
            jobs = self.home / "jobs"
            jobs.mkdir()
            self.job = self.job.rename(jobs / "20261018-000000-1234-1")
            self.output = self.job / "out.txt"
            (self.job / "meta.json").write_text(
                '{"lane":"fixture","vendor":"claude","session_mode":"live"}\n')
        if unset_timeout:
            self.env.pop("OMNILANE_TIMEOUT")
        descriptors = []
        for _ in range(2):
            pair = os.pipe()
            high = tuple(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10) for fd in pair)
            for fd in pair:
                os.close(fd)
            descriptors.extend(high)
        notice_r, notice_w, control_r, control_w = descriptors
        self.addCleanup(lambda: [os.close(fd) for fd in descriptors])
        self.env.update(FIXTURE_NOTICE_FD=str(notice_w),
                        FIXTURE_CONTROL_FD=str(control_r),
                        FIXTURE_VENDOR=vendor,
                        FIXTURE_SECOND=json.dumps(second),
                        FIXTURE_DUPLICATE=str(int(duplicate)),
                        FIXTURE_FORWARD_FAILURE=str(int(forward_failure)),
                        FIXTURE_PENDING=str(int(pending)),
                        FIXTURE_RC=str(runner_rc),
                        FIXTURE_COMMIT=str(int(commit_output)),
                        FIXTURE_REAL=str(int(real_runner)))
        worker = self.runtime / "scripts/lib/job-worker.sh"
        source = worker.read_text()
        boundary = '(umask 077; : > "$READY_FILE")'
        self.assertEqual(source.count(boundary), 1)
        seam = '\nprintf "ready\\n" >&"$FIXTURE_NOTICE_FD"\n'
        if pending:
            seam += 'kill -STOP "$$"\nset +e\nwait "$runner_pid"\nset -e\n'
        worker.write_text(source.replace(boundary, boundary + seam))
        runner = self.runtime / f"scripts/runners/run-{vendor}.sh"
        fixture = self.root / "controlled-runner"
        fixture.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
vendor = os.environ["FIXTURE_VENDOR"]
real = os.environ["FIXTURE_REAL"] == "1"
notice = int(os.environ["FIXTURE_NOTICE_FD"])
control = int(os.environ["FIXTURE_CONTROL_FD"])
def result(text):
    if vendor == "claude":
        return {"type":"result", "is_error":False, "result":text}
    return {"event":"result", "result":{"status":"SUCCESS", "response":text}}
output = None if real else Path(sys.argv[6])
def emit(event):
    line = json.dumps(event) + "\\n"
    if real:
        print(line, end="", flush=True)
    else:
        with Path(str(output) + ".events.jsonl").open("a") as stream:
            stream.write(line)
inbox = sys.stdin if real else open(os.environ["OMNILANE_INBOX"])
assert inbox.readline()
emit(result("turn-1"))
if os.environ["FIXTURE_DUPLICATE"] == "1":
    emit(result("turn-1-duplicate"))
os.write(notice, b"first\\n")
if os.environ["FIXTURE_FORWARD_FAILURE"] == "1":
    inbox.close()
    os.write(notice, b"closed\\n")
    assert os.read(control, 1)
elif os.environ["FIXTURE_PENDING"] == "1":
    assert os.read(control, 1)
else:
    assert inbox.readline()
    event = json.loads(os.environ["FIXTURE_SECOND"])
    if event is not None:
        emit(event)
if not real and os.environ["FIXTURE_COMMIT"] == "1":
    # Match real runners: commit output only after the CLI's final event.
    latest = "turn-1"
    event = json.loads(os.environ["FIXTURE_SECOND"])
    if event is not None:
        text = event.get("result") if vendor == "claude" else event.get("result", {}).get("response")
        if isinstance(text, str):
            latest = text
    output.write_text(latest + "\\n")
sys.exit(int(os.environ["FIXTURE_RC"]))
''')
        fixture.chmod(0o755)
        if real_runner:
            shutil.copyfile(ROOT / f"scripts/runners/run-{vendor}.sh", runner)
            (Path(self.env["HOME"]) / ".gemini").mkdir(exist_ok=True)
            self.env.update(CLAUDE_BIN=str(fixture), AGY_BIN=str(fixture),
                            OMNILANE_JOB_SUPERVISED="1")
        else:
            shutil.copyfile(fixture, runner)
        runner.chmod(0o755)
        process = subprocess.Popen(
            ["bash", str(worker), vendor, "sysops", str(self.root),
             f"{vendor}-default", "-", str(self.prompt), str(self.output)],
            env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(notice_w, control_r), start_new_session=True)
        observed = b""
        def await_notice(expected):
            nonlocal observed
            while expected not in observed.splitlines():
                self.assertTrue(select.select([notice_r], [], [], 15)[0],
                                f"missing rendezvous {expected!r}; got {observed!r}")
                observed += os.read(notice_r, 4096)
        prefix = b""
        payload = '{"type":"user","message":{"role":"user","content":[]}}'
        try:
            await_notice(b"ready")
            await_notice(b"first")
            if real_runner:
                self.assertFalse(self.output.exists(), "runner committed turn 1 before CLI exit")
            if pending:
                _, status = os.waitpid(process.pid, os.WUNTRACED)
                self.assertTrue(os.WIFSTOPPED(status))
            if forward_failure:
                await_notice(b"closed")
            if pending and not partial:
                sent = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), "send",
                                       self.job.name, "accepted-follow-up"],
                                      env=self.env, text=True, capture_output=True, timeout=10)
                self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)
                self.assertEqual(sent.stdout, f"sent {self.job.name}\n")
                payload = json.dumps({"type":"user", "omnilane_job_id":self.job.name,
                    "foreman_session":"", "message":{"role":"user", "content":[
                    {"type":"text", "text":"accepted-follow-up"}]}}, separators=(",", ":"))
            else:
                with (self.job / "inbox.fifo").open("w") as inbox:
                    inbox.write(payload + ("" if partial else "\n"))
            if forward_failure:
                # Do not let the runner exit until the REAL printf got EPIPE.
                while b"Broken pipe" not in prefix:
                    self.assertTrue(select.select([process.stderr], [], [], 15)[0],
                                    f"worker never observed EPIPE: {prefix!r}")
                    chunk = os.read(process.stderr.fileno(), 4096)
                    self.assertTrue(chunk, f"worker exited before EPIPE: {prefix!r}")
                    prefix += chunk
            if pending or forward_failure:
                os.write(control_w, b"x")
            if pending:
                process.send_signal(signal.SIGCONT)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
        if forward_failure:
            self.assertEqual(process.returncode, runner_rc or 1, prefix.decode() + stderr)
        self.assertEqual(stdout, "", prefix.decode() + stderr)
        self.assertFalse((self.job / "inbox.ready").exists())
        return process.returncode, prefix.decode() + stderr, payload

    def test_review_pending_inbox_stays_142_and_is_preserved(self):
        rc, stderr, payload = self.review_timeout(pending=True)
        self.assertEqual(rc, 142, stderr)
        self.assertEqual((self.job / "close-pending.jsonl").read_text(), payload + "\n")

    def test_review_partial_pending_inbox_is_preserved(self):
        rc, stderr, payload = self.review_timeout(pending=True, partial=True)
        self.assertEqual(rc, 142, stderr)
        self.assertEqual((self.job / "close-pending.jsonl").read_text(), payload)

    def test_review_forward_epipe_keeps_runner_142(self):
        rc, stderr, payload = self.review_timeout(forward_failure=True)
        self.assertEqual(rc, 142, stderr)
        self.assertIn("follow-up could not be forwarded", (self.job / "mode-notice.txt").read_text())
        self.assertEqual((self.job / "close-pending.jsonl").read_text(), payload + "\n")

    def test_review_forward_epipe_cannot_report_runner_zero(self):
        rc, stderr, _ = self.review_timeout(forward_failure=True, runner_rc=0)
        self.assertEqual(rc, 1, stderr)

    def test_review_duplicate_success_then_activity_stays_142(self):
        rc, stderr, _ = self.review_timeout(vendor="gemini", duplicate=True,
            second={"event":"step_update", "step":{"text":"incomplete"}})
        self.assertEqual(rc, 142, stderr)

    def test_review_claude_duplicate_success_then_assistant_stays_142(self):
        rc, stderr, _ = self.review_timeout(duplicate=True,
            second={"type":"assistant", "message":{"content":[{"type":"text", "text":"partial"}]}})
        self.assertEqual(rc, 142, stderr)

    def test_review_duplicate_success_then_tool_stays_142(self):
        rc, stderr, _ = self.review_timeout(duplicate=True,
            second={"type":"tool_result", "content":"still working"})
        self.assertEqual(rc, 142, stderr)

    def test_review_duplicate_success_without_second_turn_result_stays_142(self):
        rc, stderr, _ = self.review_timeout(duplicate=True)
        self.assertEqual(rc, 142, stderr)

    def test_review_claude_two_completed_turns_use_last_output(self):
        rc, stderr, _ = self.review_timeout(real_runner=True,
            second={"type":"result", "is_error":False, "result":"turn-2"})
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "turn-2\n\nclosed by per-call timeout after 30s; last turn completed\n")

    def test_review_gemini_two_completed_turns_use_last_output(self):
        rc, stderr, _ = self.review_timeout(vendor="gemini", real_runner=True,
            second={"event":"result", "result":{"status":"SUCCESS", "response":"turn-2"}})
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "turn-2\n\nclosed by per-call timeout after 30s; last turn completed\n")

    def test_review_claude_second_turn_incomplete_stays_142(self):
        rc, stderr, _ = self.review_timeout(second={"type":"assistant", "message":{"content":[]}})
        self.assertEqual(rc, 142, stderr)

    def test_review_gemini_second_turn_incomplete_stays_142(self):
        rc, stderr, _ = self.review_timeout(vendor="gemini",
            second={"event":"step_update", "step":{"text":"incomplete"}})
        self.assertEqual(rc, 142, stderr)

    def test_review_claude_second_turn_recovers_output(self):
        rc, stderr, _ = self.review_timeout(commit_output=False,
            second={"type":"result", "is_error":False, "result":"turn-2"})
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "turn-2\n\nclosed by per-call timeout after 30s; last turn completed\n")

    def test_review_claude_second_turn_error_stays_142(self):
        rc, stderr, _ = self.review_timeout(second={"type":"result", "is_error":True, "result":"failed"})
        self.assertEqual(rc, 142, stderr)

    def test_review_claude_second_empty_success_replaces_first_output(self):
        rc, stderr, _ = self.review_timeout(real_runner=True,
            second={"type":"result", "is_error":False, "result":""})
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "\n\nclosed by per-call timeout after 30s; last turn completed\n")

    def test_review_gemini_second_empty_success_replaces_first_output(self):
        rc, stderr, _ = self.review_timeout(vendor="gemini", real_runner=True,
            second={"event":"result", "result":{"status":"SUCCESS", "response":""}})
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "\n\nclosed by per-call timeout after 30s; last turn completed\n")

    def test_review_claude_second_success_without_text_stays_142(self):
        rc, stderr, _ = self.review_timeout(real_runner=True,
            second={"type":"result", "is_error":False})
        self.assertEqual(rc, 142, stderr)

    def test_review_gemini_second_success_without_text_stays_142(self):
        rc, stderr, _ = self.review_timeout(vendor="gemini", real_runner=True,
            second={"event":"result", "result":{"status":"SUCCESS"}})
        self.assertEqual(rc, 142, stderr)

    def test_review_unset_timeout_does_not_abort(self):
        rc, stderr, _ = self.review_timeout(unset_timeout=True,
            second={"type":"result", "is_error":False, "result":"turn-2"})
        self.assertEqual(rc, 0, stderr)
        self.assertNotIn("unbound variable", stderr)
        self.assertIn("after unknowns; last turn completed", self.output.read_text())

    def test_review_warning_fixture_has_no_unused_zero_dispatch(self):
        source = (ROOT / "tests/test_live_mailbox.sh").read_text()
        self.assertFalse("call-timeout-warnings-zero)" in source, "unused zero-warning dispatch remains")

    def test_review_warning_fixture_quotes_idle_array(self):
        source = (ROOT / "tests/test_live_mailbox.sh").read_text()
        self.assertTrue('for idle in "${idles[@]}"; do' in source, "idle loop must quote its array")

    @staticmethod
    def successful_results():
        return [
            {"type": "result", "subtype": "success", "is_error": False, "result": text}
            for text in ("earlier result", "completed vendor result")
        ]

    def test_idle_cap_recovers_prior_success_result(self):
        rc, stderr = self.close_worker(self.successful_results())
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(),
                         "completed vendor result\n\nclosed by idle cap after 1s\n")

    def test_idle_cap_without_result_fails(self):
        rc, stderr = self.close_worker([])
        self.assertNotEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "\nclosed by idle cap after 1s\n")

    def test_idle_cap_unrecoverable_success_fails(self):
        rc, stderr = self.close_worker([
            {"type": "result", "subtype": "success", "is_error": False},
        ])
        self.assertNotEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "\nclosed by idle cap after 1s\n")

    def test_idle_cap_incomplete_turn_does_not_synthesize_result(self):
        rc, stderr = self.close_worker([
            {"type": "assistant", "message": {
                "content": [{"type": "text", "text": "unfinished turn"}],
            }},
        ])
        self.assertNotEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "\nclosed by idle cap after 1s\n")
        self.assertNotIn("normalized_by", (self.job / "events.jsonl").read_text())

    def test_operator_close_recovers_prior_success_result(self):
        rc, stderr = self.close_worker(self.successful_results(), operator=True)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(self.output.read_text(), "completed vendor result\n")

    def test_operator_close_without_result_fails(self):
        rc, stderr = self.close_worker([], operator=True)
        self.assertNotEqual(rc, 0, stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
