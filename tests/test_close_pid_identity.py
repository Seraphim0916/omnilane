"""jobs close and cancel must not signal a process that reused a recorded PID.

Each victim is a real process the test started, with no USR1 handler, so a signal
that gets out ends it. Nothing intercepts kill: the victim surviving is the proof.
Each refusal has a control where the same victim, correctly identified, is hit.
"""
import offline_env  # noqa: F401
import json
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

from test_close_holder_early import FAKE_HIRES, start_time

ROOT = pathlib.Path(__file__).resolve().parents[1]
JOB_ID = "20261101-170000-12345-1"
OTHER_START = "Thu Jan  1 00:00:01 2026"


class IdentityTestCase(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(exist_ok=True)
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="pid-identity-", dir=scratch))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.job = self.home / "jobs" / JOB_ID
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text('{"lane":"triage","vendor":"codex","session_mode":"live"}')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_")}
        self.env["OMNILANE_HOME"] = str(self.home)
        self.holder_file = self.job / "inbox.holder.pid"
        self.holder_start = self.job / "inbox.holder.pid.start"

    def victim(self, code="import time; time.sleep(60)"):
        process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True, stdin=subprocess.DEVNULL)
        self.addCleanup(process.wait, 5)
        self.addCleanup(self.kill_quietly, process)
        # A reaped victim is gone; an unreaped one would stay a zombie that kill -0 still finds.
        threading.Thread(target=process.wait, daemon=True).start()
        return process

    @staticmethod
    def kill_quietly(process):
        if process.poll() is None:
            process.kill()

    def assert_alive(self, process):
        time.sleep(0.3)
        self.assertIsNone(process.poll(), "the victim was signalled")

    def assert_killed_by(self, process, number):
        process.wait(timeout=5)
        self.assertEqual(process.returncode, -number)

    def jobs(self, *args, env=None, timeout=30):
        return subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), *args, JOB_ID], env=env or self.env,
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def blocked_ps_env(self):
        shims = self.home / "shims"
        shims.mkdir(exist_ok=True)
        (shims / "ps").write_text("#!/bin/bash\nexit 126\n")
        (shims / "ps").chmod(0o755)
        return dict(self.env, PATH=f"{shims}:{os.environ['PATH']}")

    def refused_probe_env(self):
        # kill -0 refused and ps blocked, as inside the Codex sandbox. Real signals stay real.
        bash_env = self.home / "bash-env"
        bash_env.write_text('kill() { [[ "$1" == "-0" ]] && return 1; builtin kill "$@"; }\n')
        return dict(self.blocked_ps_env(), BASH_ENV=str(bash_env))

    def fake_close(self, events, env=None, kill_log=""):
        # A virtual clock passes the 11s deadline at once; signals stay real unless kill_log is set.
        lib = self.home / "fake-perl" / "Time"
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "HiRes.pm").write_text(FAKE_HIRES)
        env = dict(env or self.env, PERL5LIB=str(self.home / "fake-perl"), FAKE_DEADLINE="11",
                   FAKE_KILL_LOG=str(kill_log), FAKE_KILL_FIRST_DEAD="", FAKE_READ_ACTION="",
                   FAKE_READ_MARKER=str(self.home / "read-marker"), FAKE_HOLDER=str(self.holder_file),
                   FAKE_EVENTS=";".join(f"{at}:{path}:{content}" for at, path, content in events))
        return self.jobs("close", env=env, timeout=20)

    def late_holder(self, pid, start=None):
        events = [("0.5", self.holder_start, f"{start}\n")] if start is not None else []
        return events + [("1", self.holder_file, f"{pid}\n")]


class CloseAtStartTests(IdentityTestCase):
    def test_control_matching_start_time_is_signalled(self):
        victim = self.victim()
        self.holder_start.write_text(start_time(victim.pid) + "\n")
        self.holder_file.write_text(f"{victim.pid}\n")
        self.fake_close([])
        self.assert_killed_by(victim, signal.SIGUSR1)

    def test_reused_pid_is_not_signalled(self):
        victim = self.victim()
        self.holder_start.write_text(OTHER_START + "\n")
        self.holder_file.write_text(f"{victim.pid}\n")
        result = self.fake_close([])
        self.assertEqual((result.returncode, result.stderr), (1, "live inbox holder is gone\n"))
        self.assert_alive(victim)

    def test_reused_holder_and_writer_fail_at_once(self):
        holder, writer = self.victim(), self.victim()
        self.holder_start.write_text(OTHER_START + "\n")
        self.holder_file.write_text(f"{holder.pid}\n")
        (self.job / "pid.start").write_text(OTHER_START + "\n")
        (self.job / "pid").write_text(f"{writer.pid}\n")
        started = time.monotonic()
        result = self.jobs("close")
        self.assertEqual((result.returncode, result.stderr), (1, "live inbox holder is gone\n"))
        self.assertLess(time.monotonic() - started, 5, "close waited for a receipt nobody can write")
        self.assert_alive(holder)
        self.assert_alive(writer)

    def test_reused_holder_with_live_writer_keeps_waiting_for_the_receipt(self):
        holder, writer = self.victim(), self.victim()
        self.holder_start.write_text(OTHER_START + "\n")
        self.holder_file.write_text(f"{holder.pid}\n")
        (self.job / "pid.start").write_text(start_time(writer.pid) + "\n")
        (self.job / "pid").write_text(f"{writer.pid}\n")
        result = self.fake_close([("2", self.job / "exit", "0\n")])
        self.assertEqual((result.returncode, result.stdout), (0, "already closed (exit 0)\n"), result.stderr)
        self.assert_alive(holder)

    def test_older_job_with_a_live_pid_is_refused_and_explained(self):
        victim = self.victim()
        self.holder_file.write_text(f"{victim.pid}\n")
        result = self.jobs("close")
        self.assertEqual(result.returncode, 1)
        for part in ("older omnilane", str(victim.pid), "no signal was sent",
                     f"kill -USR1 {victim.pid}", "ps -o lstart="):
            self.assertIn(part, result.stderr)
        self.assert_alive(victim)

    def test_blocked_ps_refuses_and_points_outside_the_sandbox(self):
        victim = self.victim()
        self.holder_start.write_text(start_time(victim.pid) + "\n")
        self.holder_file.write_text(f"{victim.pid}\n")
        result = self.jobs("close", env=self.blocked_ps_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs close {JOB_ID}", result.stderr)
        self.assertNotIn("gone", result.stderr)
        self.assert_alive(victim)

    def test_blocked_ps_still_calls_a_dead_pid_gone(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait(timeout=5)
        self.holder_start.write_text(OTHER_START + "\n")
        self.holder_file.write_text(f"{dead.pid}\n")
        result = self.jobs("close", env=self.blocked_ps_env())
        self.assertEqual((result.returncode, result.stderr), (1, "live inbox holder is gone\n"))

    def test_a_refused_probe_does_not_call_a_dead_pid_gone(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait(timeout=5)
        self.holder_start.write_text(OTHER_START + "\n")
        self.holder_file.write_text(f"{dead.pid}\n")
        result = self.jobs("close", env=self.refused_probe_env())
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertNotIn("gone", result.stderr)

    def test_older_job_is_not_called_gone_when_the_probe_is_refused(self):
        victim = self.victim()
        self.holder_file.write_text(f"{victim.pid}\n")
        result = self.jobs("close", env=self.refused_probe_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs close {JOB_ID}", result.stderr)
        self.assert_alive(victim)

    def test_invalid_start_files_are_refused(self):
        victim = self.victim()
        self.holder_file.write_text(f"{victim.pid}\n")
        for name, write in (("symlink", lambda: self.holder_start.symlink_to(self.job / "meta.json")),
                            ("oversized", lambda: self.holder_start.write_text("x" * 200 + "\n")),
                            ("non-ascii", lambda: self.holder_start.write_text("週日 11月\n")),
                            ("empty", lambda: self.holder_start.write_text(""))):
            with self.subTest(name):
                write()
                result = self.jobs("close")
                self.assertEqual((result.returncode, result.stderr), (1, "live inbox holder is unavailable\n"))
                self.assert_alive(victim)
                self.holder_start.unlink()

    def test_pid_and_start_removed_during_the_read_is_not_an_older_job(self):
        shims = self.home / "shims"
        shims.mkdir()
        (shims / "cat").write_text(textwrap.dedent(f"""\
            #!/bin/bash
            /bin/cat "$@"
            rc=$?
            for arg in "$@"; do
              if [[ "$arg" == "{self.holder_file}" ]]; then
                /bin/rm "{self.holder_file}" "{self.holder_start}"
                printf '0\\n' > "{self.job}/exit"
              fi
            done
            exit $rc
        """))
        (shims / "cat").chmod(0o755)
        (shims / "wc").write_text('#!/bin/bash\nexec /usr/bin/wc "$@"\n')
        (shims / "wc").chmod(0o755)
        victim = self.victim()
        self.holder_file.write_text(f"{victim.pid}\n")
        env = dict(self.env, PATH=f"{shims}:{os.environ['PATH']}")
        result = self.jobs("close", env=env)
        self.assertNotIn("older omnilane", result.stderr)
        self.assertEqual((result.returncode, result.stdout), (0, "already closed (exit 0)\n"), result.stderr)
        self.assert_alive(victim)


class CloseLatePidTests(IdentityTestCase):
    """The PID file appears after close started: the Perl polling path."""

    def test_control_matching_start_time_is_signalled(self):
        victim = self.victim()
        result = self.fake_close(self.late_holder(victim.pid, start_time(victim.pid)))
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assert_killed_by(victim, signal.SIGUSR1)

    def test_reused_pid_is_not_signalled(self):
        victim = self.victim()
        result = self.fake_close(self.late_holder(victim.pid, OTHER_START))
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "live inbox holder is unavailable\n"))
        self.assert_alive(victim)

    def test_reused_pid_never_reaches_kill(self):
        victim = self.victim()
        kill_log = self.home / "kill-log"
        self.fake_close(self.late_holder(victim.pid, OTHER_START), kill_log=kill_log)
        self.assertFalse(kill_log.exists())

    def test_real_clock_late_pid_is_not_signalled(self):
        victim = self.victim()
        closer = subprocess.Popen(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "close", JOB_ID], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        time.sleep(1.5)
        self.holder_start.write_text(OTHER_START + "\n")
        tmp = self.job / "inbox.holder.pid.tmp"
        tmp.write_text(f"{victim.pid}\n")
        os.replace(tmp, self.holder_file)
        _, err = closer.communicate(timeout=20)
        self.assertEqual((closer.returncode, err), (1, "live inbox holder is unavailable\n"))
        self.assert_alive(victim)

    def test_older_job_with_a_live_pid_is_refused_and_explained(self):
        victim = self.victim()
        result = self.fake_close(self.late_holder(victim.pid))
        self.assertEqual(result.returncode, 1)
        for part in ("older omnilane", str(victim.pid), f"kill -USR1 {victim.pid}"):
            self.assertIn(part, result.stderr)
        self.assert_alive(victim)

    def test_blocked_ps_refuses_and_points_outside_the_sandbox(self):
        victim = self.victim()
        env = self.blocked_ps_env()
        result = self.fake_close(self.late_holder(victim.pid, OTHER_START), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(victim.pid), result.stderr)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs close {JOB_ID}", result.stderr)
        self.assert_alive(victim)

    def test_older_job_is_not_called_gone_when_the_probe_is_refused(self):
        victim = self.victim()
        lib = self.home / "fake-perl" / "Time"
        lib.mkdir(parents=True)
        hires = FAKE_HIRES.replace('return CORE::kill($_[0], @_[1 .. $#_]) unless $ENV{FAKE_KILL_LOG};',
                                   'if ($_[0] eq "0") { $! = 1; return 0; } return CORE::kill($_[0], @_[1 .. $#_]);')
        self.assertNotEqual(hires, FAKE_HIRES)
        (lib / "HiRes.pm").write_text(hires)
        env = dict(self.blocked_ps_env(), PERL5LIB=str(self.home / "fake-perl"), FAKE_DEADLINE="11",
                   FAKE_READ_ACTION="", FAKE_HOLDER=str(self.holder_file),
                   FAKE_EVENTS=f"1:{self.holder_file}:{victim.pid}\n")
        result = self.jobs("close", env=env, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(victim.pid), result.stderr)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs close {JOB_ID}", result.stderr)
        self.assert_alive(victim)

    def test_blocked_ps_still_treats_a_dead_late_pid_as_gone(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait(timeout=5)
        for start in (OTHER_START, None):
            with self.subTest(start=start):
                result = self.fake_close(self.late_holder(dead.pid, start), env=self.blocked_ps_env())
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("rerun jobs close outside the sandbox", result.stderr)
                self.holder_file.unlink(missing_ok=True)
                self.holder_start.unlink(missing_ok=True)

    def test_invalid_start_file_fails_at_once(self):
        victim = self.victim()
        result = self.fake_close(self.late_holder(victim.pid, "週日 11月"))
        self.assertEqual((result.returncode, result.stderr), (1, "live inbox holder is unavailable\n"))
        self.assert_alive(victim)


class TimeZoneTests(IdentityTestCase):
    def write_pid_file_for(self, zone):
        """Run the real writer in a process that then stays alive as itself."""
        script = (f'source "{ROOT}/scripts/lib/common.sh"; write_current_pid_file "{self.holder_file}" || exit 9; '
                  'exec sleep 60')
        process = subprocess.Popen(["/bin/bash", "-c", script], env=dict(self.env, TZ=zone), start_new_session=True, stdin=subprocess.DEVNULL)
        self.addCleanup(process.wait, 5)
        self.addCleanup(self.kill_quietly, process)
        threading.Thread(target=process.wait, daemon=True).start()
        end = time.monotonic() + 5
        while not self.holder_file.exists():
            self.assertLess(time.monotonic(), end, "the writer never published its PID")
            time.sleep(0.02)
        return process

    def test_written_and_read_in_different_zones_at_start(self):
        process = self.write_pid_file_for("Asia/Taipei")
        self.fake_close([], env=dict(self.env, TZ="America/New_York"))
        self.assert_killed_by(process, signal.SIGUSR1)

    def test_written_and_read_in_different_zones_late(self):
        process = self.write_pid_file_for("Asia/Taipei")
        pid = self.holder_file.read_text().strip()
        self.holder_file.unlink()
        self.fake_close([("1", self.holder_file, f"{pid}\n")], env=dict(self.env, TZ="America/New_York"))
        self.assert_killed_by(process, signal.SIGUSR1)

    def test_start_file_is_recorded_in_utc(self):
        process = self.write_pid_file_for("Asia/Taipei")
        self.assertEqual(self.holder_start.read_text().strip(), start_time(process.pid))


class CancelTests(IdentityTestCase):
    GROUP = ("import subprocess, sys, time\n"
             "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL)\n"
             "print(child.pid, flush=True)\n"
             "time.sleep(60)\n")

    def group_victim(self):
        process = subprocess.Popen([sys.executable, "-c", self.GROUP], start_new_session=True,
                                   stdout=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        child = int(process.stdout.readline())
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait, 5)
        self.addCleanup(self.kill_group, process)
        threading.Thread(target=process.wait, daemon=True).start()
        return process, child

    @staticmethod
    def kill_group(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass

    @staticmethod
    def child_alive(pid):
        result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        return result.returncode == 0 and not result.stdout.strip().startswith("Z")

    def test_control_matching_start_time_terminates_the_group(self):
        process, child = self.group_victim()
        (self.job / "pid.start").write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_killed_by(process, signal.SIGTERM)
        end = time.monotonic() + 3
        while self.child_alive(child) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertFalse(self.child_alive(child))

    def test_reused_pid_leaves_the_whole_group_alive(self):
        process, child = self.group_victim()
        (self.job / "pid.start").write_text(OTHER_START + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel")
        self.assertEqual((result.returncode, result.stdout), (0, "not running (no live worker to cancel)\n"))
        self.assertIn(f"PID {process.pid} is running, but its start time differs", result.stderr)
        self.assert_alive(process)
        self.assertTrue(self.child_alive(child))
        self.assertFalse((self.job / "exit").exists())

    def test_older_job_is_refused_and_explained(self):
        process, child = self.group_victim()
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel")
        self.assertEqual(result.returncode, 1)
        for part in ("older omnilane", str(process.pid), f"kill -TERM {process.pid}"):
            self.assertIn(part, result.stderr)
        self.assert_alive(process)
        self.assertTrue(self.child_alive(child))

    def test_older_job_with_a_dead_pid_is_not_running(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait(timeout=5)
        (self.job / "pid").write_text(f"{dead.pid}\n")
        result = self.jobs("cancel")
        self.assertEqual((result.returncode, result.stdout), (0, "not running (no live worker to cancel)\n"))

    def test_blocked_ps_refuses_and_points_outside_the_sandbox(self):
        process, child = self.group_victim()
        (self.job / "pid.start").write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel", env=self.blocked_ps_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs cancel outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs cancel {JOB_ID}", result.stderr)
        self.assert_alive(process)
        self.assertTrue(self.child_alive(child))

    def test_older_job_is_not_called_dead_when_the_probe_is_refused(self):
        process, child = self.group_victim()
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel", env=self.refused_probe_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs cancel outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs cancel {JOB_ID}", result.stderr)
        self.assert_alive(process)
        self.assertTrue(self.child_alive(child))
        self.assertFalse((self.job / "exit").exists())

    def test_invalid_start_file_is_refused(self):
        process, _ = self.group_victim()
        (self.job / "pid.start").write_text("週日\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid start time", result.stderr)
        self.assert_alive(process)

    def test_pid_reused_during_the_grace_period_gets_no_sigkill(self):
        # The worker ignores TERM. Its start time stops matching during the grace
        # period, as if it died and the PID went to another process.
        ready = self.home / "ready"
        process = self.victim("import pathlib, signal, sys, time\n"
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                              f"pathlib.Path({str(ready)!r}).write_text('1')\n"
                              "time.sleep(60)\n")
        end = time.monotonic() + 5
        while not ready.exists():
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        start_file = self.job / "pid.start"
        start_file.write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        shims = self.home / "shims"
        shims.mkdir()
        (shims / "sleep").write_text(f'#!/bin/bash\nprintf "{OTHER_START}\\n" > "{start_file}"\nexec /bin/sleep "$@"\n')
        (shims / "sleep").chmod(0o755)
        result = self.jobs("cancel", env=dict(self.env, PATH=f"{shims}:{os.environ['PATH']}"))
        self.assertEqual((result.returncode, result.stdout), (0, "cancelled (exit 137)\n"), result.stderr)
        self.assert_alive(process)

    def term_ignoring_job(self, on_sleep):
        ready = self.home / "ready"
        process = self.victim("import pathlib, signal, time\n"
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                              f"pathlib.Path({str(ready)!r}).write_text('1')\n"
                              "time.sleep(60)\n")
        end = time.monotonic() + 5
        while not ready.exists():
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        (self.job / "pid.start").write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        shims = self.home / "shims"
        shims.mkdir(exist_ok=True)
        (shims / "sleep").write_text(f'#!/bin/bash\n{on_sleep}\nexec /bin/sleep "$@"\n')
        (shims / "sleep").chmod(0o755)
        return process, dict(self.env, PATH=f"{shims}:{os.environ['PATH']}")

    def test_a_start_file_damaged_during_the_grace_period_records_no_exit(self):
        start_file = self.job / "pid.start"
        process, env = self.term_ignoring_job(f'printf "週日\\n" > "{start_file}"')
        result = self.jobs("cancel", env=env)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("invalid start time", result.stderr)
        self.assertIn("TERM was sent", result.stderr)
        self.assertFalse((self.job / "exit").exists())
        self.assert_alive(process)

    def test_a_start_file_lost_during_the_grace_period_records_no_exit(self):
        start_file = self.job / "pid.start"
        process, env = self.term_ignoring_job(f'/bin/rm -f "{start_file}"')
        result = self.jobs("cancel", env=env)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertFalse((self.job / "exit").exists())
        self.assert_alive(process)

    def test_a_damaged_start_file_does_not_cut_the_grace_period_short(self):
        exit_file = self.job / "exit"
        start_file = self.job / "pid.start"
        ready = self.home / "ready"
        process = self.victim("import pathlib, signal, sys, time\n"
                              "def stop(*_):\n"
                              "    time.sleep(2)\n"
                              f"    pathlib.Path({str(exit_file)!r}).write_text('7\\n')\n"
                              "    sys.exit(7)\n"
                              "signal.signal(signal.SIGTERM, stop)\n"
                              f"pathlib.Path({str(ready)!r}).write_text('1')\n"
                              "time.sleep(60)\n")
        end = time.monotonic() + 5
        while not ready.exists():
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        start_file.write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        shims = self.home / "shims"
        shims.mkdir()
        (shims / "sleep").write_text(f'#!/bin/bash\nprintf "週日\\n" > "{start_file}"\nexec /bin/sleep "$@"\n')
        (shims / "sleep").chmod(0o755)
        result = self.jobs("cancel", env=dict(self.env, PATH=f"{shims}:{os.environ['PATH']}"))
        self.assertEqual((result.returncode, result.stdout), (0, "cancelled (exit 7)\n"), result.stderr)

    def test_control_term_ignoring_worker_gets_sigkill(self):
        ready = self.home / "ready"
        process = self.victim("import pathlib, signal, sys, time\n"
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                              f"pathlib.Path({str(ready)!r}).write_text('1')\n"
                              "time.sleep(60)\n")
        end = time.monotonic() + 5
        while not ready.exists():
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        (self.job / "pid.start").write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_killed_by(process, signal.SIGKILL)


class ProbeTests(IdentityTestCase):
    """Probes that send no signal: a new job compares, an older one keeps kill -0."""

    def reused(self):
        victim = self.victim()
        (self.job / "pid.start").write_text(OTHER_START + "\n")
        (self.job / "pid").write_text(f"{victim.pid}\n")
        return victim

    def test_reused_pid_is_dead_to_status_wait_and_rm(self):
        victim = self.reused()
        status = self.jobs("status")
        self.assertIn("dead (worker gone, no exit recorded)", status.stdout, status.stderr)
        self.assertIn(f"PID {victim.pid} is running, but its start time differs", status.stderr)
        waited = self.jobs("wait")
        self.assertEqual((waited.returncode, waited.stdout.strip()), (125, "dead (worker gone, no exit recorded)"))
        self.assertIn(f"PID {victim.pid} is running, but its start time differs", waited.stderr)
        removed = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "rm", JOB_ID], env=dict(self.env, HOME=str(self.home)),
                                 capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assert_alive(victim)

    def test_matching_pid_is_running(self):
        victim = self.victim()
        (self.job / "pid.start").write_text(start_time(victim.pid) + "\n")
        (self.job / "pid").write_text(f"{victim.pid}\n")
        self.assertIn("running", self.jobs("status").stdout)
        refused = self.jobs("rm")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(f"job is running (pid {victim.pid})", refused.stderr)

    def test_older_job_keeps_kill_0(self):
        victim = self.victim()
        (self.job / "pid").write_text(f"{victim.pid}\n")
        self.assertIn("running", self.jobs("status").stdout)
        self.assertIn(f"job is running (pid {victim.pid})", self.jobs("rm").stderr)
        victim.kill()
        victim.wait(timeout=5)
        self.assertIn("dead (worker gone, no exit recorded)", self.jobs("status").stdout)

    def test_older_job_is_not_reported_dead_when_the_probe_is_refused(self):
        victim = self.victim()
        (self.job / "pid").write_text(f"{victim.pid}\n")
        env = self.refused_probe_env()
        status = self.jobs("status", env=env)
        self.assertNotIn("dead", status.stdout)
        self.assertIn("rerun jobs status outside the sandbox", status.stderr)
        self.assertIn(f"omnilane jobs status {JOB_ID}", status.stderr)
        self.assertIn("This does not mean the process is dead", status.stderr)
        removed = self.jobs("rm", env=env)
        self.assertEqual(removed.returncode, 1)
        self.assertTrue((self.job / "pid").exists())
        self.assert_alive(victim)

    def test_blocked_ps_is_not_reported_dead(self):
        victim = self.reused()
        env = self.blocked_ps_env()
        status = self.jobs("status", env=env)
        self.assertNotIn("dead", status.stdout)
        self.assertIn("rerun jobs status outside the sandbox", status.stderr)
        self.assertIn(f"omnilane jobs status {JOB_ID}", status.stderr)
        waited = self.jobs("wait", env=env)
        self.assertEqual(waited.returncode, 1)
        self.assertIn("rerun jobs wait outside the sandbox", waited.stderr)
        self.assertIn(f"omnilane jobs wait {JOB_ID}", waited.stderr)
        removed = self.jobs("rm", env=env)
        self.assertEqual(removed.returncode, 1)
        self.assertIn("rerun jobs rm outside the sandbox", removed.stderr)
        self.assertIn(f"omnilane jobs rm {JOB_ID}", removed.stderr)
        self.assertTrue((self.job / "pid").exists())
        self.assert_alive(victim)

    def dead_pid(self, start=OTHER_START):
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait(timeout=5)
        if start:
            (self.job / "pid.start").write_text(start + "\n")
        (self.job / "pid").write_text(f"{dead.pid}\n")
        return dead.pid

    def status_json(self, env=None):
        return json.loads(self.jobs("--json", "status", env=env).stdout)["job"]

    def test_a_live_pid_behind_a_blocked_ps_is_unknown(self):
        self.reused()
        env = self.blocked_ps_env()
        status = self.jobs("status", env=env)
        self.assertEqual((status.returncode, status.stdout), (0, "unknown (ps blocked)\n"))
        job = self.status_json(env)
        self.assertEqual((job["state"], job["reason"], job["exit_code"]), ("unknown", "ps blocked", None))

    def jobs_bare(self, *args, env=None):
        return subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), *args], env=env or self.env,
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def test_list_status_running_shows_unknown_marked_as_such(self):
        self.reused()
        env = self.blocked_ps_env()
        listed = self.jobs_bare("list", "--status", "running", env=env)
        self.assertRegex(listed.stdout, rf"{JOB_ID}  unknown  ")
        rows = json.loads(self.jobs_bare("--json", "list", "--status", "running", env=env).stdout)["jobs"]
        self.assertEqual([(row["id"], row["state"]) for row in rows], [(JOB_ID, "unknown")])
        self.assertNotIn(JOB_ID, self.jobs_bare("list", "--status", "dead", env=env).stdout)

    def test_list_status_outside_a_sandbox_reads_as_before(self):
        self.reused()
        self.assertNotIn(JOB_ID, self.jobs_bare("list", "--status", "running").stdout)
        self.assertRegex(self.jobs_bare("list", "--status", "dead").stdout, rf"{JOB_ID}  dead  ")
        victim = self.victim()
        (self.job / "pid.start").write_text(start_time(victim.pid) + "\n")
        (self.job / "pid").write_text(f"{victim.pid}\n")
        self.assertRegex(self.jobs_bare("list", "--status", "running").stdout, rf"{JOB_ID}  running  ")
        rows = json.loads(self.jobs_bare("--json", "list", "--status", "running").stdout)["jobs"]
        self.assertEqual([(row["id"], row["state"]) for row in rows], [(JOB_ID, "running")])

    def test_prune_leaves_unknown_alone(self):
        self.reused()
        env = dict(self.blocked_ps_env(), OMNILANE_TRASH_DIR=str(self.home / "trash"))
        pruned = self.jobs_bare("prune", "--keep", "0", "--apply", env=env)
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertTrue(self.job.is_dir())
        self.assertFalse((self.home / "trash").exists() and any((self.home / "trash").iterdir()))

    def test_unknown_is_no_end_state(self):
        self.reused()
        env = self.blocked_ps_env()
        result = self.jobs("result", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("job state is unknown (ps blocked)", result.stderr)
        self.assertFalse((self.job / "exit").exists())
        recommend = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "--json", "recommend"], env=env,
                                   capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(json.loads(recommend.stdout)["completed"], 0, recommend.stderr)
        self.assertTrue((self.job / "pid").exists())

    def test_a_dead_pid_behind_a_refused_probe_is_unknown(self):
        self.dead_pid()
        env = self.refused_probe_env()
        self.assertEqual(self.jobs("status", env=env).stdout, "unknown (ps blocked)\n")
        self.assertEqual(self.status_json(env)["state"], "unknown")

    def test_a_dead_pid_needs_no_ps_to_be_dead(self):
        for start in (OTHER_START, None):
            with self.subTest(start=start):
                (self.job / "pid.start").unlink(missing_ok=True)
                self.dead_pid(start)
                env = self.blocked_ps_env()
                status = self.jobs("status", env=env)
                self.assertEqual((status.stdout, status.stderr), ("dead (worker gone, no exit recorded)\n", ""))
                self.assertEqual(self.status_json(env)["state"], "dead")
                waited = self.jobs("wait", env=env)
                self.assertEqual(waited.returncode, 125, waited.stderr)
                self.assertEqual(self.jobs("cancel", env=env).stdout, "not running (no live worker to cancel)\n")

    def test_the_same_dead_job_outside_a_sandbox_reads_as_before(self):
        self.dead_pid()
        status = self.jobs("status")
        self.assertEqual((status.stdout, status.stderr), ("dead (worker gone, no exit recorded)\n", ""))
        job = self.status_json()
        self.assertEqual((job["state"], job["reason"]), ("dead", "worker gone, no exit recorded"))

    def counting_ps_env(self):
        shims = self.home / "counting"
        shims.mkdir(exist_ok=True)
        self.ps_log = self.home / "ps-calls"
        (shims / "ps").write_text(f'#!/bin/bash\necho "$*" >> "{self.ps_log}"\nexec /bin/ps "$@"\n')
        (shims / "ps").chmod(0o755)
        return dict(self.env, PATH=f"{shims}:{os.environ['PATH']}")

    def ps_calls(self):
        calls = self.ps_log.read_text().splitlines() if self.ps_log.exists() else []
        self.ps_log.unlink(missing_ok=True)
        return calls

    def test_a_dead_pid_is_answered_without_running_ps(self):
        # jobs list reads every job directory, so one ps per dead job adds up.
        env = self.counting_ps_env()
        for start in (OTHER_START, None, "週日"):
            with self.subTest(start=start):
                (self.job / "pid.start").unlink(missing_ok=True)
                self.dead_pid(start)
                status = self.jobs("status", env=env)
                self.assertEqual((status.stdout, status.stderr), ("dead (worker gone, no exit recorded)\n", ""))
                self.assertEqual(self.jobs("wait", env=env).returncode, 125)
                self.assertEqual(self.jobs("cancel", env=env).stdout, "not running (no live worker to cancel)\n")
                listed = subprocess.run(["/bin/bash", str(ROOT / "scripts/jobs.sh"), "list", "--status", "running"],
                                        env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
                self.assertEqual(listed.returncode, 0, listed.stderr)
                self.assertEqual(self.ps_calls(), [])

    def test_a_dead_holder_at_close_is_answered_without_running_ps(self):
        env = self.counting_ps_env()
        self.dead_pid(OTHER_START)
        pid = (self.job / "pid").read_text()
        self.holder_file.write_text(pid)
        self.holder_start.write_text(OTHER_START + "\n")
        result = self.jobs("close", env=env)
        self.assertEqual((result.returncode, result.stderr), (1, "live inbox holder is gone\n"))
        self.assertEqual(self.ps_calls(), [])

    def test_a_refused_probe_still_asks_ps(self):
        # kill -0 refused for a process that may exist (EPERM): only ps can answer.
        bash_env = self.home / "bash-env"
        bash_env.write_text('kill() { [[ "$1" == "-0" ]] && return 1; builtin kill "$@"; }\n')
        env = dict(self.counting_ps_env(), BASH_ENV=str(bash_env))
        victim = self.victim()
        (self.job / "pid").write_text(f"{victim.pid}\n")
        (self.job / "pid.start").write_text(start_time(victim.pid) + "\n")
        self.assertEqual(self.jobs("status", env=env).stdout.split()[0], "running")
        self.assertTrue(self.ps_calls())
        (self.job / "pid.start").write_text(OTHER_START + "\n")
        self.assertEqual(self.jobs("status", env=env).stdout.split()[0], "dead")
        self.assertTrue(self.ps_calls())
        self.assert_alive(victim)

    def test_a_corrupt_start_file_still_refuses_a_process_that_may_exist(self):
        # Only a gone process skips the start file: reuse protection must not weaken.
        bash_env = self.home / "bash-env"
        bash_env.write_text('kill() { [[ "$1" == "-0" ]] && return 1; builtin kill "$@"; }\n')
        for name, env in (("kill -0 succeeds", self.env), ("kill -0 refused", dict(self.env, BASH_ENV=str(bash_env)))):
            for corrupt in ("週日", "x" * 200):
                with self.subTest(name, corrupt=corrupt[:2]):
                    victim = self.victim()
                    (self.job / "pid").write_text(f"{victim.pid}\n")
                    (self.job / "pid.start").write_text(corrupt + "\n")
                    status = self.jobs("status", env=env)
                    self.assertEqual(status.stdout, "unknown (invalid start time file)\n")
                    self.assertIn("invalid start time", status.stderr)
                    for command in ("wait", "cancel", "rm"):
                        refused = self.jobs(command, env=env)
                        self.assertEqual(refused.returncode, 1, (command, refused.stdout, refused.stderr))
                        self.assertIn("invalid start time", refused.stderr)
                        self.assertIn(f"ps -o lstart=,command= -p {victim.pid}", refused.stderr)
                    self.assertTrue(self.job.is_dir())
                    self.assertFalse((self.job / "exit").exists())
                    self.holder_file.write_text(f"{victim.pid}\n")
                    self.holder_start.write_text(corrupt + "\n")
                    closed = self.jobs("close", env=env)
                    self.assertEqual((closed.returncode, closed.stderr), (1, "live inbox holder is unavailable\n"))
                    self.holder_file.unlink()
                    self.holder_start.unlink()
                    self.assert_alive(victim)
                    victim.kill()

    def test_start_file_removed_after_the_first_look_is_not_invalid(self):
        # The worker removes its PID file and then the start file on the way out.
        victim = self.victim()
        path, start = self.holder_file, self.holder_start
        path.write_text(f"{victim.pid}\n")
        start.write_text(OTHER_START + "\n")
        bash_env = self.home / "bash-env"
        bash_env.write_text(f'kill() {{ /bin/rm -f "{path}" "{start}"; builtin kill "$@"; }}\n')
        result = subprocess.run(["/bin/bash", "-c", 'source "$1/scripts/lib/common.sh"; recorded_pid_identity "$2" "$3"; echo "rc=$?"',
                                 "probe", str(ROOT), str(path), str(victim.pid)], env=dict(self.env, BASH_ENV=str(bash_env)),
                                capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
        self.assertEqual(result.stdout.strip(), "rc=5", result.stderr)

    def test_older_job_on_a_reused_live_pid_stays_running_and_says_how_to_leave(self):
        # No start file, so nothing shows this PID went to another process. Nothing may be signalled or removed.
        victim = self.reused()
        (self.job / "pid.start").unlink()
        self.assertEqual(self.jobs("status").stdout, "running\n")
        removed = self.jobs("rm")
        self.assertEqual((removed.returncode, removed.stderr.strip()), (1, f"job is running (pid {victim.pid}); cancel it first"))
        self.assertTrue(self.job.is_dir())
        cancelled = self.jobs("cancel")
        self.assertEqual(cancelled.returncode, 1)
        self.assertIn("you can move", cancelled.stderr)
        self.assertIn(str(self.job), cancelled.stderr)
        self.assert_alive(victim)

    def test_a_recorded_exit_wins_behind_a_blocked_ps(self):
        victim = self.reused()
        (self.job / "exit").write_text("0\n")
        for env in (self.blocked_ps_env(), self.refused_probe_env()):
            status = self.jobs("status", env=env)
            self.assertEqual((status.stdout, status.stderr), ("done exit=0\n", ""))
            self.assertEqual(self.status_json(env)["state"], "done")
        self.assert_alive(victim)

    def test_blocked_ps_at_close_with_only_a_writer_is_not_gone(self):
        self.reused()
        result = self.jobs("close", env=self.blocked_ps_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertIn(f"omnilane jobs close {JOB_ID}", result.stderr)


class WriterTests(IdentityTestCase):
    def write(self, path, env=None):
        script = f'source "{ROOT}/scripts/lib/common.sh"; write_current_pid_file "{path}"'
        return subprocess.run(["/bin/bash", "-c", script], env=env or self.env, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)

    def test_start_file_is_published_before_the_pid(self):
        shims = self.home / "shims"
        shims.mkdir()
        log = self.home / "mv-log"
        (shims / "mv").write_text(f'#!/bin/bash\nprintf "%s\\n" "${{@: -1}}" >> "{log}"\nexec /bin/mv "$@"\n')
        (shims / "mv").chmod(0o755)
        result = self.write(self.holder_file, dict(self.env, PATH=f"{shims}:{os.environ['PATH']}"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), [str(self.holder_start), str(self.holder_file)])

    def test_files_are_private_and_no_temporary_is_left(self):
        self.assertEqual(self.write(self.holder_file).returncode, 0)
        for path in (self.holder_file, self.holder_start):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.job.iterdir()),
                         ["inbox.holder.pid", "inbox.holder.pid.start", "meta.json"])

    def test_failure_returns_nonzero_gives_the_reason_and_publishes_nothing(self):
        result = self.write(self.holder_file, self.blocked_ps_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not start", result.stderr)
        self.assertEqual(sorted(p.name for p in self.job.iterdir()), ["meta.json", "out.txt.stderr.log"])
        self.assertIn("did not start", (self.job / "out.txt.stderr.log").read_text())


class RunShHintTests(unittest.TestCase):
    """tests/run.sh reports what dispatch would decide, and its result stays the tests' own."""

    HINT = "ps cannot read process start times here, so tests that start a job or check a job's process fail"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-run-hint-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name).resolve()
        self.bins = self.root / "bin"
        self.bins.mkdir()
        (self.root / "tests").mkdir()
        (self.root / "scripts").symlink_to(ROOT / "scripts")
        self.env, violations = offline_env.isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.env.update(PYTHONDONTWRITEBYTECODE="1", PATH=str(self.bins) + os.pathsep + self.env["PATH"])

    def run_sh(self, failures):
        # The real head and tail of run.sh, with the cases between them replaced by a known count.
        lines = (ROOT / "tests/run.sh").read_text().splitlines(keepends=True)
        end = next(i for i, line in enumerate(lines) if self.HINT in line) + 2
        script = self.root / "tests/run.sh"
        script.write_text("".join(lines[:end]) + f"FAIL={failures}\n" + "".join(lines[-2:]))
        # Past the re-exec: the launcher rebuilds PATH, which would drop the blocked ps.
        return subprocess.run(["bash", str(script), "--omnilane-offline-child"], env=self.env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def dispatch(self):
        home = self.root / "home"
        home.mkdir(exist_ok=True)
        gate = self.bins / "fake-exec"
        gate.write_text('#!/usr/bin/env bash\nprintf "ran\\n" > "$5"\n')
        gate.chmod(0o755)
        (home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        return subprocess.run(["bash", str(ROOT / "bin/omnilane"), "dispatch", "--workdir", str(self.root), "fixture", "offline"],
                              env=dict(self.env, OMNILANE_HOME=str(home)), capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def block_ps(self):
        real = shutil.which("ps", path=os.environ["PATH"])
        (self.bins / "ps").write_text(f'#!/usr/bin/env bash\nif [[ "$*" == *lstart=* ]]; then exit 126; fi\nexec {real} "$@"\n')
        (self.bins / "ps").chmod(0o755)

    def test_silent_where_jobs_can_start(self):
        for failures, code in ((0, 0), (3, 1)):
            with self.subTest(failures=failures):
                result = self.run_sh(failures)
                self.assertNotIn(self.HINT, result.stdout + result.stderr)
                self.assertEqual(result.returncode, code)
                self.assertIn(f"0 passed, {failures} failed", result.stdout)
        self.assertEqual(self.dispatch().returncode, 0)

    def test_reported_where_jobs_cannot_start_and_the_result_is_unchanged(self):
        self.block_ps()
        for failures, code in ((0, 0), (3, 1)):
            with self.subTest(failures=failures):
                result = self.run_sh(failures)
                self.assertEqual(result.stdout.count(self.HINT), 1, result.stdout + result.stderr)
                self.assertEqual(result.returncode, code)
                self.assertIn(f"0 passed, {failures} failed", result.stdout)
        refused = self.dispatch()
        self.assertEqual(refused.returncode, 1, refused.stderr)
        self.assertIn("no job was started", refused.stderr)

    def test_the_hint_makes_no_judgement(self):
        self.block_ps()
        output = self.run_sh(0).stdout.lower()
        for word in ("ignore", "expected", "safe", "harmless"):
            self.assertNotIn(word, output)


class DispatchFailureTests(unittest.TestCase):
    """A job that cannot record its start time fails with a reason and a whole record."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-pid-identity-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.env, violations = offline_env.isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.bins = self.root / "bin"
        self.home.mkdir()
        self.bins.mkdir()
        self.env.update(OMNILANE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1",
                        PATH=str(self.bins) + os.pathsep + self.env["PATH"])
        gate = self.bins / "fake-exec"
        self.ran = self.root / "ran"
        gate.write_text(f'#!/usr/bin/env bash\n: > "{self.ran}"\nprintf "ran\\n" > "$5"\n')
        gate.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')

    def shim_ps(self, condition):
        # A ps that fails only the start-time reads this module takes, and only once `condition` holds.
        real = shutil.which("ps", path=os.environ["PATH"])
        (self.bins / "ps").write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            if [[ "$TZ" == "UTC" && "$*" == *lstart=* ]] && {condition}; then exit 126; fi
            exec {real} "$@"
        """))
        (self.bins / "ps").chmod(0o755)

    def cli(self, *args):
        return subprocess.run(["bash", str(ROOT / "bin/omnilane"), *args], env=self.env,
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def jobs_dirs(self):
        jobs = self.home / "jobs"
        return sorted(jobs.iterdir()) if jobs.exists() else []

    def dispatch(self, *flags):
        return self.cli("dispatch", "--workdir", str(self.root), *flags, "fixture", "Print a harmless fixed offline diagnostic")

    def await_exit(self, job):
        end = time.monotonic() + 15
        while not (job / "exit").exists():
            self.assertLess(time.monotonic(), end, "the job never recorded an exit")
            time.sleep(0.05)

    def test_no_usable_ps_refuses_before_any_job_exists(self):
        self.shim_ps("true")
        for flags in ((), ("--background",)):
            with self.subTest(flags=flags):
                result = self.dispatch(*flags)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("no job was started", result.stderr)
                self.assertIn("rerun outside the sandbox (from a normal terminal", result.stderr)
                self.assertEqual(self.jobs_dirs(), [])
        self.assertFalse(self.ran.exists())

    def test_foreground_write_failure_records_a_failed_job(self):
        self.shim_ps(f'compgen -G "{self.home}/jobs/*" >/dev/null')
        result = self.dispatch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not start", result.stderr)
        [job] = self.jobs_dirs()
        self.assertEqual((job / "exit").read_text(), "1\n")
        self.assertFalse((job / "pid").exists())
        self.assertFalse((job / "pid.start").exists())
        self.assertFalse(self.ran.exists())

    def test_background_write_failure_records_a_failed_job_with_its_reason(self):
        self.shim_ps(f'compgen -G "{self.home}/jobs/*" >/dev/null')
        result = self.dispatch("--background")
        self.assertEqual(result.returncode, 0, result.stderr)
        [job] = self.jobs_dirs()
        self.await_exit(job)
        self.assertEqual((job / "exit").read_text(), "1\n")
        self.assertFalse((job / "pid").exists())
        self.assertFalse(self.ran.exists())
        env = dict(self.env)
        shown = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), "result", job.name], env=env,
                               capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        self.assertEqual(shown.returncode, 1)
        self.assertIn("did not start", shown.stderr)


class HungPsTests(IdentityTestCase):
    # A ps that never answers must not hold a job or a jobs command for as long as it hangs.
    WITHIN = 14

    def hung_ps_env(self, script="exec /bin/sleep 30"):
        shims = self.home / "hung"
        shims.mkdir(exist_ok=True)
        (shims / "ps").write_text(f"#!/bin/bash\n{script}\n")
        (shims / "ps").chmod(0o755)
        return dict(self.env, PATH=f"{shims}:{os.environ['PATH']}")

    def timed(self, *args, env=None):
        began = time.monotonic()
        result = self.jobs(*args, env=env or self.hung_ps_env(), timeout=45)
        return result, time.monotonic() - began

    def live_job(self):
        victim = self.victim()
        (self.job / "pid.start").write_text(start_time(victim.pid) + "\n")
        (self.job / "pid").write_text(f"{victim.pid}\n")
        return victim

    def test_status_answers_unknown(self):
        victim = self.live_job()
        result, took = self.timed("status")
        self.assertEqual(result.stdout, "unknown (ps blocked)\n", result.stderr)
        self.assertLess(took, self.WITHIN)
        self.assert_alive(victim)

    def test_cancel_refuses_without_signalling(self):
        victim = self.live_job()
        result, took = self.timed("cancel")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("rerun jobs cancel outside the sandbox", result.stderr)
        self.assertLess(took, self.WITHIN)
        self.assert_alive(victim)

    def test_close_with_a_holder_refuses_without_signalling(self):
        victim = self.victim()
        self.holder_start.write_text(start_time(victim.pid) + "\n")
        self.holder_file.write_text(f"{victim.pid}\n")
        result, took = self.timed("close")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertLess(took, self.WITHIN)
        self.assert_alive(victim)

    def test_close_with_a_late_holder_refuses_within_its_deadline(self):
        victim = self.victim()
        start = start_time(victim.pid)

        def publish():
            time.sleep(1)
            self.holder_start.write_text(start + "\n")
            self.holder_file.write_text(f"{victim.pid}\n")

        thread = threading.Thread(target=publish)
        thread.start()
        self.addCleanup(thread.join)
        result, took = self.timed("close")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertLess(took, self.WITHIN)
        self.assert_alive(victim)

    def common(self, script, *args, env=None):
        began = time.monotonic()
        result = subprocess.run(["/bin/bash", "-c", f'source "$1/scripts/lib/common.sh"; {script}', "probe", str(ROOT), *args],
                                env=env or self.hung_ps_env(), capture_output=True, text=True, timeout=45,
                                stdin=subprocess.DEVNULL)
        return result, time.monotonic() - began

    def test_publishing_a_pid_file_gives_up_and_says_ps_timed_out(self):
        path = self.job / "published.pid"
        result, took = self.common('PS_START_LIMIT=2; write_current_pid_file "$2"', str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ps did not answer within 2s", result.stderr)
        self.assertLess(took, 8)
        self.assertEqual(sorted(p.name for p in self.job.glob("published*")), [])

    def test_the_start_path_waits_longer_than_a_query(self):
        result, _ = self.common('printf "%s %s" "$PS_QUERY_LIMIT" "$PS_START_LIMIT"', env=self.env)
        self.assertEqual(result.stdout, "5 30")

    def test_dispatch_check_reports_a_timeout(self):
        result, took = self.common('PS_START_LIMIT=2; can_record_start_time; echo "$?"')
        self.assertEqual(result.stdout.strip(), "142")
        self.assertLess(took, 8)

    def test_an_inherited_ignored_alarm_does_not_lift_the_limit(self):
        result, took = self.common('trap "" ALRM; pid_file_start_time "$$" 2; echo "$?"')
        self.assertEqual(result.stdout.strip(), "142")
        self.assertLess(took, 8)
        victim = self.victim()
        start = start_time(victim.pid)
        threading.Timer(1, lambda: (self.holder_start.write_text(start + "\n"),
                                    self.holder_file.write_text(f"{victim.pid}\n"))).start()
        began = time.monotonic()
        closed = subprocess.run(["/bin/bash", "-c", 'trap "" ALRM; exec /bin/bash "$1" close "$2"', "probe",
                                 str(ROOT / "scripts/jobs.sh"), JOB_ID], env=self.hung_ps_env(), capture_output=True,
                                text=True, timeout=45, stdin=subprocess.DEVNULL)
        self.assertEqual(closed.returncode, 1, closed.stderr)
        self.assertLess(time.monotonic() - began, self.WITHIN)
        self.assert_alive(victim)

    def test_without_perl_ps_still_answers_with_no_limit(self):
        bare = self.home / "bare"
        bare.mkdir()
        for tool in ("ps", "cat", "mv", "rm", "sh"):
            (bare / tool).symlink_to(shutil.which(tool))
        path = self.job / "published.pid"
        result, _ = self.common('PATH="$3"; pid_file_start_time "$$"; write_current_pid_file "$2"; echo "rc=$?"',
                                str(path), str(bare), env=self.env)
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], (self.job / "published.pid.start").read_text().strip())
        self.assertTrue(path.exists())

    def test_a_perl_that_cannot_run_still_lets_ps_answer(self):
        bash_env = self.home / "bash-env"
        bash_env.write_text("perl() { return 127; }\n")
        result, _ = self.common('pid_file_start_time "$$"; echo "rc=$?"', env=dict(self.env, BASH_ENV=str(bash_env)))
        self.assertEqual(len(result.stdout.splitlines()), 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[1], "rc=0")

    def test_a_process_gone_between_the_two_probes_is_gone_behind_a_blocked_ps(self):
        marker = self.home / "probed"
        bash_env = self.home / "bash-env"
        bash_env.write_text(f'''kill() {{
  [[ "$1" == "-0" ]] || {{ builtin kill "$@"; return; }}
  if [[ -e "{marker}" ]]; then echo "kill: No such process" >&2; else : > "{marker}"; echo "kill: Operation not permitted" >&2; fi
  return 1
}}
''')
        self.holder_file.write_text("12345\n")
        self.holder_start.write_text(OTHER_START + "\n")
        env = dict(self.hung_ps_env("exit 126"), BASH_ENV=str(bash_env))
        result, _ = self.common('recorded_pid_identity "$2" 12345; echo "rc=$?"', str(self.holder_file), env=env)
        self.assertEqual(result.stdout.strip(), "rc=1", result.stderr)

    def test_a_missing_or_blocked_ps_fails_the_same_way_as_before(self):
        only_perl = self.home / "only-perl"
        only_perl.mkdir()
        (only_perl / "perl").symlink_to(shutil.which("perl"))
        result, took = self.common('PATH="$2"; pid_file_start_time "$$"; echo "rc=$?"', str(only_perl), env=self.env)
        self.assertEqual(result.stdout.strip(), "rc=126")
        self.assertLess(took, 3)
        blocked = self.hung_ps_env("exit 126")
        result, _ = self.common('pid_file_start_time "$$"; echo "rc=$?"', env=blocked)
        self.assertEqual(result.stdout.strip(), "rc=126")

    def target_only_ps_env(self, pid, script):
        return self.hung_ps_env(f'[[ "$*" != *" -p {pid}" ]] || {{ {script}; }}\nexec /bin/ps "$@"')

    def test_ps_failing_on_the_target_alone_says_nothing_about_it(self):
        # ps answers for every other process, so asking about this shell would call the job dead.
        for name, script in (("times out", "exec /bin/sleep 30"), ("refused", "exit 1")):
            with self.subTest(name):
                victim = self.live_job()
                env = self.target_only_ps_env(victim.pid, script)
                result, took = self.timed("status", env=env)
                self.assertEqual(result.stdout, "unknown (ps blocked)\n", result.stderr)
                self.assertLess(took, self.WITHIN)
                for command in ("wait", "cancel", "rm"):
                    result, _ = self.timed(command, env=env)
                    self.assertEqual(result.returncode, 1, (command, result.stdout, result.stderr))
                    self.assertIn(f"rerun jobs {command} outside the sandbox", result.stderr)
                    self.assertTrue(self.job.is_dir(), command)
                    self.assertFalse((self.job / "exit").exists(), command)
                self.assert_alive(victim)
                victim.kill()

    def test_close_when_ps_fails_on_the_target_alone(self):
        for name, late in (("present at start", False), ("appears during the poll", True)):
            with self.subTest(name):
                victim = self.victim()
                start = start_time(victim.pid)

                def publish():
                    self.holder_start.write_text(start + "\n")
                    self.holder_file.write_text(f"{victim.pid}\n")

                if late:
                    timer = threading.Timer(1, publish)
                    timer.start()
                    self.addCleanup(timer.join)
                else:
                    publish()
                result, took = self.timed("close", env=self.target_only_ps_env(victim.pid, "exec /bin/sleep 30"))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("rerun jobs close outside the sandbox", result.stderr)
                self.assertLess(took, self.WITHIN)
                self.assert_alive(victim)
                victim.kill()
                self.holder_file.unlink()
                self.holder_start.unlink()

    def test_a_perl_found_but_not_executable_still_lets_ps_answer(self):
        # bash answers 126 here, which is also what a ps that cannot be executed answers.
        shims = self.home / "bad-perl"
        shims.mkdir()
        (shims / "perl").write_text("#!/nonexistent/interpreter\n")
        (shims / "perl").chmod(0o755)
        result, _ = self.common('pid_file_start_time "$$"; echo "rc=$?"',
                                env=dict(self.env, PATH=f"{shims}:{os.environ['PATH']}"))
        self.assertEqual(len(result.stdout.splitlines()), 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[1], "rc=0")

    def test_any_other_failure_of_ps_comes_back_unchanged(self):
        counted = self.home / "ps-calls"
        env = self.hung_ps_env(f'echo called >> "{counted}"\nexit 23')
        result, _ = self.common('pid_file_start_time "$$"; echo "rc=$?"', env=env)
        self.assertEqual(result.stdout.strip(), "rc=23")
        self.assertEqual(counted.read_text().count("called"), 1)

    def test_a_slow_ps_within_the_limit_still_answers(self):
        victim = self.live_job()
        result, _ = self.timed("status", env=self.hung_ps_env('/bin/sleep 1\nexec /bin/ps "$@"'))
        self.assertEqual(result.stdout.split()[0], "running", result.stderr)
        self.assert_alive(victim)


if __name__ == "__main__":
    unittest.main()
