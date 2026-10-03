"""Ordinary threaded goal output contracts, using only the fake Claude fixture."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/omnilane"
JOB_ID = r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+"


class GoalThreadOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-goal-thread-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.work = self.root / "work"
        bins = self.root / "bin"
        for directory in (self.home, self.work, bins):
            directory.mkdir()
        # Reuse the existing benign provider double rather than discovering a
        # provider on the host or maintaining a second implementation of it.
        source = (ROOT / "tests/test_thread_dispatch.sh").read_text()
        fake = source.split('cat > "$BIN_DIR/claude" <<\'EOF\'\n', 1)[1].split("\nEOF\n", 1)[0]
        (bins / "claude").write_text(fake + "\n")
        (bins / "claude").chmod(0o755)
        self.calls = self.root / "claude.argv"
        self.env.update(
            OMNILANE_HOME=str(self.home), PATH=str(bins) + os.pathsep + self.env["PATH"],
            FAKE_CLAUDE_ARGV_LOG=str(self.calls),
            FAKE_CLAUDE_RETURN_FILE=str(self.root / "return-session"),
            FAKE_CLAUDE_FAIL_FILE=str(self.root / "fail-resume"),
            CLAUDE_CODE_SESSION_ID="goal-thread-foreman",
        )
        (self.home / "routing.local.yaml").write_text("consult: claude fake-thread-model low\n")
        self.flags = ("--vendor", "claude", "--model", "fake-thread-model", "--effort", "low", "--single-shot")

    def add_dispatch_diagnostics(self, text="", fail_relay=False):
        # Emit benign fixture diagnostics in the captured dispatch phase.
        # Every real Python operation still uses the isolated interpreter.
        wrapper = self.root / "bin/python3"
        wrapper.write_text(
            f"#!{sys.executable}\nimport os, sys\n"
            "args = sys.argv[1:]\n"
            "if len(args) > 1 and args[0].endswith('/goal_dispatch.py') and args[1] == 'claim':\n"
            f"    sys.stderr.write({text!r})\n"
            f"if {fail_relay!r} and len(args) == 3 and args[0] == '-' and args[1].endswith('/meta.json'):\n"
            "    sys.exit(19)\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *args])\n")
        wrapper.chmod(0o755)

    def cli(self, *args, input=None):
        return subprocess.run([str(CLI), *args], input=input, env=self.env,
                              capture_output=True, text=True, timeout=20)

    def open_goal(self):
        result = self.cli("goal", "open", "ordinary threaded goal", "--workdir", str(self.work))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def wait_job(self, job_id):
        result = self.cli("jobs", "wait", job_id, "--timeout", "10")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "done exit=0\n")
        # exit precedes completion publication; don't remove fixture storage
        # while the detached worker still needs it for the inbox record.
        deadline = time.monotonic() + 10
        while not (self.home / "inbox" / (job_id + ".json")).exists():
            self.assertLess(time.monotonic(), deadline, "fake worker did not finish publication")
            time.sleep(.02)

    def notice(self, name, turn, session):
        mode = "new" if turn == 1 else "resume"
        return f"omnilane: thread {name} turn {turn} (claude session {session}, {mode})\n"

    def assert_call_count(self, count):
        self.assertEqual(len(self.calls.read_text().splitlines()), count)
        self.assertEqual(len(list((self.home / "jobs").iterdir())), count)

    def test_goal_first_turn_and_stdin_continuation_return_only_job_id(self):
        goal_id = self.open_goal()
        goal = self.home / "goals" / goal_id
        session = None
        for turn in (1, 2):
            task = "first threaded goal" if turn == 1 else "-"
            result = self.cli("goal", "dispatch", goal_id, "--thread", "goal-fixture",
                              *self.flags, "consult", task,
                              input="continue threaded goal" if turn == 2 else None)
            # Find the durable record even on the red baseline, where a real
            # worker launches successfully but the caller receives no job ID.
            paths = sorted((goal / "jobs").glob("job-*.json"))
            self.assertEqual(len(paths), turn)
            job_id = json.loads(paths[-1].read_text())["job_id"]
            self.wait_job(job_id)
            status = self.cli("goal", "status", goal_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            record = json.loads(paths[-1].read_text())
            self.assertEqual(record["exit"], 0)
            budget = json.loads((goal / "budget.json").read_text())
            self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (turn, 0))
            state = json.loads((self.home / "threads/goal-fixture.json").read_text())
            self.assertEqual(state["turns"], turn)
            self.assertEqual(state["last_job_id"], job_id)
            if session is None:
                session = state["session_id"]
            self.assertEqual(state["session_id"], session)
            self.assert_call_count(turn)
            args = self.calls.read_text().splitlines()[-1]
            self.assertIn(("--session-id " if turn == 1 else "--resume ") + session, args)
            with self.subTest(turn=turn):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, job_id + "\n")
                self.assertEqual(result.stderr, self.notice("goal-fixture", turn, session))
                # The normal executor diagnostic stays captured; only the
                # benign thread visibility line is replayed to the caller.
                captured = "".join(path.read_text() for path in goal.glob("dispatch-error-*.txt"))
                self.assertIn("omnilane: executor=cli reason=no-native-context\n", captured)
                self.assertIn(self.notice("goal-fixture", turn, session), captured)

    def test_direct_background_thread_keeps_notice_then_job_id_on_stdout(self):
        session = None
        for turn in (1, 2):
            result = self.cli("dispatch", "--background", "--workdir", str(self.work),
                              "--thread", "direct-fixture", *self.flags, "consult", f"direct turn {turn}")
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 2, result.stdout)
            self.assertRegex(lines[1], "^" + JOB_ID + "$")
            self.wait_job(lines[1])
            state = json.loads((self.home / "threads/direct-fixture.json").read_text())
            if session is None:
                session = state["session_id"]
            self.assertEqual(state["session_id"], session)
            self.assertEqual(state["turns"], turn)
            self.assertEqual(result.stdout, self.notice("direct-fixture", turn, session) + lines[1] + "\n")
            self.assertEqual(result.stderr, "omnilane: executor=cli reason=no-native-context\n")
            self.assert_call_count(turn)

    def test_ordinary_goal_keeps_one_id_and_no_success_stderr(self):
        goal_id = self.open_goal()
        result = self.cli("goal", "dispatch", goal_id, *self.flags, "consult", "ordinary goal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(re.fullmatch(JOB_ID + "\n", result.stdout), result.stdout)
        self.assertEqual(result.stderr, "")
        self.wait_job(result.stdout.strip())
        self.assert_call_count(1)
        self.assertFalse((self.home / "threads").exists())

    def test_goal_relays_only_notice_matching_its_verified_job_metadata(self):
        unrelated = (
            "fixture: ordinary progress detail\n"
            "omnilane: thread other-fixture turn 1 (claude session fixture-session, new)\n"
            "omnilane: thread goal-fixture turn 2 (claude session fixture-session, resume)\n"
            "omnilane: thread goal-fixture turn 1 (codex session pending, new)\n"
        )
        self.add_dispatch_diagnostics(unrelated)
        goal_id = self.open_goal()
        result = self.cli("goal", "dispatch", goal_id, "--thread", "goal-fixture",
                          *self.flags, "consult", "ordinary diagnostic visibility")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(re.fullmatch(JOB_ID + "\n", result.stdout), result.stdout)
        self.wait_job(result.stdout.strip())
        state = json.loads((self.home / "threads/goal-fixture.json").read_text())
        self.assertEqual(result.stderr, self.notice("goal-fixture", 1, state["session_id"]))
        captured = "".join(path.read_text() for path in (self.home / "goals" / goal_id).glob("dispatch-error-*.txt"))
        self.assertIn(unrelated, captured)
        self.assert_call_count(1)

    def test_goal_continuation_relays_supported_non_uuid_session_token(self):
        session = "fixture.Session:42"
        (self.root / "return-session").write_text(session)
        first = self.cli("dispatch", "--background", "--workdir", str(self.work),
                         "--thread", "token-fixture", *self.flags, "consult", "establish fixture session")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.wait_job(first.stdout.splitlines()[-1])
        goal_id = self.open_goal()
        result = self.cli("goal", "dispatch", goal_id, "--thread", "token-fixture",
                          *self.flags, "consult", "continue fixture session")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(re.fullmatch(JOB_ID + "\n", result.stdout), result.stdout)
        self.assertEqual(result.stderr, self.notice("token-fixture", 2, session))
        self.wait_job(result.stdout.strip())
        self.assert_call_count(2)

    def test_long_benign_diagnostics_keep_success_and_metadata_visibility(self):
        self.add_dispatch_diagnostics("fixture: ordinary progress detail\n" * 2200)
        goal_id = self.open_goal()
        result = self.cli("goal", "dispatch", goal_id, "--thread", "goal-fixture",
                          *self.flags, "consult", "long fixture progress")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(re.fullmatch(JOB_ID + "\n", result.stdout), result.stdout)
        self.assertEqual(result.stderr, "omnilane: thread goal-fixture turn 1 (claude)\n")
        self.wait_job(result.stdout.strip())
        self.assert_call_count(1)

    def test_notice_relay_failure_cannot_lose_successful_job_id(self):
        self.add_dispatch_diagnostics(fail_relay=True)
        goal_id = self.open_goal()
        result = self.cli("goal", "dispatch", goal_id, "--thread", "goal-fixture",
                          *self.flags, "consult", "best effort notice fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(re.fullmatch(JOB_ID + "\n", result.stdout), result.stdout)
        self.wait_job(result.stdout.strip())
        self.assert_call_count(1)
        refreshed = self.cli("goal", "status", goal_id)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        budget = json.loads((self.home / "goals" / goal_id / "budget.json").read_text())
        self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
