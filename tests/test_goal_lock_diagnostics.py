"""Offline timeout diagnostics never reclaim or modify goal locks."""
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class GoalLockDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-goal-lock-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env, self.violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = self.root / "home"
        self.home.mkdir()
        self.env["OMNILANE_HOME"] = str(self.home)
        opened = self.run_goal("open", "offline lock diagnosis", "--workdir", str(self.root))
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.goal_id = opened.stdout.strip()
        self.goal = self.home / "goals" / self.goal_id
        self.lock = self.goal / ".lock"
        self.lock.mkdir(mode=0o700)
        self.pid = self.lock / "pid"
        # Avoid 4.9 seconds of deliberate contention delay in every case.
        bins = self.root / "fixture-bin"
        bins.mkdir()
        sleep = bins / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)
        self.env["PATH"] = str(bins) + os.pathsep + self.env["PATH"]
        self.bins = bins

    def run_goal(self, *args):
        return subprocess.run(["bash", str(ROOT / "scripts/lib/goal-loop.sh"), *args],
                              env=self.env, text=True, capture_output=True, timeout=15)

    def snapshot(self, root):
        result = {}
        for path in (root, *sorted(root.rglob("*"))):
            info = path.lstat()
            value = (os.readlink(path) if path.is_symlink() else
                     path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
            result[str(path.relative_to(root))] = (
                info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_mtime_ns, value)
        return result

    def assert_busy_unchanged(self, detail):
        before = self.snapshot(self.goal)
        failed = self.run_goal("status", self.goal_id)
        self.assertEqual(failed.returncode, 75, failed.stderr)
        self.assertEqual(failed.stdout, "")
        self.assertIn(f"goal is busy: {self.goal_id}", failed.stderr)
        self.assertIn(f"lock path: {self.lock}", failed.stderr)
        self.assertIn(detail, failed.stderr)
        self.assertIn("lock left unchanged; manual verification is required", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual(self.snapshot(self.goal), before)
        return failed

    def inject(self, fault):
        hook = self.root / "diagnostic_fault.py"
        hook.write_text('''import errno, os, sys
sys.argv = sys.argv[1:]
source = sys.stdin.read()
if "def inspect_lock():" in source:
    fault = os.environ["DIAGNOSTIC_FAULT"]
    if fault == "unavailable":
        raise SystemExit(3)
    if fault == "owner":
        os.geteuid = lambda: -1
    if fault == "no_nofollow":
        del os.O_NOFOLLOW
    if fault == "no_directory":
        del os.O_DIRECTORY
    if fault == "no_dir_fd":
        original_open = os.open
        def open_fd(*args, **kwargs):
            if "dir_fd" in kwargs:
                raise NotImplementedError("injected unsupported dir_fd")
            return original_open(*args, **kwargs)
        os.open = open_fd
    def kill(pid, sig):
        assert pid == 424242 and sig == 0, (pid, sig)
        with open(os.environ["DIAGNOSTIC_PROBES"], "a") as handle:
            handle.write(f"{pid}:{sig}\\n")
        if fault == "missing":
            raise ProcessLookupError(errno.ESRCH, "injected absent process")
        if fault == "permission":
            raise PermissionError(errno.EPERM, "injected inaccessible process")
        if fault == "unknown":
            raise OSError(errno.EIO, "injected ambiguous process")
    os.kill = kill
exec(compile(source, "<goal diagnostic fixture>", "exec"), {"__name__": "__main__"})
''')
        wrapper = self.bins / "python3"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                           shlex.quote(str(hook)) + ' "$@"\n')
        wrapper.chmod(0o755)
        self.probes = self.root / "process-probes"
        self.env.update(DIAGNOSTIC_FAULT=fault, DIAGNOSTIC_PROBES=str(self.probes))

    def test_live_pid_is_preserved_and_pid_reuse_is_explicit(self):
        self.pid.write_text(f"{os.getpid()}\n")
        self.assert_busy_unchanged("exists; PID reuse means lock ownership is unverified")

    def test_missing_pid_is_ambiguous_not_reclaimed(self):
        self.assert_busy_unchanged("owner PID is missing; acquisition may be incomplete or interrupted")

    def test_invalid_pid_is_bounded_and_never_probed(self):
        self.inject("missing")
        for value in (b"", b"0\n", b"-1\n", b"12 34\n", b"42\n\n", b"x" * 100000):
            with self.subTest(value=value[:20]):
                self.pid.write_bytes(value)
                self.assert_busy_unchanged("owner PID metadata is invalid")
                self.assertFalse(self.probes.exists())

    def test_absent_process_is_only_a_possible_stale_lock(self):
        self.pid.write_text("424242\n")
        self.inject("missing")
        self.assert_busy_unchanged("recorded PID 424242 does not exist; lock may be stale")
        self.assertEqual(self.probes.read_text(), "424242:0\n")

    def test_exited_and_reaped_owner_is_a_possible_stale_lock(self):
        owner = subprocess.Popen([sys.executable, "-c", "pass"], env=self.env)
        self.assertEqual(owner.wait(timeout=5), 0)
        self.pid.write_text(f"{owner.pid}\n")
        self.assert_busy_unchanged(f"recorded PID {owner.pid} does not exist; lock may be stale")

    def test_permission_denied_never_means_stale(self):
        self.pid.write_text("424242\n")
        self.inject("permission")
        failed = self.assert_busy_unchanged("cannot be checked (permission denied); ownership is unknown")
        self.assertNotIn("may be stale", failed.stderr)
        self.assertEqual(self.probes.read_text(), "424242:0\n")

    def test_other_process_error_remains_ambiguous(self):
        self.pid.write_text("424242\n")
        self.inject("unknown")
        failed = self.assert_busy_unchanged("recorded PID 424242 cannot be checked; ownership is unknown")
        self.assertNotIn("may be stale", failed.stderr)

    def test_symlinked_pid_is_never_followed(self):
        target = self.root / "outside-pid"
        target.write_text("424242\n")
        self.pid.symlink_to(target)
        self.inject("missing")
        self.assert_busy_unchanged("owner PID metadata is unsafe or unreadable")
        self.assertFalse(self.probes.exists())
        self.assertEqual(target.read_text(), "424242\n")

    def test_symlinked_lock_is_never_followed(self):
        self.lock.rmdir()
        target = self.root / "outside-lock"
        target.mkdir()
        (target / "pid").write_text("424242\n")
        before = self.snapshot(target)
        self.lock.symlink_to(target, target_is_directory=True)
        self.inject("missing")
        self.assert_busy_unchanged("lock is missing, unsafe, or unreadable")
        self.assertFalse(self.probes.exists())
        self.assertEqual(self.snapshot(target), before)

    def test_fifo_pid_does_not_block_or_get_probed(self):
        os.mkfifo(self.pid)
        self.inject("missing")
        self.assert_busy_unchanged("owner PID metadata is unsafe")
        self.assertFalse(self.probes.exists())

    def test_different_directory_owner_is_not_inspected(self):
        self.pid.write_text("424242\n")
        self.inject("owner")
        self.assert_busy_unchanged("lock directory has a different owner")
        self.assertFalse(self.probes.exists())

    def test_diagnostic_failure_preserves_exit_code_and_path(self):
        self.pid.write_text("424242\n")
        self.inject("unavailable")
        self.assert_busy_unchanged("lock diagnostic unavailable; ownership is unknown")

    def test_unsupported_safe_open_features_remain_ambiguous(self):
        self.pid.write_text("424242\n")
        for fault in ("no_nofollow", "no_directory", "no_dir_fd"):
            with self.subTest(fault=fault):
                self.inject(fault)
                self.assert_busy_unchanged("owner metadata cannot be inspected safely; ownership is unknown")
                self.assertFalse(self.probes.exists())


if __name__ == "__main__":
    unittest.main()
