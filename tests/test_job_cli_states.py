"""Ordinary job CLI surfaces share one offline, observed state contract."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
STATES = {"running", "done", "dead", "pending", "cancelled", "expired"}


class JobCliStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-job-states-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        self.env["OMNILANE_HOME"] = str(self.home)
        self.next_id = 1

    def job(self, *, pid=None, exit_code=None):
        job_id = f"20261001-000000-1234-{self.next_id}"
        self.next_id += 1
        directory = self.home / "jobs" / job_id
        directory.mkdir(parents=True)
        (directory / "meta.json").write_text('{"lane":"fixture","vendor":"exec"}\n')
        if pid is not None:
            (directory / "pid").write_text(str(pid) + "\n")
        if exit_code is not None:
            (directory / "exit").write_text(str(exit_code) + "\n")
        return job_id, directory

    def cli(self, *args, expected=0):
        result = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), *args],
                                env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def owned_worker(self, *, live):
        child = subprocess.Popen(["sleep", "60"] if live else ["true"], env=self.env)
        if live:
            def cleanup():
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
            self.addCleanup(cleanup)
        else:
            child.wait(timeout=5)
        return child

    def assert_surfaces(self, job_id, state, exit_code=None, reason=None):
        status = json.loads(self.cli("--json", "status", job_id).stdout)
        self.assertEqual(status["job"]["state"], state)
        self.assertEqual(status["job"]["exit_code"], exit_code)
        if reason:
            self.assertEqual(status["job"]["reason"], reason)
        status_text = self.cli("status", job_id).stdout
        self.assertIn(state, status_text)
        if reason:
            self.assertIn(reason, status_text)
        listing = json.loads(self.cli("--json", "list").stdout)
        listed = next(job for job in listing["jobs"] if job["id"] == job_id)
        self.assertEqual((listed["state"], listed["exit_code"]), (state, exit_code))
        text_row = next(row for row in self.cli("list").stdout.splitlines()
                        if row.startswith(job_id + " "))
        self.assertEqual(text_row.split()[1].split("(")[0], state)
        for filter_state in STATES:
            with self.subTest(filter_state=filter_state):
                args = ("list", "--status", filter_state)
                rows = json.loads(self.cli("--json", *args).stdout)["jobs"]
                self.assertEqual(job_id in {row["id"] for row in rows}, state == filter_state)
                text_ids = {row.split()[0] for row in self.cli(*args).stdout.splitlines()}
                self.assertEqual(job_id in text_ids, state == filter_state)

    def assert_unfinished_result(self, job_id, directory, state, reason=None):
        # Partial output is not a recorded result, and reads must not publish one.
        (directory / "out.txt").write_text("partial fixture output\n")
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        for json_mode in (False, True):
            args = ("--json",) if json_mode else ()
            result = self.cli(*args, "result", job_id, expected=1)
            if json_mode:
                record = json.loads(result.stdout)
                self.assertFalse(record["ok"])
                self.assertEqual(record["command"], "result")
                self.assertNotIn("job", record)
                error = record["error"]
                self.assertEqual(result.stderr, "")
            else:
                self.assertEqual(result.stdout, "")
                error = result.stderr
            if state == "dead":
                self.assertIn("dead", error)
                self.assertIn(reason, error)
                self.assertIn("no recorded result", error)
                self.assertNotIn("still running", error)
            else:
                self.assertEqual(error.strip(), "still running")
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})
        self.assertFalse((directory / "exit").exists())

    def test_reaped_owned_worker_is_dead_on_status_and_list(self):
        child = self.owned_worker(live=False)
        job_id, _ = self.job(pid=child.pid)
        self.assert_surfaces(job_id, "dead", reason="worker gone, no exit recorded")

    def test_reaped_owned_worker_result_explains_missing_record(self):
        child = self.owned_worker(live=False)
        job_id, directory = self.job(pid=child.pid)
        self.assert_unfinished_result(job_id, directory, "dead", "worker gone, no exit recorded")

    def test_live_owned_worker_remains_running(self):
        child = self.owned_worker(live=True)
        job_id, directory = self.job(pid=child.pid)
        self.assert_surfaces(job_id, "running")
        self.assert_unfinished_result(job_id, directory, "running")
        self.assertIsNone(child.poll())

    def test_missing_pid_retains_startup_running_state(self):
        job_id, directory = self.job()
        self.assert_surfaces(job_id, "running")
        self.assert_unfinished_result(job_id, directory, "running")

    def test_ordinary_invalid_pid_is_dead_on_status_and_list(self):
        job_id, _ = self.job(pid="not-a-pid")
        self.assert_surfaces(job_id, "dead", reason="invalid pid metadata")

    def test_ordinary_invalid_pid_result_explains_missing_record(self):
        job_id, directory = self.job(pid="not-a-pid")
        self.assert_unfinished_result(job_id, directory, "dead", "invalid pid metadata")

    def test_completed_results_keep_real_exit_and_streams(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                # A recorded exit takes precedence over PID state.
                job_id, directory = self.job(pid="not-a-pid", exit_code=exit_code)
                (directory / "out.txt").write_text("completed fixture output\n")
                (directory / "out.txt.stderr.log").write_text("fixture diagnostic\n")
                self.assert_surfaces(job_id, "done", exit_code)
                result = self.cli("result", job_id, expected=exit_code)
                self.assertEqual(result.stdout, "completed fixture output\n")
                self.assertEqual(result.stderr, "--- stderr ---\nfixture diagnostic\n")
                record = json.loads(self.cli("--json", "result", job_id, expected=exit_code).stdout)
                self.assertTrue(record["ok"])
                self.assertEqual(record["job"], {"id": job_id, "state": "done", "exit_code": exit_code,
                                                "output_available": True, "stderr_available": True})

    def test_dead_filter_composes_with_lane_and_vendor(self):
        child = self.owned_worker(live=False)
        dead_id, _ = self.job(pid=child.pid)
        self.job()
        self.job(exit_code=0)
        for flags in (("--lane", "fixture"), ("--vendor", "exec"),
                      ("--lane", "fixture", "--vendor", "exec")):
            result = self.cli("--json", "list", "--status", "dead", *flags)
            self.assertEqual([row["id"] for row in json.loads(result.stdout)["jobs"]], [dead_id])
        result = self.cli("--json", "list", "--status", "dead", "--lane", "other")
        self.assertEqual(json.loads(result.stdout)["jobs"], [])

    def test_help_and_bad_filter_list_all_supported_states(self):
        self.assertIn("running|done|dead|pending|cancelled|expired", self.cli("help").stdout)
        rejected = self.cli("--json", "list", "--status", "other", expected=2)
        error = json.loads(rejected.stdout)["error"]
        for state in STATES:
            self.assertIn(state, error)

    def test_bash_completion_offers_all_job_states(self):
        script = '''source "$1"
shift
COMP_WORDS=(omnilane jobs "$@" --status "")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
_omnilane
printf '%s\\n' "${COMPREPLY[@]}"
'''
        for args in (("list",), ("--json", "list")):
            result = subprocess.run(["bash", "-c", script, "_", str(ROOT / "completions/omnilane.bash"), *args],
                                    env=self.env, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(result.stdout.split()), STATES)

    def test_zsh_and_fish_completion_declare_all_job_states(self):
        zsh_source = (ROOT / "completions/_omnilane").read_text()
        fish_source = (ROOT / "completions/omnilane.fish").read_text()
        zsh_status = next((line for line in zsh_source.splitlines() if "--status[" in line), "")
        fish_status = next(line for line in fish_source.splitlines() if "-l status " in line)
        for state in STATES:
            self.assertIn(state, zsh_status)
            self.assertIn(state, fish_status)
        for shell, source in (("zsh", "_omnilane"), ("fish", "omnilane.fish")):
            if shutil.which(shell, path=self.env["PATH"]):
                result = subprocess.run([shell, "-n", str(ROOT / "completions" / source)],
                                        env=self.env, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_zsh_passes_all_states_to_list_completion(self):
        if not shutil.which("zsh", path=self.env["PATH"]):
            self.skipTest("zsh is unavailable")
        script = '''source "$1"
shift
_arguments() { print -l -- "$@"; }
words=(omnilane jobs "$@" --status "")
CURRENT=${#words}
_omnilane
'''
        for args in (("list",), ("--json", "list")):
            result = subprocess.run(["zsh", "-c", script, "_", str(ROOT / "completions/_omnilane"), *args],
                                    env=self.env, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            status = next(line for line in result.stdout.splitlines() if line.startswith("--status["))
            self.assertEqual(set(status.split(":(")[1].rstrip(")").split()), STATES)


if __name__ == "__main__":
    unittest.main()
