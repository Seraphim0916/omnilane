"""A job's exit file is published whole, and cancel never replaces one already recorded.

The writers pause through a BASH_ENV printf that waits while an exit file, or its
temporary, exists but is still empty: the redirection has opened it and nothing is
written yet. A reader running during that pause must never see a partial exit.
"""
import offline_env  # noqa: F401
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from test_close_holder_early import start_time
from test_close_pid_identity import JOB_ID, IdentityTestCase

ROOT = pathlib.Path(__file__).resolve().parents[1]

PAUSE_WHILE_EMPTY = r'''
printf() {
  local exit_hook_file exit_hook_try
  if [[ -n "${EXIT_HOOK:-}" && ! -e "$EXIT_HOOK.reached" ]]; then
    for exit_hook_file in "$OMNILANE_HOME"/jobs/*/exit "$OMNILANE_HOME"/jobs/*/exit.tmp.*; do
      if [[ -f "$exit_hook_file" && ! -L "$exit_hook_file" && ! -s "$exit_hook_file" ]]; then
        : > "$EXIT_HOOK.reached"
        for ((exit_hook_try = 0; exit_hook_try < 300; exit_hook_try++)); do
          [[ -e "$EXIT_HOOK.go" ]] && break
          sleep 0.05
        done
        break
      fi
    done
  fi
  builtin printf "$@"
}
'''


def await_path(case, path, seconds=15):
    end = time.monotonic() + seconds
    while not path.exists():
        case.assertLess(time.monotonic(), end, f"{path.name} never appeared")
        time.sleep(0.02)


def exit_leftovers(job):
    return sorted(p.name for p in job.iterdir() if p.name.startswith("exit."))


class CancelExitTests(IdentityTestCase):
    def dying_worker(self):
        # A group leader with the default TERM action: it dies without recording an exit.
        process = self.victim()
        (self.job / "supervised").write_text("1\n")
        (self.job / "pid.start").write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        return process

    def hooked_env(self, hook):
        bash_env = self.home / "pause-while-empty.sh"
        bash_env.write_text(PAUSE_WHILE_EMPTY)
        return dict(self.env, BASH_ENV=str(bash_env), EXIT_HOOK=str(hook))

    def test_a_reader_during_the_cancel_write_never_sees_a_partial_exit(self):
        process = self.dying_worker()
        hook = self.home / "hook"
        cancel = subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "cancel", JOB_ID],
                                  env=self.hooked_env(hook), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, stdin=subprocess.DEVNULL)
        try:
            await_path(self, pathlib.Path(f"{hook}.reached"))
            during = self.jobs("status")
            exit_during = (self.job / "exit").read_bytes() if (self.job / "exit").exists() else None
        finally:
            pathlib.Path(f"{hook}.go").touch()
            out, err = cancel.communicate(timeout=30)
        self.assertNotIn("invalid", during.stdout + during.stderr)
        self.assertIn(exit_during, (None, b"137\n"))
        self.assertEqual((cancel.returncode, out), (0, "cancelled (exit 137)\n"), err)
        process.wait(timeout=5)
        exit_file = self.job / "exit"
        self.assertEqual(exit_file.read_bytes(), b"137\n")
        self.assertEqual(stat.S_IMODE(exit_file.stat().st_mode), 0o600)
        self.assertEqual(exit_file.stat().st_nlink, 1)
        self.assertEqual(exit_leftovers(self.job), [])

    def test_cancel_keeps_an_exit_the_worker_recorded_after_the_first_check(self):
        # jobs.sh copied with a pause after cancel found no exit, before it confirms the
        # worker is gone: the worker records its own exit in that window and dies.
        copy = self.home / "repo"
        shutil.copytree(ROOT / "scripts", copy / "scripts")
        jobs = copy / "scripts/jobs.sh"
        anchor = "      cancel_state=0\n"
        source = jobs.read_text()
        self.assertEqual(source.count(anchor), 1, "the cancel anchor moved")
        hook = self.home / "late-hook"
        pause = (f'      : > "{hook}.reached"\n'
                 f'      for ((late_try = 0; late_try < 300; late_try++)); do [[ -e "{hook}.go" ]] && break; sleep 0.05; done\n')
        jobs.write_text(source.replace(anchor, pause + anchor))
        process = self.dying_worker()
        cancel = subprocess.Popen(["/bin/bash", str(jobs), "cancel", JOB_ID], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  stdin=subprocess.DEVNULL)
        try:
            await_path(self, pathlib.Path(f"{hook}.reached"))
            process.wait(timeout=5)
            (self.job / "exit").write_text("143\n")
        finally:
            pathlib.Path(f"{hook}.go").touch()
            out, err = cancel.communicate(timeout=30)
        self.assertEqual((cancel.returncode, out), (0, "cancelled (exit 143)\n"), err)
        self.assertEqual((self.job / "exit").read_text(), "143\n")
        self.assertEqual(exit_leftovers(self.job), [])

    def test_a_leftover_temporary_is_not_an_exit(self):
        process = self.victim()
        process.kill()
        process.wait(timeout=5)
        (self.job / "pid").write_text(f"{process.pid}\n")
        (self.job / "exit.tmp.123-456").write_text("0\n")
        status = self.jobs("status")
        self.assertNotIn("done", status.stdout)
        self.assertTrue(status.stdout.startswith("dead"), status.stdout + status.stderr)
        listing = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "list"], env=self.env,
                                 capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn(JOB_ID, listing.stdout)
        self.assertNotIn("done", listing.stdout)
        prune = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "prune", "--keep", "0"], env=self.env,
                               capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(prune.returncode, 0, prune.stderr)
        self.assertNotIn(JOB_ID, prune.stdout)
        self.assertTrue((self.job / "exit.tmp.123-456").exists())
        self.assertFalse((self.job / "exit").exists())


class DispatchExitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-exit-atomic-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.env, violations = offline_env.isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.bins = self.root / "bin"
        self.home.mkdir()
        self.bins.mkdir()
        gate = self.bins / "fake-exec"
        gate.write_text('#!/usr/bin/env bash\nprintf "ran\\n" > "$5"\n')
        gate.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        bash_env = self.root / "pause-while-empty.sh"
        bash_env.write_text(PAUSE_WHILE_EMPTY)
        self.hook = self.root / "hook"
        self.env.update(OMNILANE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1",
                        PATH=str(self.bins) + os.pathsep + self.env["PATH"])
        self.hooked = dict(self.env, BASH_ENV=str(bash_env), EXIT_HOOK=str(self.hook))

    def test_a_reader_during_finish_job_never_sees_a_partial_exit(self):
        dispatch = subprocess.Popen(["bash", str(ROOT / "scripts/dispatch.sh"), "--workdir", str(self.root),
                                     "fixture", "Print a harmless fixed offline diagnostic"],
                                    env=self.hooked, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, stdin=subprocess.DEVNULL)
        try:
            await_path(self, pathlib.Path(f"{self.hook}.reached"), 30)
            (job,) = sorted((self.home / "jobs").iterdir())
            during = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), "status", job.name], env=self.env,
                                    capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            exit_during = (job / "exit").read_bytes() if (job / "exit").exists() else None
        finally:
            pathlib.Path(f"{self.hook}.go").touch()
            out, err = dispatch.communicate(timeout=60)
        self.assertNotIn("invalid", during.stdout + during.stderr)
        self.assertIn(exit_during, (None, b"0\n"))
        self.assertEqual(dispatch.returncode, 0, err)
        exit_file = job / "exit"
        self.assertEqual(exit_file.read_bytes(), b"0\n")
        self.assertEqual(stat.S_IMODE(exit_file.stat().st_mode), 0o600)
        self.assertEqual(exit_leftovers(job), [])


if __name__ == "__main__":
    unittest.main()
