"""Ordinary threaded goals retain completion history through cleanup and close."""
import json
import shlex
import subprocess
import unittest

import test_goal_thread_output as threaded


class ThreadedGoalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = threaded.GoalThreadOutputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        case = self.fixture
        self.release = case.root / "release"
        self.addCleanup(self.finish_jobs)
        # Gate the existing fake rather than invent another provider double.
        # This orders dispatch < completion hook < terminal goal observation.
        fake = case.root / "bin/claude"
        implementation = fake.with_name("claude-fixture")
        fake.rename(implementation)
        case.env.update(FAKE_CLAUDE_RELEASE=str(self.release),
                        FAKE_CLAUDE_FIXTURE=str(implementation))
        fake.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                        'deadline=$((SECONDS + 20))\n'
                        'while [[ ! -f "$FAKE_CLAUDE_RELEASE" ]]; do\n'
                        '  [[ "$SECONDS" -lt "$deadline" ]] || exit 98\n'
                        '  sleep .02\n'
                        'done\nexec "$FAKE_CLAUDE_FIXTURE" "$@"\n')
        fake.chmod(0o755)

    def finish_jobs(self):
        # An early assertion must not remove HOME while a released worker is
        # still publishing. Already consumed completions need no second wait.
        case = self.fixture
        self.release.touch()
        for job in (case.home / "jobs").glob("*"):
            completion = case.home / "inbox" / (job.name + ".json")
            consumed = completion.parent / "consumed" / completion.name
            if not completion.exists() and not consumed.exists():
                case.wait_job(job.name)

    def test_threaded_goal_cleanup_continuation_and_budget_close(self):
        case = self.fixture
        opened = case.cli("goal", "open", "complete two threaded turns",
                          "--budget-jobs", "2", "--workdir", str(case.work))
        self.assertEqual(opened.returncode, 0, opened.stderr)
        goal_id = opened.stdout.strip()
        goal = case.home / "goals" / goal_id
        thread_path = case.home / "threads/lifecycle-fixture.json"
        session = None
        expected_records = {}
        job_ids = []

        def read_json(path):
            return json.loads(path.read_text())

        def check_counts(count, status="open"):
            budget = read_json(goal / "budget.json")
            self.assertEqual((budget["budget_jobs"], budget["spent_jobs"],
                              budget["reserved_jobs"], budget["fuse_trips"],
                              budget["status"]), (2, count, 0, 0, status))
            self.assertEqual(read_json(goal / "failures.json"), {})

        def check_history():
            actual = {path.name: read_json(path)
                      for path in (goal / "jobs").glob("job-*.json")}
            self.assertEqual(actual, expected_records)

        def consume():
            return subprocess.run(
                ["bash", str(threaded.ROOT / "hooks/report-completions.sh")],
                env=dict(case.env, CLAUDE_PROJECT_DIR=str(case.work)),
                input=json.dumps({"session_id": case.env["CLAUDE_CODE_SESSION_ID"]}),
                text=True, capture_output=True, timeout=15)

        check_counts(0)
        for turn in (1, 2):
            self.release.unlink(missing_ok=True)
            try:
                result = case.cli("goal", "dispatch", goal_id,
                                  "--thread", "lifecycle-fixture", *case.flags,
                                  "consult", f"ordinary lifecycle turn {turn}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, "^" + threaded.JOB_ID + "\n$")
                job_id = result.stdout.strip()
                self.assertNotIn(job_id, job_ids)
                job_ids.append(job_id)
                record_path = goal / "jobs" / f"job-{turn:08d}.json"
                pending = read_json(record_path)
                self.assertEqual((pending["job_id"], pending["state"], pending["exit"]),
                                 (job_id, "running", None))
                check_counts(turn)
            finally:
                self.release.touch()
            case.wait_job(job_id)
            state = read_json(thread_path)
            if session is None:
                session = state["session_id"]
            self.assertEqual((state["session_id"], state["turns"], state["last_job_id"]),
                             (session, turn, job_id))
            self.assertEqual(result.stderr, case.notice("lifecycle-fixture", turn, session))
            calls = case.calls.read_text().splitlines()
            self.assertEqual(len(calls), turn)
            argv = shlex.split(calls[-1])
            flag = "--session-id" if turn == 1 else "--resume"
            self.assertEqual(argv[argv.index(flag) + 1], session)
            self.assertNotIn("--resume" if turn == 1 else "--session-id", argv)

            inbox = case.home / "inbox" / (job_id + ".json")
            completion = read_json(inbox)
            self.assertEqual((completion["job_id"], completion["thread"],
                              completion["thread_turn"], completion["exit"]),
                             (job_id, "lifecycle-fixture", turn, 0))
            consumed = consume()
            self.assertEqual(consumed.returncode, 0, consumed.stderr)
            self.assertEqual(consumed.stdout.count("Omnilane completion:"), 1)
            self.assertIn(f"job={job_id}", consumed.stdout)
            self.assertIn(f"thread=lifecycle-fixture turn={turn} exit=0", consumed.stdout)
            self.assertFalse(inbox.exists())
            self.assertEqual(read_json(inbox.parent / "consumed" / inbox.name), completion)
            repeated = consume()
            self.assertEqual((repeated.returncode, repeated.stdout), (0, ""), repeated.stderr)
            # The hook consumed the completion while the goal still had only
            # its running record. Status must recover the real terminal exit.
            self.assertEqual(read_json(record_path), pending)
            status = case.cli("goal", "status", goal_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn(f"jobs: {turn} / 2\nreserved jobs: 0\n", status.stdout)
            terminal = read_json(record_path)
            self.assertEqual((terminal["state"], terminal["exit"],
                              terminal["failure_counted"]), ("done", 0, False))
            self.assertIs(type(terminal["seconds"]), int)
            self.assertGreaterEqual(terminal["seconds"], 0)
            self.assertRegex(terminal["finished"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            expected_records[record_path.name] = terminal
            check_counts(turn)
            check_history()

            if turn == 1:
                removed = case.cli("jobs", "rm", job_id)
                self.assertEqual(removed.returncode, 0, removed.stderr)
                self.assertFalse((case.home / "jobs" / job_id).exists())
                self.assertEqual(read_json(thread_path), state)
                expected_records[record_path.name] = dict(terminal, artifacts_missing=True)
                after_cleanup = case.cli("goal", "status", goal_id)
                self.assertEqual(after_cleanup.returncode, 0, after_cleanup.stderr)
                self.assertIn(f"job {job_id}: lane=consult vendor=claude exit=0 "
                              f"seconds={terminal['seconds']} artifacts=missing", after_cleanup.stdout)
                check_counts(1)
                check_history()

        journal = {path.name: path.read_bytes() for path in (goal / "dispatch").iterdir()}
        self.assertEqual(len(journal), 4)  # Two intents and their two claims.
        outcomes = [json.loads(value) for name, value in journal.items()
                    if name.endswith(".outcome.json")]
        self.assertEqual({(entry["kind"], entry["job_id"]) for entry in outcomes},
                         {("claim", job_id) for job_id in job_ids})
        calls_before = case.calls.read_bytes()
        thread_before = thread_path.read_bytes()
        diagnostics = sorted(path.name for path in goal.glob("dispatch-error-*.txt"))
        refused = case.cli("goal", "dispatch", goal_id, "--thread", "lifecycle-fixture",
                           *case.flags, "consult", "third lifecycle turn must not launch")
        self.assertEqual(refused.returncode, 75, refused.stderr)
        self.assertEqual(refused.stdout, "")
        self.assertIn("jobs budget exhausted: 2/2", refused.stderr)
        self.assertEqual(case.calls.read_bytes(), calls_before)
        self.assertEqual(thread_path.read_bytes(), thread_before)
        self.assertEqual({path.name: path.read_bytes() for path in (goal / "dispatch").iterdir()}, journal)
        self.assertEqual(sorted(path.name for path in goal.glob("dispatch-error-*.txt")), diagnostics)
        self.assertEqual([path.name for path in (case.home / "jobs").iterdir()], [job_ids[1]])
        check_counts(2)
        check_history()

        closed = case.cli("goal", "close", goal_id)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(closed.stdout, str(goal / "report.md") + "\n")
        report = (goal / "report.md").read_text()
        self.assertIn("- Status: closed", report)
        self.assertIn("- Jobs: 2 / 2", report)
        self.assertIn("- Fuse trips: 0", report)
        self.assertEqual(report.count("job "), 2)
        for turn, record in enumerate(expected_records.values(), 1):
            availability = " artifacts=missing" if turn == 1 else ""
            self.assertIn(f"job {record['job_id']}: lane=consult vendor=claude exit=0 "
                          f"seconds={record['seconds']}{availability} task=ordinary lifecycle turn {turn}", report)
        final = case.cli("goal", "status", goal_id)
        self.assertEqual(final.returncode, 0, final.stderr)
        self.assertIn("status: closed\njobs: 2 / 2\nreserved jobs: 0\n", final.stdout)
        check_counts(2, "closed")
        check_history()
        self.assertEqual(case.calls.read_bytes(), calls_before)
        self.assertEqual(thread_path.read_bytes(), thread_before)
        self.assertEqual({path.name: path.read_bytes() for path in (goal / "dispatch").iterdir()}, journal)


if __name__ == "__main__":
    unittest.main()
