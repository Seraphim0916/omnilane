"""Goal dispatch rejects preview options without touching its ordinary ledger."""
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
REFUSAL = "goal dispatch does not support --dry-run"


class GoalDryRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-goal-preview-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.work = self.root / "work"
        self.home.mkdir()
        self.work.mkdir()
        self.calls = self.root / "worker-calls.jsonl"
        worker = self.root / "worker.py"
        worker.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "task = pathlib.Path(sys.argv[4]).read_text()\n"
            "with open(os.environ['FAKE_WORKER_CALLS'], 'a') as handle:\n"
            "    handle.write(json.dumps({'effort': sys.argv[3], 'task': task}) + '\\n')\n"
            "pathlib.Path(sys.argv[5]).write_text('ordinary fake worker completed\\n')\n")
        worker.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'probe: exec "{worker}" -\n')
        self.env.update(OMNILANE_HOME=str(self.home), FAKE_WORKER_CALLS=str(self.calls))
        opened = self.cli("goal", "open", "ordinary preview boundary", "--workdir", str(self.work))
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.goal_id = opened.stdout.strip()
        self.goal = self.home / "goals" / self.goal_id

    def cli(self, *args, stdin=None):
        return subprocess.run([str(CLI), *args], env=self.env, stdin=stdin,
                              capture_output=True, text=True, timeout=20)

    def snapshot(self):
        # Include modification metadata as well as bytes: even a same-content
        # goal refresh must not happen on this unsupported invocation.
        result = {}
        for path in sorted(self.home.rglob("*")):
            info = path.stat()
            result[str(path.relative_to(self.home))] = (
                info.st_mode, info.st_mtime_ns,
                None if path.is_dir() else path.read_bytes())
        return result

    def assert_unchanged(self, before):
        after = self.snapshot()
        changed = [name for name in sorted(before.keys() | after.keys())
                   if before.get(name) != after.get(name)]
        self.assertEqual(changed, [], "goal preview changed state: " + ", ".join(changed))
        self.assertFalse(self.calls.exists(), "preview invoked the fake provider")

    def assert_refused(self, result):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(REFUSAL, result.stderr)
        self.assertIn("omnilane dispatch --dry-run", result.stderr)

    def wait_job(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+\n$")
        job_id = result.stdout.strip()
        waited = self.cli("jobs", "wait", job_id, "--timeout", "10")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertEqual(waited.stdout, "done exit=0\n")
        deadline = time.monotonic() + 10
        while not (self.home / "inbox" / (job_id + ".json")).exists():
            self.assertLess(time.monotonic(), deadline, "fake worker did not finish publication")
            time.sleep(.02)
        return json.loads(self.calls.read_text().splitlines()[-1])

    def test_goal_dry_run_rejection_is_explicit(self):
        result = self.cli("goal", "dispatch", self.goal_id, "--dry-run", "--mode", "work",
                          "probe", "ordinary routing preview")
        self.assert_refused(result)

    def test_goal_dry_run_creates_no_intent_reservation_or_state_changes(self):
        before = self.snapshot()
        result = self.cli("goal", "dispatch", self.goal_id, "--dry-run", "--mode", "work",
                          "probe", "ordinary routing preview")
        self.assert_unchanged(before)
        self.assert_refused(result)

    def test_goal_dry_run_after_other_options_is_also_rejected(self):
        before = self.snapshot()
        result = self.cli("goal", "dispatch", self.goal_id, "--background", "--single-shot",
                          "--mode", "work", "--workdir", str(self.work), "--effort", "low",
                          "--dry-run", "probe", "ordinary later preview option")
        self.assert_unchanged(before)
        self.assert_refused(result)

    def test_rejection_does_not_consume_stdin_task(self):
        task = self.root / "stdin-task.txt"
        task.write_text("ordinary stdin task\n")
        before = self.snapshot()
        with task.open() as handle:
            result = self.cli("goal", "dispatch", self.goal_id, "--dry-run", "--mode", "work",
                              "probe", "-", stdin=handle)
            self.assertEqual(handle.tell(), 0, "unsupported preview consumed the task input")
        self.assert_unchanged(before)
        self.assert_refused(result)

    def test_literal_dry_run_task_is_dispatched_normally(self):
        result = self.cli("goal", "dispatch", self.goal_id, "--mode", "work", "probe", "--dry-run")
        call = self.wait_job(result)
        self.assertIn("--dry-run", call["task"])

    def test_option_value_equal_to_dry_run_is_dispatched_normally(self):
        result = self.cli("goal", "dispatch", self.goal_id, "--mode", "work", "--effort",
                          "--dry-run", "--effort", "low", "probe", "ordinary option value")
        call = self.wait_job(result)
        self.assertEqual(call["effort"], "low")

    def test_invalid_option_value_keeps_normal_dispatch_validation(self):
        direct = self.cli("dispatch", "--mode", "work", "--workdir", str(self.work),
                          "--effort", "--dry-run", "probe", "ordinary invalid effort")
        result = self.cli("goal", "dispatch", self.goal_id, "--mode", "work", "--effort",
                          "--dry-run", "probe", "ordinary invalid effort")
        self.assertNotEqual(direct.returncode, 0)
        self.assertEqual(result.returncode, direct.returncode, result.stderr)
        self.assertIn("dispatch failed", result.stderr)
        self.assertNotIn(REFUSAL, result.stderr)

    def test_real_option_after_equal_value_is_still_rejected(self):
        before = self.snapshot()
        result = self.cli("goal", "dispatch", self.goal_id, "--mode", "work", "--effort",
                          "--dry-run", "--dry-run", "probe", "ordinary second option")
        self.assert_unchanged(before)
        self.assert_refused(result)

    def test_direct_preview_remains_supported_and_side_effect_free(self):
        before = self.snapshot()
        result = self.cli("dispatch", "--dry-run", "--mode", "work", "--workdir", str(self.work),
                          "probe", "ordinary direct preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry_run=yes\n", result.stdout)
        self.assertIn("provider_invoked=no\n", result.stdout)
        self.assert_unchanged(before)

    def test_cli_help_explains_goal_preview_boundary(self):
        for args in (("help",), ("goal", "--help")):
            with self.subTest(args=args):
                result = self.cli(*args)
                text = result.stdout + result.stderr
                self.assertIn("--dry-run", text)
                self.assertIn("omnilane dispatch --dry-run", text)

    def test_preflight_value_options_match_dispatch_parser(self):
        pattern = r"^\s+(--mode\|--workdir[^\n]*)\)\s*$"
        direct = re.findall(pattern, (ROOT / "scripts/dispatch.sh").read_text(), re.MULTILINE)
        goal = re.findall(pattern, (ROOT / "scripts/lib/goal-loop.sh").read_text(), re.MULTILINE)
        self.assertEqual(len(direct), 1, "locate the dispatch parser's value-taking option arm")
        self.assertEqual(len(goal), 1, "locate the goal preflight's value-taking option arm")
        self.assertEqual(set(goal[0].split("|")), set(direct[0].split("|")))


if __name__ == "__main__":
    unittest.main()
