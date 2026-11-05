"""Forced stop of live mailbox jobs in tests/test_live_mailbox.sh, offline.

Each scenario runs as a case of that file (force-stop-<scenario>), through its own
isolated standalone entry, with fake job controllers it starts and always ends itself.
"""
import offline_env  # noqa: F401
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "test_live_mailbox.sh"


class LiveForceStopTests(unittest.TestCase):
    def run_scenario(self, scenario: str, timeout: int = 120) -> str:
        result = subprocess.run(
            ["bash", str(SCRIPT), f"force-stop-{scenario}"],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def test_slow_process_tables_still_reach_every_job(self):
        out = self.run_scenario("batch")
        self.assertEqual(len(re.findall(r"^live jobs: TERM process group ", out, re.M)), 8, out)

    def test_kill_follows_recorded_members_after_the_leader_exits(self):
        self.run_scenario("leader-exits")

    def test_a_reused_pid_gets_no_term(self):
        self.assertIn("reused, not signalled", self.run_scenario("reuse-term"))

    def test_a_pid_reused_after_term_gets_no_kill(self):
        self.assertIn("reused, not signalled", self.run_scenario("reuse-kill"))

    def test_a_job_without_a_pid_file_is_found_by_its_worker_log(self):
        self.run_scenario("no-pid")

    def test_the_proc_search_reports_holders(self):
        self.run_scenario("holders-proc")

    def test_limits_end_searches_and_commands_halfway(self):
        self.run_scenario("interrupt")

    def test_cancel_wait_and_forced_stop_name_the_job(self):
        out = self.run_scenario("stop-flow", timeout=180)
        self.assertIn("did not stop after cancel and wait; its process groups were ended", out)

    def test_forced_stop_keeps_to_force_by(self):
        self.run_scenario("tight")

    def test_the_run_deadline_stays_inside_ci_step_limit(self):
        # Evaluate the script's own deadline lines with the defaults, as a run would.
        text = SCRIPT.read_text(encoding="utf-8")
        start = text.index('\nLIVE_EPOCH0="$(date +%s)"\n') + 1
        end = text.index("\n", text.index("\nRUN_DEADLINE=") + 1)
        block = text[start:end]
        environment = {k: v for k, v in os.environ.items()
                       if k not in ("LIVE_FINAL_BY_EPOCH", "OMNILANE_LIVE_RUN_LIMIT")}
        result = subprocess.run(
            ["bash", "-c", block + '\necho "$((LIVE_FINAL_BY_EPOCH - LIVE_EPOCH0)) $RUN_DEADLINE $STOP_BY $FORCE_BY $FINAL_BY"'],
            env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10, check=True)
        total, run_by, stop_by, force_by, final_by = map(int, result.stdout.split())
        # FINAL_BY bounds the run: the last step can end a second past it (SECONDS counts
        # whole seconds) and the epoch can be a second off, which 590 leaves room for.
        self.assertLessEqual(total, 590, result.stdout)
        self.assertLess(total + 2, 600, result.stdout)
        self.assertLess(run_by, stop_by)
        self.assertLess(stop_by, force_by)
        self.assertLess(force_by, final_by)
        self.assertLessEqual(final_by, total)

    def test_cleanup_steps_that_can_block_run_within_a_limit(self):
        text = SCRIPT.read_text(encoding="utf-8")
        cleanup = text[text.index("\ncleanup() {"):text.index("\ntrap cleanup EXIT")]
        for command in ("/bin/rm -rf", "rmdir"):
            for line in cleanup.splitlines():
                if command in line and not line.lstrip().startswith("#"):
                    self.assertIn("within ", line.split(command)[0], line)
        holders = text[text.index("\nfile_holders() {"):text.index("\njob_processes() {")]
        self.assertEqual(holders.count('within "$seconds" "$by"'), 2, holders)


if __name__ == "__main__":
    unittest.main()
