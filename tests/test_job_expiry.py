"""Read-side job deadlines through the offline public CLI; no vendor calls."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
MISSING = object()


class JobExpiryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-job-expiry-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        self.env["OMNILANE_HOME"] = str(self.home)
        self.next_id = 1

    def cli(self, *args, expected=0):
        result = subprocess.run(["bash", str(ROOT / "bin/omnilane"), "jobs", *args],
                                env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def job(self, *, pid=None, exit_code=None):
        job_id = f"20261001-000000-1234-{self.next_id}"
        self.next_id += 1
        directory = self.home / "jobs" / job_id
        directory.mkdir(parents=True)
        self.save(directory / "meta.json", {"lane": "fixture", "vendor": "codex"})
        if pid is not None:
            (directory / "pid").write_text(str(pid) + "\n")
        if exit_code is not None:
            (directory / "exit").write_text(str(exit_code) + "\n")
        return job_id, directory

    @staticmethod
    def save(path, value):
        path.write_text(json.dumps(value, separators=(",", ":")) + "\n")

    @staticmethod
    def bytes_in(directory):
        return {p.name: p.read_bytes() for p in directory.iterdir()}

    def native(self, *, age=0, timeout=600, meta_timeout=MISSING, created=True):
        job_id, directory = self.job()
        start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age))
        start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        state = dict(job_id=job_id, executor="native", state="pending", agent_id=None,
                     executor_reason="fixture", vendor="codex", model="fixture-model",
                     effort="high", harness="fixture-harness")
        if created:
            state["created"] = start
        if timeout is not MISSING:
            state["timeout"] = timeout
        metadata = {"lane": "fixture", "vendor": "codex", "started": start}
        if meta_timeout is not MISSING:
            metadata["timeout"] = meta_timeout
        self.save(directory / "native.json", state)
        self.save(directory / "meta.json", metadata)
        (directory / "native.lock").touch()
        return job_id, directory

    def status(self, job_id):
        return json.loads(self.cli("--json", "status", job_id).stdout)["job"]

    def stats(self, *filters):
        return json.loads(self.cli("--json", "stats", *filters).stdout)

    def complete(self, job_id, directory, outcome="success"):
        state = json.loads((directory / "native.json").read_text())
        completion = dict(schema_version=1, job_id=job_id, agent_id="fixture-agent",
                          runtime={k: state[k] for k in ("vendor", "model", "effort", "harness")},
                          outcome=outcome, result="late fixture result", evidence=["fixture:observed"])
        completion["runtime"]["backend"] = "fixture-backend"
        path = self.root / (job_id + "-completion.json")
        self.save(path, completion)
        return self.cli("--json", "complete-native", job_id, str(path))

    def test_native_inside_deadline_including_grace_is_pending(self):
        job_id, directory = self.native(age=850, timeout=600)
        before = self.bytes_in(directory)
        self.assertEqual(self.status(job_id)["state"], "pending")
        rows = json.loads(self.cli("--json", "list", "--status", "pending").stdout)["jobs"]
        self.assertEqual([row["id"] for row in rows], [job_id])
        result = self.cli("wait", job_id, "--timeout", "0", expected=124)
        self.assertIn("wait timeout after 0s", result.stderr)
        self.assertEqual(before, self.bytes_in(directory))

    def test_expired_native_all_surfaces_are_read_only(self):
        job_id, directory = self.native(age=1200, timeout=600)
        before = self.bytes_in(directory)
        state = self.status(job_id)
        self.assertEqual(state["state"], "expired")
        self.assertIsNone(state["exit_code"])
        reason = state["reason"]
        created = json.loads((directory / "native.json").read_text())["created"]
        deadline = datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ") + datetime.timedelta(seconds=900)
        self.assertIn(deadline.strftime("%Y-%m-%dT%H:%M:%SZ"), reason)
        for text in ("deadline", "600", "300"):
            self.assertIn(text, reason)
        self.assertEqual(self.cli("status", job_id).stdout, f"expired ({reason})\n")
        for filter_state in ("running", "done", "dead", "pending", "cancelled", "expired"):
            rows = json.loads(self.cli("--json", "list", "--status", filter_state).stdout)["jobs"]
            self.assertEqual([row["id"] for row in rows], [job_id] if filter_state == "expired" else [])
        listing = self.cli("list", "--status", "expired", "--lane", "fixture", "--vendor", "codex")
        self.assertEqual(listing.stdout.split()[:2], [job_id, "expired"])
        for prefix in ((), ("--json",)):
            result = self.cli(*prefix, "result", job_id, expected=2)
            error = json.loads(result.stdout)["error"] if prefix else result.stderr
            self.assertIn("expired", error)
            self.assertIn(reason, error)
            self.assertIn("no recorded result", error)
        started = time.monotonic()
        result = self.cli("wait", job_id, "--timeout", "60", expected=125)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result.stdout, f"expired ({reason})\n")
        self.assertEqual(before, self.bytes_in(directory))

    def test_recorded_native_timeout_wins_over_metadata_and_current_environment(self):
        self.env["OMNILANE_TIMEOUT"] = "1"
        job_id, _ = self.native(age=1200, timeout=3600, meta_timeout=1)
        self.assertEqual(self.status(job_id)["state"], "pending")
        self.env["OMNILANE_TIMEOUT"] = "999999"
        job_id, _ = self.native(age=400, timeout=30, meta_timeout=3600)
        self.assertEqual(self.status(job_id)["state"], "expired")

    def test_legacy_metadata_timeout_and_started_are_used(self):
        for age, expected in ((1200, "pending"), (4000, "expired")):
            with self.subTest(age=age):
                job_id, _ = self.native(age=age, timeout=MISSING, meta_timeout=3600, created=False)
                self.assertEqual(self.status(job_id)["state"], expected)

    def test_missing_timeout_uses_dispatcher_default_600_plus_grace(self):
        self.env["OMNILANE_TIMEOUT"] = "999999"
        for age, expected in ((850, "pending"), (1000, "expired")):
            with self.subTest(age=age):
                job_id, _ = self.native(age=age, timeout=MISSING)
                self.assertEqual(self.status(job_id)["state"], expected)

    def test_invalid_deadline_metadata_fails_closed_without_writes(self):
        for timeout in (True, "600", 0, -1, None, 10**30):
            with self.subTest(timeout=timeout):
                job_id, directory = self.native(age=1200, timeout=timeout)
                before = self.bytes_in(directory)
                result = self.cli("--json", "status", job_id, expected=2)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertEqual(before, self.bytes_in(directory))

    def test_dead_cli_wait_returns_promptly_without_recording_exit(self):
        # Reap a process we own rather than guessing a PID in the live store.
        process = subprocess.Popen(["true"], env=self.env)
        process.wait(timeout=5)
        job_id, directory = self.job(pid=process.pid)
        before = self.bytes_in(directory)
        self.assertEqual(self.status(job_id)["state"], "dead")
        result = self.cli("wait", job_id, "--timeout", "60", expected=125)
        self.assertIn("dead (worker gone, no exit recorded)", result.stdout)
        self.assertEqual(before, self.bytes_in(directory))

    def test_stats_and_recommend_separate_unfinished_states(self):
        self.native(age=1200)
        self.native(age=10)
        process = subprocess.Popen(["true"], env=self.env)
        process.wait(timeout=5)
        self.job(pid=process.pid)
        self.job(pid=os.getpid())
        self.job(exit_code=0)
        self.job(exit_code=7)
        for filters in ((), ("--lane", "fixture"), ("--vendor", "codex"),
                        ("--lane", "fixture", "--vendor", "codex")):
            stats = self.stats(*filters)
            for name, value in dict(sampled=6, succeeded=1, failed=1, running=1,
                                    pending=1, dead=1, expired=1, invalid_exit=0, success_rate=50).items():
                self.assertEqual(stats[name], value, name)
                self.assertIn(f"{name}={value}", self.cli("stats", *filters).stdout)
        self.assertEqual(self.stats("--last", "2")["sampled"], 2)
        self.assertEqual(self.stats("--lane", "other")["sampled"], 0)
        recommendation = json.loads(self.cli("--json", "recommend", "--min-samples", "1").stdout)
        self.assertEqual(recommendation["completed"], 2)
        for name in ("running", "pending", "dead", "expired"):
            self.assertEqual(recommendation["excluded"][name], 1)
            self.assertIn(f"{name}=1", self.cli("recommend").stdout)

    def test_only_dead_and_expired_have_zero_running_and_success_rate(self):
        self.native(age=1200)
        self.job(pid="invalid")
        stats = self.stats()
        self.assertEqual((stats["dead"], stats["expired"], stats["running"], stats["success_rate"]), (1, 1, 0, 0))

    def test_late_completion_can_succeed_or_fail_and_counts_as_terminal(self):
        for outcome, exit_code in (("success", 0), ("failure", 1)):
            with self.subTest(outcome=outcome):
                job_id, directory = self.native(age=1200)
                self.assertEqual(self.status(job_id)["state"], "expired")
                self.complete(job_id, directory, outcome)
                state = self.status(job_id)
                self.assertEqual((state["state"], state["exit_code"]), ("done", exit_code))
                self.assertEqual(self.cli("result", job_id, expected=exit_code).stdout, "late fixture result\n")
                self.assertEqual(self.cli("wait", job_id, expected=exit_code).stdout, f"done exit={exit_code}\n")
                self.assertFalse((directory / "exit").exists())
        stats = self.stats()
        self.assertEqual((stats["succeeded"], stats["failed"], stats["expired"], stats["running"]), (1, 1, 0, 0))
        self.assertEqual(stats["success_rate"], 50)
        recommendation = json.loads(self.cli("--json", "recommend").stdout)
        self.assertEqual(recommendation["completed"], 2)

    def test_cancelled_native_is_terminal_not_expired(self):
        job_id, _ = self.native(age=1200)
        self.cli("cancel", job_id)
        self.assertEqual(self.status(job_id)["state"], "cancelled")
        self.cli("wait", job_id, expected=143)
        stats = self.stats()
        self.assertEqual((stats["failed"], stats["expired"], stats["running"]), (1, 0, 0))

    def test_wait_rechecks_native_deadline(self):
        job_id, _ = self.native(age=299, timeout=1)
        self.assertEqual(self.status(job_id)["state"], "pending")
        result = self.cli("wait", job_id, "--timeout", "10", expected=125)
        self.assertIn("expired (", result.stdout)


if __name__ == "__main__":
    unittest.main()
