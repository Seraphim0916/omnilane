"""Defensive schema checks before goal consumers use persisted budget fields."""
import json
import hashlib
import subprocess
import sys
from pathlib import Path
import unittest

import test_goal_recovery as recovery

MAX_COUNTER = (1 << 63) - 1


class GoalBudgetValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery.GoalRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.goal / "budget.json"
        self.original = json.loads(self.path.read_text())

    def snapshot(self, fixture=None):
        fixture = fixture or self.fixture
        result = {}
        for path in fixture.goal.rglob("*"):
            key = str(path.relative_to(fixture.goal))
            if path.is_dir():
                result[key] = "directory"
            else:
                info = path.stat()
                result[key] = (path.read_bytes(), info.st_ino, info.st_mtime_ns)
        return result

    def reject(self, state, command="status", arguments=()):
        self.path.write_text(json.dumps(state))
        before = self.snapshot()
        result = self.fixture.run_goal(command, self.fixture.goal_id, *arguments)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid goal budget", result.stderr)
        self.assertEqual(self.snapshot(), before, "invalid state changed the ledger")
        self.assertFalse((self.fixture.home / "jobs").exists())

    def test_limits_require_exact_positive_integers_or_null(self):
        for field in ("budget_jobs", "budget_seconds"):
            for value in (True, False, 0, -1, 1000000000, "2", "not-a-number", [], {}):
                with self.subTest(field=field, value=value):
                    self.reject(dict(self.original, **{field: value}))

    def test_counters_and_epoch_require_bounded_nonnegative_integers(self):
        for field in ("spent_jobs", "spent_seconds", "fuse_trips", "reserved_jobs", "started_epoch", "closed_epoch"):
            for value in (True, False, -1, MAX_COUNTER + 1, "0", [], {}):
                with self.subTest(field=field, value=value):
                    self.reject(dict(self.original, **{field: value}))

    def test_combined_job_counters_cannot_overflow_shell_arithmetic(self):
        self.reject(dict(self.original, spent_jobs=MAX_COUNTER, reserved_jobs=1))

    def test_workdir_rejects_nonstrings_relative_paths_and_field_delimiters(self):
        for value in (None, False, 1, [], {}, "", "relative", "/tmp/line\nbreak",
                      "/tmp/tab\tbreak", "/tmp/cr\rbreak", "/tmp/nul\x00break"):
            with self.subTest(value=value):
                self.reject(dict(self.original, workdir=value))

    def test_required_fields_and_status_are_validated(self):
        for field in ("status", "budget_jobs", "budget_seconds", "spent_jobs", "spent_seconds",
                      "fuse_trips", "started_epoch", "workdir"):
            with self.subTest(missing=field):
                state = dict(self.original)
                del state[field]
                self.reject(state)
        for status in (None, False, 1, [], {}, "unknown"):
            with self.subTest(status=status):
                self.reject(dict(self.original, status=status))

    def test_invalid_types_stop_note_close_and_dispatch_before_mutations(self):
        for command, arguments in (("note", ("ordinary note",)),
                                   ("close", ("--summary", "ordinary summary")),
                                   ("dispatch", ("probe", "ordinary validation fixture"))):
            with self.subTest(command=command):
                self.reject(dict(self.original, budget_jobs="2"), command, arguments)

    def test_private_prepare_validates_before_creating_journal_state(self):
        helper = Path(__file__).resolve().parents[1] / "scripts/lib/goal_dispatch.py"
        for value in ("2", True):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(dict(self.original, budget_jobs=value)))
                before = self.snapshot()
                result = subprocess.run([sys.executable, str(helper), "prepare",
                    str(self.fixture.home), self.fixture.goal_id, "probe",
                    hashlib.sha256(b"probe\0ordinary fixture").hexdigest(), "ordinary fixture"],
                    env=self.fixture.env, text=True, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid goal budget", result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.fixture.home / "jobs").exists())

    def test_valid_legacy_unlimited_and_bounded_budgets_remain_supported(self):
        for limit in (None, 1, 999999999):
            with self.subTest(limit=limit):
                state = dict(self.original, budget_jobs=limit, budget_seconds=limit)
                state.pop("reserved_jobs", None)
                self.path.write_text(json.dumps(state))
                result = self.fixture.run_goal("status", self.fixture.goal_id)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = json.loads(self.path.read_text())
                self.assertEqual(actual["budget_jobs"], limit)
                self.assertEqual(actual["reserved_jobs"], 0)

    def test_valid_workdir_with_spaces_and_unicode_is_preserved(self):
        workdir = self.fixture.root / "project 名称 [draft]"
        workdir.mkdir()
        self.path.write_text(json.dumps(dict(self.original, workdir=str(workdir))))
        result = self.fixture.run_goal("status", self.fixture.goal_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_text())["workdir"], str(workdir))


if __name__ == "__main__":
    unittest.main()
