import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import goal_dispatch as gd


GOAL = "20261001-000000-1-1"
JOB = "20261001-000001-2-3"
OTHER_JOB = "20261001-000002-4-5"
FINGERPRINT = "a" * 64


class JournalFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="goal-dispatch-cov-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.goal = self.home / "goals" / GOAL
        self.jobs = self.goal / "jobs"
        self.jobs.mkdir(parents=True)
        self.dispatch = self.goal / "dispatch"
        self.write_budget()

    def write_budget(self, **changes):
        value = {"status": "open", "budget_jobs": None, "budget_seconds": None, "spent_jobs": 0,
                 "spent_seconds": 0, "fuse_trips": 0, "started_epoch": 1, "workdir": "/srv/goal"}
        value.update(changes)
        (self.goal / "budget.json").write_text(json.dumps(value))

    def main(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = gd.main([args[0], str(self.home), *args[1:]])
        return code, stdout.getvalue().strip()

    def prepare(self, lane="probe", task="offline task"):
        code, ref = self.main("prepare", GOAL, lane, FINGERPRINT, task)
        self.assertEqual(code, 0)
        return ref

    def intent_name(self, ref):
        return ref.split("/")[2]

    def reconcile(self):
        return gd.reconcile(str(self.home), GOAL)

    def write_intent(self, number, suffix="b" * 32, **changes):
        name = f"intent-{number:08d}-{suffix}"
        value = {"schema_version": 1, "intent_id": name, "goal_id": GOAL, "ordinal": number,
                 "lane": "probe", "task": "t", "fingerprint": FINGERPRINT, "workdir": "/srv/goal",
                 "submitted_epoch": 1, "submitted": "1970-01-01T00:00:01Z"}
        value.update(changes)
        self.dispatch.mkdir(exist_ok=True)
        (self.dispatch / (name + ".json")).write_text(json.dumps(value))
        return name

    def write_outcome(self, name, **changes):
        value = {"schema_version": 1, "intent_id": name, "goal_id": GOAL, "kind": "claim",
                 "job_id": JOB}
        value.update(changes)
        (self.dispatch / (name + ".outcome.json")).write_text(json.dumps(value))

    def write_record(self, ordinal, job_id=JOB, name=None, **changes):
        value = {"ordinal": ordinal, "job_id": job_id}
        value.update(changes)
        (self.jobs / (name or f"job-{ordinal:08d}.json")).write_text(json.dumps(value))

    def job_dir(self, job_id=JOB):
        path = self.home / "jobs" / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path


class LowLevelTests(JournalFixture):
    def test_store_refuses_a_malformed_goal_id_before_opening_anything(self):
        for goal_id in ("", "../x", "20261001-000000-1", GOAL + "\n"):
            with self.assertRaisesRegex(ValueError, "invalid goal journal goal id"):
                gd.Store(str(self.home), goal_id)

    def test_store_closes_opened_directories_when_a_later_open_fails(self):
        opened = []
        real_open = os.open

        def recording_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        self.jobs.rmdir()
        with mock.patch.object(gd.os, "open", side_effect=recording_open), \
                self.assertRaises(FileNotFoundError):
            gd.Store(str(self.home), GOAL)
        self.assertEqual(len(opened), 3)
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_directory_owned_by_someone_else_is_refused(self):
        with mock.patch.object(gd.os, "geteuid", return_value=os.geteuid() + 1), \
                self.assertRaisesRegex(ValueError, "different owner"):
            gd.Store(str(self.home), GOAL)

    def test_directory_create_is_idempotent(self):
        parent = os.open(str(self.goal), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent)
        for _ in range(2):
            os.close(gd.directory(parent, "dispatch", create=True))
        self.assertTrue(self.dispatch.is_dir())

    def test_read_bytes_refuses_fifo_symlink_and_oversized_evidence(self):
        fd = os.open(str(self.goal), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        os.mkfifo(self.goal / "fifo")
        with self.assertRaisesRegex(ValueError, "unsafe goal journal evidence: fifo"):
            gd.read_bytes(fd, "fifo")
        (self.goal / "link").symlink_to(self.goal / "budget.json")
        with self.assertRaises(OSError):
            gd.read_bytes(fd, "link")
        (self.goal / "big").write_bytes(b"x" * 33)
        with self.assertRaisesRegex(ValueError, "oversized goal journal evidence: big"):
            gd.read_bytes(fd, "big", limit=32)
        self.assertEqual(gd.read_bytes(fd, "big", limit=33), b"x" * 33)

    def test_read_bytes_bounds_a_file_that_grows_after_fstat(self):
        fd = os.open(str(self.goal), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        (self.goal / "growing").write_bytes(b"x" * 8)
        with mock.patch.object(gd.os, "read", return_value=b"x" * 9), \
                self.assertRaisesRegex(ValueError, "oversized goal journal evidence: growing"):
            gd.read_bytes(fd, "growing", limit=8)

    def test_read_json_requires_an_object(self):
        fd = os.open(str(self.goal), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        (self.goal / "list.json").write_text("[1]")
        with self.assertRaisesRegex(ValueError, "invalid goal journal object: list.json"):
            gd.read_json(fd, "list.json")

    def test_publish_never_overwrites_and_never_leaves_a_temporary(self):
        fd = os.open(str(self.goal), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        gd.publish(fd, "slot.json", {"a": 1})
        with self.assertRaises(FileExistsError):
            gd.publish(fd, "slot.json", {"a": 2})
        self.assertEqual(json.loads((self.goal / "slot.json").read_text()), {"a": 1})
        with self.assertRaisesRegex(ValueError, "oversized goal journal record"):
            gd.publish(fd, "big.json", {"argv": "\x01" * (gd.MAX_RECORD_BYTES // 6 + 1)})
        self.assertFalse((self.goal / "big.json").exists())
        self.assertEqual(sorted(p.name for p in self.goal.iterdir() if p.name.startswith(".journal-")), [])

    def test_reference_requires_goal_dispatch_intent_shape(self):
        name = "intent-00000001-" + "c" * 32
        self.assertEqual(gd.reference(f"{GOAL}/dispatch/{name}"), (GOAL, name))
        for ref in (name, f"{GOAL}/jobs/{name}", f"{GOAL}/dispatch/intent-1-" + "c" * 32,
                    f"{GOAL}/dispatch/{name}/x"):
            with self.assertRaisesRegex(ValueError, "invalid goal dispatch intent reference"):
                gd.reference(ref)


class CommandTests(JournalFixture):
    def test_prepare_publishes_a_bounded_intent_and_blocks_a_second_reservation(self):
        ref = self.prepare(task="x" * 2500)
        intent = json.loads((self.dispatch / (self.intent_name(ref) + ".json")).read_text())
        self.assertEqual(intent["ordinal"], 1)
        self.assertEqual(len(intent["task"]), 2000)
        self.assertEqual(intent["workdir"], "/srv/goal")
        self.assertRegex(intent["submitted"], r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
        self.assertEqual(self.reconcile(), (0, 1, 1))
        with self.assertRaisesRegex(ValueError, "unresolved dispatch reservations or is closed"):
            self.prepare()

    def test_prepare_refuses_a_closed_goal_and_an_exhausted_budget(self):
        self.write_budget(status="closed")
        with self.assertRaisesRegex(ValueError, "unresolved dispatch reservations or is closed"):
            self.prepare()
        self.write_budget(budget_jobs=1)
        self.write_record(1)
        with self.assertRaisesRegex(ValueError, "goal jobs budget exhausted"):
            self.prepare()
        self.assertFalse(self.dispatch.exists())
        self.write_budget(budget_jobs=2)
        self.assertTrue(self.intent_name(self.prepare()).startswith("intent-00000002-"))

    def test_prepare_validates_the_budget_first(self):
        self.write_budget(workdir="relative")
        with self.assertRaisesRegex(ValueError, "invalid goal budget: workdir"):
            self.prepare()

    def test_claim_refuses_mismatched_task_and_malformed_job_id(self):
        ref = self.prepare()
        with self.assertRaisesRegex(ValueError, "dispatch task does not match its intent"):
            self.main("claim", ref, JOB, "other-lane", FINGERPRINT)
        with self.assertRaisesRegex(ValueError, "dispatch task does not match its intent"):
            self.main("claim", ref, JOB, "probe", "b" * 64)
        with self.assertRaisesRegex(ValueError, "invalid claimed job id"):
            self.main("claim", ref, "not-a-job", "probe", FINGERPRINT)
        self.assertFalse((self.dispatch / (self.intent_name(ref) + ".outcome.json")).exists())

    def test_a_second_claim_even_for_the_same_job_can_never_launch(self):
        ref = self.prepare()
        self.assertEqual(self.main("claim", ref, JOB, "probe", FINGERPRINT), (0, ""))
        with self.assertRaises(FileExistsError):
            self.main("claim", ref, JOB, "probe", FINGERPRINT)

    def test_verify_matches_only_the_claimed_job(self):
        ref = self.prepare()
        with self.assertRaisesRegex(ValueError, "does not match its claimed job id"):
            self.main("verify", ref, JOB)
        self.main("claim", ref, JOB, "probe", FINGERPRINT)
        self.assertEqual(self.main("verify", ref, JOB), (0, ""))
        with self.assertRaisesRegex(ValueError, "does not match its claimed job id"):
            self.main("verify", ref, OTHER_JOB)

    def test_abort_after_claim_keeps_the_reservation_and_returns_75(self):
        ref = self.prepare()
        self.main("claim", ref, JOB, "probe", FINGERPRINT)
        self.assertEqual(self.main("abort", ref), (75, ""))
        self.assertEqual(self.reconcile(), (0, 1, 1))

    def test_abort_is_idempotent_and_releases_the_reservation(self):
        ref = self.prepare()
        self.assertEqual(self.main("abort", ref), (0, ""))
        self.assertEqual(self.main("abort", ref), (0, ""))
        self.assertEqual(self.reconcile(), (0, 0, 1))
        with self.assertRaisesRegex(ValueError, "unexpected abort arguments"):
            self.main("abort", ref, "extra")
        self.assertTrue(self.intent_name(self.prepare()).startswith("intent-00000002-"))

    def test_claim_without_a_journal_and_unknown_operations_are_refused(self):
        ref = f"{GOAL}/dispatch/intent-00000001-" + "d" * 32
        with self.assertRaisesRegex(ValueError, "invalid goal dispatch intent reference"):
            self.main("claim", ref, JOB, "probe", FINGERPRINT)
        with self.assertRaisesRegex(ValueError, "unknown private goal journal operation"):
            self.main("rewrite", GOAL)

    def test_script_entry_reports_refusals_on_stderr_with_exit_1(self):
        stderr = io.StringIO()
        argv = [gd.__file__, "abort", str(self.home), "not/a/ref"]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as caught:
            runpy.run_path(gd.__file__, run_name="__main__")
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(stderr.getvalue(),
                         "omnilane goal journal: invalid goal dispatch intent reference\n")


class StartEvidenceTests(JournalFixture):
    def claimed(self):
        ref = self.prepare()
        self.main("claim", ref, JOB, "probe", FINGERPRINT)
        return ref

    def test_claim_with_pid_is_recorded_as_an_unknown_job(self):
        ref = self.claimed()
        self.assertEqual(self.reconcile(), (0, 1, 1))
        (self.job_dir() / "pid").write_text("4242\n")
        self.assertEqual(self.reconcile(), (1, 0, 1))
        record = json.loads((self.jobs / "job-00000001.json").read_text())
        self.assertEqual(record["intent_id"], self.intent_name(ref))
        self.assertEqual((record["job_id"], record["state"], record["vendor"], record["exit"]),
                         (JOB, "unknown", "unknown", None))
        self.assertEqual(self.reconcile(), (1, 0, 1))

    def test_exit_status_is_start_evidence_within_0_to_255(self):
        self.claimed()
        exit_path = self.job_dir() / "exit"
        exit_path.write_text("999\n")
        with self.assertRaisesRegex(ValueError, "invalid goal dispatch start evidence: exit"):
            self.reconcile()
        exit_path.write_text("255")
        self.assertEqual(self.reconcile(), (1, 0, 1))

    def test_malformed_or_hardlinked_pid_is_refused(self):
        self.claimed()
        pid = self.job_dir() / "pid"
        for text in ("0", "-1", "12345678901", "12 ", "abc"):
            pid.write_text(text)
            with self.assertRaisesRegex(ValueError, "invalid goal dispatch start evidence: pid"):
                self.reconcile()
        pid.write_text("7")
        os.link(pid, self.home / "pid-alias")
        with self.assertRaisesRegex(ValueError, "unsafe goal journal evidence: pid"):
            self.reconcile()

    def test_missing_jobs_store_or_job_dir_keeps_the_reservation(self):
        self.claimed()
        self.assertFalse((self.home / "jobs").exists())
        self.assertEqual(self.reconcile(), (0, 1, 1))
        (self.home / "jobs").mkdir()
        self.assertEqual(self.reconcile(), (0, 1, 1))
        self.job_dir()
        self.assertEqual(self.reconcile(), (0, 1, 1))


class ReconcileRefusalTests(JournalFixture):
    def assert_refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            self.reconcile()

    def test_job_store_names_are_strict_but_temporaries_are_ignored(self):
        (self.jobs / ".hidden").write_text("")
        (self.jobs / "job-00000001.json.tmp.123").write_text("")
        self.assertEqual(self.reconcile(), (0, 0, 0))
        (self.jobs / "notes.txt").write_text("")
        self.assert_refused("unexpected goal job record: notes.txt")

    def test_conflicting_job_records_are_refused(self):
        self.write_record(2, name="job-00000001.json")
        self.assert_refused("conflicting goal job record: job-00000001.json")
        (self.jobs / "job-00000001.json").unlink()
        self.write_record(1, job_id="bad")
        self.assert_refused("conflicting goal job record")
        self.write_record(1)
        self.write_record(2)
        self.assert_refused("conflicting goal job record: job-00000002.json")
        (self.jobs / "job-00000002.json").unlink()
        self.write_record(1, name="job-000000001.json")
        self.assert_refused("conflicting goal job record: job-000000001.json")

    def test_record_naming_an_intent_needs_that_intent(self):
        self.write_record(1, intent_id="intent-00000001-" + "e" * 32)
        self.assert_refused("goal job record is missing its dispatch intent")

    def test_dispatch_directory_names_are_strict_but_interrupted_writes_are_ignored(self):
        self.dispatch.mkdir()
        (self.dispatch / ".journal-abc.tmp").write_text("{")
        self.assertEqual(self.reconcile(), (0, 0, 0))
        (self.dispatch / "notes.json").write_text("{}")
        self.assert_refused("unexpected goal dispatch evidence: notes.json")

    def test_intent_schema_is_exact(self):
        name = "intent-00000001-" + "b" * 32
        for changes in ({"schema_version": 2}, {"schema_version": True}, {"goal_id": "other"},
                        {"intent_id": "intent-00000001-" + "f" * 32}, {"ordinal": 2},
                        {"submitted_epoch": -1}, {"lane": 7}, {"fingerprint": "A" * 64},
                        {"extra": 1}):
            self.write_intent(1, **changes)
            self.assert_refused("invalid goal dispatch intent: " + name)
        self.write_intent(0, ordinal=0, intent_id="intent-00000000-" + "b" * 32)
        self.assert_refused("invalid goal dispatch intent: intent-00000000-")

    def test_outcome_schema_is_exact(self):
        name = self.write_intent(1)
        for changes in ({"kind": "maybe"}, {"job_id": "bad"}, {"goal_id": "other"},
                        {"schema_version": 1.0}, {"intent_id": "x"}):
            self.write_outcome(name, **changes)
            self.assert_refused("invalid goal dispatch outcome: " + name)
        value = {"schema_version": 1, "intent_id": name, "goal_id": GOAL, "kind": "no-launch",
                 "job_id": JOB}
        (self.dispatch / (name + ".outcome.json")).write_text(json.dumps(value))
        self.assert_refused("invalid goal dispatch outcome")

    def test_two_intents_for_one_ordinal_or_one_job_are_refused(self):
        self.write_intent(1)
        self.write_intent(1, suffix="c" * 32)
        self.assert_refused("conflicting goal dispatch ordinal")
        for path in self.dispatch.iterdir():
            path.unlink()
        first, second = self.write_intent(1), self.write_intent(2, suffix="c" * 32)
        self.write_outcome(first)
        self.write_outcome(second)
        self.assert_refused("conflicting goal dispatch job id")

    def test_record_must_agree_with_its_claimed_intent(self):
        name = self.write_intent(1)
        self.write_record(1, intent_id=name)
        self.assert_refused("goal record conflicts with dispatch intent")
        self.write_outcome(name)
        self.write_record(1, intent_id=name, fingerprint=FINGERPRINT, task="different",
                          submitted_epoch=1, submitted="1970-01-01T00:00:01Z")
        self.assert_refused("goal record conflicts with dispatch intent")
        self.write_record(1, intent_id=name, fingerprint=FINGERPRINT, task="t",
                          submitted_epoch=1, submitted="1970-01-01T00:00:01Z")
        self.assertEqual(self.reconcile(), (1, 0, 1))

    def test_claimed_job_already_recorded_under_another_ordinal_is_refused(self):
        self.write_record(1)
        name = self.write_intent(2)
        self.write_outcome(name)
        self.assert_refused("goal dispatch job id already recorded elsewhere")


if __name__ == "__main__":
    unittest.main()
