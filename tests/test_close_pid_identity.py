"""jobs close and cancel must not signal a process that reused a recorded PID.

Each victim is a real process the test started, with no USR1 handler, so a signal
that gets out ends it. Nothing intercepts kill: the victim surviving is the proof.
Each refusal has a control where the same victim, correctly identified, is hit.
"""
import offline_env  # noqa: F401
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
        self.assertNotIn("gone", result.stderr)
        self.assert_alive(victim)

    def test_blocked_ps_does_not_call_a_dead_pid_gone(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        dead.wait(timeout=5)
        self.holder_start.write_text(OTHER_START + "\n")
        self.holder_file.write_text(f"{dead.pid}\n")
        result = self.jobs("close", env=self.blocked_ps_env())
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assertNotIn("gone", result.stderr)

    def test_older_job_is_not_called_gone_when_the_probe_is_refused(self):
        victim = self.victim()
        self.holder_file.write_text(f"{victim.pid}\n")
        result = self.jobs("close", env=self.refused_probe_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
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
        self.assert_alive(victim)

    def test_older_job_is_not_called_gone_when_the_probe_is_refused(self):
        victim = self.victim()
        lib = self.home / "fake-perl" / "Time"
        lib.mkdir(parents=True)
        hires = FAKE_HIRES.replace('return CORE::kill($_[0], @_[1 .. $#_]) unless $ENV{FAKE_KILL_LOG};',
                                   'return 0 if $_[0] eq "0"; return CORE::kill($_[0], @_[1 .. $#_]);')
        self.assertNotEqual(hires, FAKE_HIRES)
        (lib / "HiRes.pm").write_text(hires)
        env = dict(self.blocked_ps_env(), PERL5LIB=str(self.home / "fake-perl"), FAKE_DEADLINE="11",
                   FAKE_READ_ACTION="", FAKE_HOLDER=str(self.holder_file),
                   FAKE_EVENTS=f"1:{self.holder_file}:{victim.pid}\n")
        result = self.jobs("close", env=env, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(victim.pid), result.stderr)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)
        self.assert_alive(victim)

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
        self.assert_alive(process)
        self.assertTrue(self.child_alive(child))

    def test_older_job_is_not_called_dead_when_the_probe_is_refused(self):
        process, child = self.group_victim()
        (self.job / "pid").write_text(f"{process.pid}\n")
        result = self.jobs("cancel", env=self.refused_probe_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs cancel outside the sandbox", result.stderr)
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
        waited = self.jobs("wait")
        self.assertEqual((waited.returncode, waited.stdout.strip()), (125, "dead (worker gone, no exit recorded)"))
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
        waited = self.jobs("wait", env=env)
        self.assertEqual(waited.returncode, 1)
        self.assertIn("rerun jobs wait outside the sandbox", waited.stderr)
        removed = self.jobs("rm", env=env)
        self.assertEqual(removed.returncode, 1)
        self.assertIn("rerun jobs rm outside the sandbox", removed.stderr)
        self.assertTrue((self.job / "pid").exists())
        self.assert_alive(victim)

    def test_blocked_ps_at_close_with_only_a_writer_is_not_gone(self):
        self.reused()
        result = self.jobs("close", env=self.blocked_ps_env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("rerun jobs close outside the sandbox", result.stderr)


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
                self.assertIn("rerun outside the sandbox", result.stderr)
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


if __name__ == "__main__":
    unittest.main()
