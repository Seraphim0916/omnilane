"""Goal status finds a completion the prompt hook already consumed, and consumed
records are kept for a period after delivery instead of a fixed count."""
import offline_env  # Activate suite isolation for direct file execution.
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_goal_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "20261001-000000-1234-1"
DAY = 86400
NOW = 2000000000
FAKE_NOW = "package FakeNow;\nBEGIN { *CORE::GLOBAL::time = sub () { $ENV{FAKE_NOW} } }\n1;\n"


class GoalCompletionLookupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery.GoalRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        case = self.fixture
        job = case.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        (job / "exit").write_text("0\n")
        os.utime(job / "exit", (1759276800, 1759276800))
        self.record_path = case.goal / "jobs" / "job-00000001.json"
        self.record_path.write_text(json.dumps({
            "job_id": JOB_ID, "ordinal": 1, "lane": "probe", "fingerprint": "a" * 64,
            "submitted_epoch": 1759276790, "failure_counted": False}))
        self.inbox = case.home / "inbox"
        self.consumed = self.inbox / "consumed"
        self.consumed.mkdir(parents=True)

    def completion(self, directory, tail, finished="2026-10-01T00:00:05Z"):
        path = directory / f"{JOB_ID}.json"
        path.write_text(json.dumps({"job_id": JOB_ID, "exit": 0, "finished": finished, "tail": tail}))
        return path

    def status(self):
        result = self.fixture.run_goal("status", self.fixture.goal_id)
        return result, json.loads(self.record_path.read_text())

    def test_a_consumed_completion_supplies_tail_and_finished(self):
        self.completion(self.consumed, "from consumed")
        result, saved = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((saved["state"], saved["exit"]), ("done", 0))
        self.assertEqual(saved.get("tail"), "from consumed")
        self.assertEqual(saved["finished"], "2026-10-01T00:00:05Z")

    def test_the_inbox_record_wins_when_both_exist(self):
        self.completion(self.inbox, "from inbox", "2026-10-01T00:00:07Z")
        self.completion(self.consumed, "from consumed", "2026-10-01T00:00:05Z")
        result, saved = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((saved.get("tail"), saved["finished"]), ("from inbox", "2026-10-01T00:00:07Z"))

    def test_an_unsafe_inbox_entry_does_not_fall_back_to_consumed(self):
        outside = self.completion(self.fixture.root, "outside")
        (self.inbox / f"{JOB_ID}.json").symlink_to(outside)
        self.completion(self.consumed, "from consumed")
        result, saved = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tail", saved)
        self.assertEqual(saved["finished"], "2025-10-01T00:00:00Z")

    def test_a_symlinked_consumed_record_is_not_read(self):
        outside = self.completion(self.fixture.root, "outside")
        (self.consumed / f"{JOB_ID}.json").symlink_to(outside)
        result, saved = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tail", saved)
        self.assertEqual(saved["finished"], "2025-10-01T00:00:00Z")

    def test_a_symlinked_consumed_directory_is_not_read(self):
        elsewhere = self.fixture.root / "elsewhere"
        elsewhere.mkdir()
        self.completion(elsewhere, "elsewhere")
        self.consumed.rmdir()
        self.consumed.symlink_to(elsewhere, target_is_directory=True)
        result, saved = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tail", saved)

    def test_a_non_regular_consumed_entry_is_not_read(self):
        (self.consumed / f"{JOB_ID}.json").mkdir()
        result, saved = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tail", saved)

    def test_an_oversized_consumed_record_is_refused_like_an_inbox_one(self):
        for directory in (self.inbox, self.consumed):
            with self.subTest(directory=directory.name):
                path = directory / f"{JOB_ID}.json"
                path.write_text('{"tail":"' + "x" * 1048576 + '"}')
                result, _ = self.status()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("oversized JSON record", result.stderr)
                path.unlink()


class ConsumedRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-consumed-retention-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = offline_env.isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.inbox = self.home / "inbox"
        self.consumed = self.inbox / "consumed"
        self.consumed.mkdir(parents=True)
        os.chmod(self.inbox, 0o700)
        os.chmod(self.consumed, 0o700)
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        lib = self.root / "perl"
        lib.mkdir()
        (lib / "FakeNow.pm").write_text(FAKE_NOW)
        self.env.update(OMNILANE_HOME=str(self.home), CLAUDE_PROJECT_DIR=str(self.workdir),
                        PERL5LIB=str(lib), PERL5OPT="-MFakeNow", FAKE_NOW=str(NOW))

    def record(self, directory, job_id, age):
        path = directory / f"{job_id}.json"
        path.write_text(json.dumps({"job_id": job_id, "lane": "probe", "vendor": "exec",
                                    "workdir": str(self.workdir), "exit": 0, "tail": "t"}))
        os.utime(path, (NOW - age, NOW - age))
        return path

    def run_hook(self):
        self.record(self.inbox, "20330518-000000-1-9999", 0)
        result = subprocess.run(["bash", str(ROOT / "hooks/report-completions.sh")], env=self.env,
                                input="{}", text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("job=20330518-000000-1-9999", result.stdout)
        return result

    def test_a_burst_of_recent_records_is_kept(self):
        recent = [self.record(self.consumed, f"20330518-{n:06d}-1-1", 60) for n in range(205)]
        self.run_hook()
        self.assertTrue(all(path.exists() for path in recent))
        self.assertEqual(len(list(self.consumed.glob("*.json"))), 206)

    def test_age_boundaries_use_the_modification_time(self):
        exactly = self.record(self.consumed, "20330504-000000-1-1", 14 * DAY)
        older = self.record(self.consumed, "20330504-000000-1-2", 14 * DAY + 1)
        much_older = self.record(self.consumed, "20330401-000000-1-3", 40 * DAY)
        future = self.record(self.consumed, "20330519-000000-1-4", -3600)
        self.run_hook()
        self.assertTrue(exactly.exists())
        self.assertFalse(older.exists())
        self.assertFalse(much_older.exists())
        # A clock set back leaves records from the "future": they are kept.
        self.assertTrue(future.exists())

    def test_a_record_claimed_late_is_kept_from_its_delivery(self):
        late = self.record(self.inbox, "20330401-000000-1-5", 40 * DAY)
        self.run_hook()
        claimed = self.consumed / late.name
        self.assertTrue(claimed.is_file())
        self.assertEqual(int(claimed.stat().st_mtime), NOW)

    def test_only_old_record_files_inside_consumed_are_removed(self):
        old = 30 * DAY
        record = self.record(self.consumed, "20330401-000000-1-6", old)
        outside = self.record(self.root, "20330401-000000-1-7", old)
        link = self.consumed / "20330401-000000-1-8.json"
        link.symlink_to(outside)
        os.utime(link, (NOW - old, NOW - old), follow_symlinks=False)
        nested = self.consumed / "20330401-000000-1-9.json"
        nested.mkdir()
        inner = self.record(nested, "20330401-000000-1-10", old)
        others = []
        for name in ("notes.json", "20330401-000000-1-11.json.bak", ".20330401-000000-1-12.tmp.1-2",
                     "20330401-0000001-1-13.json", "readme.txt"):
            path = self.consumed / name
            path.write_text("{}")
            os.utime(path, (NOW - old, NOW - old))
            others.append(path)
        self.run_hook()
        self.assertFalse(record.exists())
        self.assertTrue(link.is_symlink())
        self.assertTrue(outside.exists())
        self.assertTrue(inner.exists())
        self.assertTrue(all(path.exists() for path in others), [p.name for p in others if not p.exists()])


if __name__ == "__main__":
    unittest.main()
