"""Exercise live worker close ordering with offline, non-normalizing runners."""

import json
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
