#!/usr/bin/env python3
"""Tests for the optional local Omnilane Live UI."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI_SCRIPT = ROOT / "scripts" / "ui.py"
SPEC = importlib.util.spec_from_file_location("omnilane_ui", UI_SCRIPT)
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="omnilane-ui-test-")
        self.home = Path(self.tempdir.name)
        self.jobs = self.home / "jobs"
        self.jobs.mkdir()
        self.store = ui.JobStore(self.jobs)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_job(
        self,
        job_id,
        *,
        meta=True,
        task="TASK-CANARY",
        output="OUTPUT-CANARY",
        pid=None,
        exit_code=None,
    ):
        job = self.jobs / job_id
        job.mkdir()
        if meta is True:
            (job / "meta.json").write_text(
                json.dumps(
                    {
                        "lane": "hard-judgment",
                        "vendor": "claude",
                        "model": "claude-opus-4-8",
                        "effort": "high",
                        "timeout": 1800,
                        "mode": "advise",
                        "workdir": "/tmp/project",
                        "candidate": "2/4",
                        "started": "2026-07-14T12:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
        elif isinstance(meta, str):
            (job / "meta.json").write_text(meta, encoding="utf-8")
        if task is not None:
            (job / "task.txt").write_text(task, encoding="utf-8")
        if output is not None:
            (job / "out.txt").write_text(output, encoding="utf-8")
        if pid is not None:
            (job / "pid").write_text(str(pid), encoding="ascii")
        if exit_code is not None:
            (job / "exit").write_text(str(exit_code), encoding="ascii")
        return job

    def test_classifies_job_states_without_exposing_bodies_in_summary(self):
        running = "20260714-120005-100-5"
        succeeded = "20260714-120004-100-4"
        failed = "20260714-120003-100-3"
        dead = "20260714-120002-100-2"
        starting = "20260714-120001-100-1"
        invalid = "20260714-120000-100-0"
        self.make_job(running, pid=os.getpid())
        self.make_job(succeeded, exit_code=0)
        self.make_job(failed, exit_code=7)
        self.make_job(dead, pid=2147483647)
        self.make_job(starting, meta=False, output=None)
        self.make_job(invalid, meta="{not-json", exit_code=0)

        payload = self.store.snapshot()
        by_id = {job["id"]: job for job in payload["jobs"]}

        self.assertEqual("running", by_id[running]["state"])
        self.assertEqual("succeeded", by_id[succeeded]["state"])
        self.assertEqual(0, by_id[succeeded]["exitCode"])
        self.assertEqual("failed", by_id[failed]["state"])
        self.assertEqual(7, by_id[failed]["exitCode"])
        self.assertEqual("dead", by_id[dead]["state"])
        self.assertEqual("starting", by_id[starting]["state"])
        self.assertEqual("invalid", by_id[invalid]["state"])
        encoded = json.dumps(payload)
        self.assertNotIn("TASK-CANARY", encoded)
        self.assertNotIn("OUTPUT-CANARY", encoded)

    def test_newest_fifty_only(self):
        for number in range(55):
            self.make_job(
                "20260714-120000-200-{:02d}".format(number),
                task=None,
                output=None,
                exit_code=0,
            )

        jobs = self.store.snapshot()["jobs"]

        self.assertEqual(50, len(jobs))
        self.assertEqual("20260714-120000-200-54", jobs[0]["id"])
        self.assertEqual("20260714-120000-200-05", jobs[-1]["id"])

    def test_detail_caps_large_text_head_and_tail(self):
        job_id = "20260714-120000-300-1"
        large = "H" * (384 * 1024) + "M" * (100 * 1024) + "T" * (128 * 1024)
        self.make_job(job_id, task="hello", output=large, exit_code=0)

        detail = self.store.detail(job_id)

        self.assertTrue(detail["outputTruncated"])
        self.assertTrue(detail["output"].startswith("H" * 100))
        self.assertIn("TRUNCATED", detail["output"])
        self.assertTrue(detail["output"].endswith("T" * 100))
        self.assertLessEqual(len(detail["output"].encode("utf-8")), 513 * 1024)

    def test_invalid_utf8_is_replaced(self):
        job_id = "20260714-120000-400-1"
        job = self.make_job(job_id, task=None, output=None, exit_code=0)
        (job / "task.txt").write_bytes(b"before\xffafter")

        detail = self.store.detail(job_id)

        self.assertEqual("before\ufffdafter", detail["task"])

    def test_rejects_traversal_and_symlinked_content(self):
        job_id = "20260714-120000-500-1"
        secret = self.home / "secret.txt"
        secret.write_text("DO-NOT-SERVE", encoding="utf-8")
        job = self.make_job(job_id, output=None, exit_code=0)
        (job / "out.txt").symlink_to(secret)
        linked_job = self.jobs / "20260714-120000-500-2"
        linked_job.symlink_to(self.home, target_is_directory=True)

        detail = self.store.detail(job_id)
        encoded = json.dumps(detail)

        self.assertNotIn("DO-NOT-SERVE", encoded)
        self.assertIn("out.txt", detail["invalidFiles"])
        self.assertNotIn(linked_job.name, [item["id"] for item in self.store.snapshot()["jobs"]])
        for bad_id in ("../secret.txt", "%2e%2e", "20260714-120000-1-1/../../x", "not-a-job"):
            with self.assertRaises(ui.JobNotFound):
                self.store.detail(bad_id)

    def test_oversized_control_files_fail_closed(self):
        meta_id = "20260714-120000-600-1"
        pid_id = "20260714-120000-600-2"
        exit_id = "20260714-120000-600-3"
        meta_job = self.make_job(meta_id, meta=False, task=None, output=None)
        with (meta_job / "meta.json").open("wb") as handle:
            handle.seek(128 * 1024)
            handle.write(b"x")
        pid_job = self.make_job(pid_id, pid=None, task=None, output=None)
        (pid_job / "pid").write_text("9" * 1000, encoding="ascii")
        exit_job = self.make_job(exit_id, task=None, output=None)
        (exit_job / "exit").write_text("9" * 1000, encoding="ascii")

        by_id = {job["id"]: job for job in self.store.snapshot()["jobs"]}

        self.assertEqual("invalid", by_id[meta_id]["state"])
        self.assertEqual("invalid", by_id[pid_id]["state"])
        self.assertEqual("invalid", by_id[exit_id]["state"])


if __name__ == "__main__":
    unittest.main()
