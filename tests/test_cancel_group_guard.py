"""jobs cancel signals a process group only when that group is the job's own.

A foreground dispatch runs in its caller's process group. Signalling that group
used to end the caller, its other work and anything piped to the dispatch.
Every fixture here is a real dispatch with an offline exec runner.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from offline_env import isolated_environment
from test_close_holder_early import start_time


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts/dispatch.sh"
# dispatch may spend up to 30s reading a start time (PS_START_LIMIT in common.sh).
STARTUP_WAIT = 45
JOBS = ROOT / "scripts/jobs.sh"

GATE = """import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT'])
child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'])
ready = root / 'gate.started'
ready.with_suffix('.tmp').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'child': child.pid}))
ready.with_suffix('.tmp').replace(ready)
signal.pause()
"""


class CancelGroupGuardTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="omnilane-cancel-guard-", dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env, self.violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        gate = self.root / "gate"
        gate.write_text("#!/usr/bin/env python3\n" + GATE, encoding="utf-8")
        gate.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'fixture: exec "{gate}" -\n')
        self.env.update(OMNILANE_HOME=str(self.home), FIXTURE_ROOT=str(self.root))
        self.groups = []
        self.callers = []
        self.addCleanup(self.stop_fixture_groups)

    def stop_fixture_groups(self):
        groups = list(self.groups)
        for pid_file in (self.home / "jobs").glob("*/pid"):
            try:
                pid = int(pid_file.read_text())
                if os.getpgid(pid) == pid:
                    groups.append(pid)
            except (OSError, ValueError):
                pass
        if (self.root / "gate.started").exists():
            groups.append(json.loads((self.root / "gate.started").read_text())["pgid"])
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pgid in groups:
                # Only groups this fixture started, and still led by that process, may be signalled.
                if pgid != os.getpgrp() and self.fixture_leader(pgid):
                    try:
                        os.killpg(pgid, sig)
                    except (ProcessLookupError, PermissionError):
                        pass
            time.sleep(0.1)
        for caller in self.callers:
            caller.wait(10)

    def fixture_leader(self, pgid):
        """A reaped group's number may already lead somebody else's group."""
        try:
            if os.getpgid(pgid) != pgid:
                return False
        except (ProcessLookupError, PermissionError):
            return False
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pgid)], env=self.env, text=True,
                                 capture_output=True, timeout=5).stdout
        return str(self.root) in command

    def wait_for(self, path, timeout=None):
        timeout = STARTUP_WAIT if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size:
                return
            time.sleep(0.02)
        errors = self.root / "caller.err"
        self.fail("fixture did not reach " + path.name + (": " + errors.read_text() if errors.exists() else ""))

    def start_caller(self, body, shell=("bash",), script=True):
        """Start a caller that leads a process group, as a terminal or an agent tool shell does."""
        # The fixture root in the source lets cleanup recognise this caller as the group's leader.
        prologue = (f'# fixture {self.root}\n'
                    'printf \'%s\\n\' "$$" > "$FIXTURE_ROOT/caller.pid"\n'
                    'sleep 60 &\n'
                    'printf \'%s\\n\' "$!" > "$FIXTURE_ROOT/sibling.pid"\n')
        # Foreground dispatch can itself become a leader (set -m); give its
        # argv this fixture's unique root too, not just the shared repo path.
        body = body.replace('bash "$DISPATCH"', 'bash "$DISPATCH" --workdir "$FIXTURE_ROOT"')
        source = prologue + body + '\nprintf \'%s\\n\' "$?" > "$FIXTURE_ROOT/caller.rc"\nwait\n'
        if script:
            path = self.root / "caller.sh"
            path.write_text("#!/bin/bash\n" + source)
            path.chmod(0o755)
            argv = [str(path)]
        else:
            argv = [*shell, "-c", source]
        errors = (self.root / "caller.err").open("w")
        self.addCleanup(errors.close)
        caller = subprocess.Popen(argv, env=dict(self.env, DISPATCH=str(DISPATCH)), start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
        self.groups.append(caller.pid)
        self.callers.append(caller)
        self.wait_for(self.root / "gate.started")
        return caller

    def job_dir(self):
        jobs = list((self.home / "jobs").iterdir())
        self.assertEqual(len(jobs), 1, jobs)
        self.wait_for(jobs[0] / "pid")
        return jobs[0]

    def cancel(self, job_dir, *flags, env=None):
        return subprocess.run(["bash", str(JOBS), *flags, "cancel", job_dir.name], env=env or self.env,
                              text=True, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)

    @staticmethod
    def snapshot(job_dir):
        state = {}
        for path in sorted(job_dir.rglob("*")):
            info = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
            state[str(path.relative_to(job_dir))] = (info.st_mode, info.st_size, info.st_mtime_ns, digest)
        return state

    def alive(self, pid):
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], env=self.env, text=True,
                                capture_output=True, timeout=5)
        return result.returncode == 0 and not result.stdout.strip().startswith("Z")

    def read_pid(self, name):
        return int((self.root / name).read_text())

    def assert_gone(self, pid, timeout=5):
        deadline = time.monotonic() + timeout
        while self.alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.alive(pid), f"process {pid} survived cancellation")

    def assert_refused(self, job_dir, code, *, env=None):
        gate = json.loads((self.root / "gate.started").read_text())
        pid = int((job_dir / "pid").read_text())
        before = self.snapshot(job_dir)
        text = self.cancel(job_dir, env=env)
        self.assertEqual(text.returncode, 1, text.stdout + text.stderr)
        self.assertEqual(text.stdout, "")
        self.assertIn(f"PID {pid}", text.stderr)
        self.assertIn("no signal was sent", text.stderr)
        if code != "process-list-unavailable":
            self.assertIn("End the job from its caller.", text.stderr)
        self.assertNotIn("kill", text.stderr)
        result = self.cancel(job_dir, "--json", env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual((reply["ok"], reply["code"], reply["signal_sent"], reply["job"]),
                         (False, code, False, {"id": job_dir.name, "pid": pid}))
        self.assertEqual(reply["reason"], text.stderr.rstrip("\n"))
        time.sleep(0.3)
        self.assertEqual(self.snapshot(job_dir), before)
        for process in (pid, gate["pid"], gate["child"]):
            self.assertTrue(self.alive(process), f"refused cancel stopped process {process}")
        return pid

    def assert_caller_untouched(self):
        for name in ("caller.pid", "sibling.pid"):
            self.assertTrue(self.alive(self.read_pid(name)), name + " was signalled")
        self.assertFalse((self.root / "caller.rc").exists())

    def test_foreground_dispatch_from_a_script_is_refused(self):
        self.start_caller('bash "$DISPATCH" fixture "script caller"')
        self.assert_refused(self.job_dir(), "unsupervised")
        self.assert_caller_untouched()

    def test_foreground_dispatch_from_a_tool_shell_is_refused(self):
        self.start_caller('bash "$DISPATCH" fixture "tool shell caller"', script=False)
        self.assert_refused(self.job_dir(), "unsupervised")
        self.assert_caller_untouched()

    def test_foreground_dispatch_leading_its_own_group_without_a_supervisor_is_refused(self):
        self.start_caller('set -m\nbash "$DISPATCH" fixture "alone"')
        job_dir = self.job_dir()
        pid = int((job_dir / "pid").read_text())
        self.assertEqual(os.getpgid(pid), pid)
        self.groups.append(pid)
        self.assert_refused(job_dir, "unsupervised")
        self.assert_caller_untouched()

    def test_job_timeout_in_the_caller_group_is_refused(self):
        self.start_caller('bash "$DISPATCH" --job-timeout 600 fixture "shared group"')
        job_dir = self.job_dir()
        self.assertTrue((job_dir / "supervised").is_file())
        self.assert_refused(job_dir, "shared-process-group")
        self.assert_caller_untouched()

    def test_job_timeout_leading_its_own_group_is_cancelled_and_the_caller_survives(self):
        self.start_caller('set -m\nbash "$DISPATCH" --job-timeout 600 fixture "own group"')
        job_dir = self.job_dir()
        pid = int((job_dir / "pid").read_text())
        self.assertEqual(os.getpgid(pid), pid)
        self.groups.append(pid)
        gate = json.loads((self.root / "gate.started").read_text())
        result = self.cancel(job_dir, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual((reply["ok"], reply["job"]["outcome"]), (True, "cancelled"))
        self.assertIn(reply["job"]["exit_code"], (137, 143))
        for process in (pid, gate["pid"], gate["child"]):
            self.assert_gone(process)
        self.wait_for(self.root / "caller.rc")
        self.assertIn(int((self.root / "caller.rc").read_text()), (137, 143))
        for name in ("caller.pid", "sibling.pid"):
            self.assertTrue(self.alive(self.read_pid(name)), name + " was signalled")

    def test_a_pipeline_led_by_the_dispatch_is_refused(self):
        # set -m makes the dispatch lead the pipeline's group, so pgid equals its PID,
        # but tee shares that group without descending from it.
        self.start_caller('set -m\nbash "$DISPATCH" --job-timeout 600 fixture "piped" '
                          '| tee "$FIXTURE_ROOT/tee.out" >/dev/null')
        job_dir = self.job_dir()
        pid = int((job_dir / "pid").read_text())
        self.assertEqual(os.getpgid(pid), pid)
        self.groups.append(pid)
        listing = subprocess.run(["ps", "-axo", "pid=,pgid=,command="], env=self.env, text=True,
                                 capture_output=True, timeout=5).stdout
        tee = [int(line.split()[0]) for line in listing.splitlines()
               if line.split()[1] == str(pid) and " tee " in f" {line} "]
        self.assertEqual(len(tee), 1, listing)
        self.assert_refused(job_dir, "shared-process-group")
        self.assertTrue(self.alive(tee[0]), "tee was signalled")
        self.assert_caller_untouched()

    def background(self):
        dispatched = subprocess.run(["bash", str(DISPATCH), "--background", "--single-shot", "fixture", "background"],
                                    env=self.env, text=True, capture_output=True, timeout=STARTUP_WAIT, stdin=subprocess.DEVNULL)
        self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        job_dir = self.home / "jobs" / dispatched.stdout.strip()
        self.wait_for(job_dir / "pid")
        pid = int((job_dir / "pid").read_text())
        self.groups.append(pid)
        self.wait_for(self.root / "gate.started")
        return job_dir, pid

    def test_background_job_is_cancelled_with_its_whole_tree(self):
        job_dir, pid = self.background()
        gate = json.loads((self.root / "gate.started").read_text())
        result = self.cancel(job_dir, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        # 143 when the worker's TERM trap records it; 137 when a loaded host needs the SIGKILL.
        self.assertIn(reply["job"].pop("exit_code"), (137, 143))
        self.assertEqual(reply, {"schema_version": 1, "command": "cancel", "ok": True,
                                 "job": {"id": job_dir.name, "outcome": "cancelled"}})
        for process in (pid, gate["pid"], gate["child"]):
            self.assert_gone(process)
        again = self.cancel(job_dir, "--json")
        self.assertEqual((again.returncode, json.loads(again.stdout)["job"]["outcome"]), (0, "already_finished"))

    def test_background_job_without_the_marker_is_refused(self):
        job_dir, _ = self.background()
        os.replace(job_dir / "supervised", self.root / "supervised.moved")
        self.assert_refused(job_dir, "unsupervised")

    def test_an_unusable_process_list_is_refused(self):
        job_dir, _ = self.background()
        shims = self.root / "shims"
        shims.mkdir()
        real_ps = Path(self.env["PATH"].split(os.pathsep)[0]) / "ps"
        (shims / "ps").write_text(f'#!/bin/bash\n[[ " $* " == *" -axo "* ]] && exit 1\nexec "{real_ps}" "$@"\n')
        (shims / "ps").chmod(0o755)
        env = dict(self.env, PATH=f"{shims}{os.pathsep}{self.env['PATH']}")
        self.assert_refused(job_dir, "process-list-unavailable", env=env)

    def shimmed(self, name, source):
        shims = self.root / "shims"
        shims.mkdir(exist_ok=True)
        (shims / name).write_text("#!/bin/bash\n" + source)
        (shims / name).chmod(0o755)
        return dict(self.env, PATH=f"{shims}{os.pathsep}{self.env['PATH']}")

    def test_a_perl_that_cannot_run_the_bounded_listing_is_refused(self):
        # Without the perl alarm there is no limit on ps, so the listing is not attempted.
        job_dir, _ = self.background()
        real_perl = Path(self.env["PATH"].split(os.pathsep)[0]) / "perl"
        for status in (126, 127):
            with self.subTest(status=status):
                env = self.shimmed("perl", f'[[ " $* " == *" -axo "* ]] && exit {status}\nexec "{real_perl}" "$@"\n')
                self.assert_refused(job_dir, "process-list-unavailable", env=env)

    def test_a_listing_that_hangs_is_refused_within_its_limit(self):
        job_dir, _ = self.background()
        real_ps = Path(self.env["PATH"].split(os.pathsep)[0]) / "ps"
        env = self.shimmed("ps", f'[[ " $* " == *" -axo "* ]] && exec sleep 60\nexec "{real_ps}" "$@"\n')
        started = time.monotonic()
        self.assert_refused(job_dir, "process-list-unavailable", env=env)
        # Two cancels (text and --json), each bounded by the 5s ps limit.
        self.assertLess(time.monotonic() - started, 25)


class GroupChangeTests(unittest.TestCase):
    """A process that joins the worker's group after TERM must not receive the SIGKILL."""

    # The worker ignores TERM so cancel escalates. Its child moves into a group of
    # its own and forks the outsider, then exits: the outsider is reparented and no
    # longer descends from the worker. Once TERM arrives it joins the worker's group.
    # The middle process's PID is taken before the fork: it may exit before the
    # outsider first runs, and getppid() would then already name the adopter.
    WORKER = """import os, signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
def on_term(*_):
    (root / 'term.seen').write_text('1')
signal.signal(signal.SIGTERM, on_term)
group = os.getpgrp()
middle = os.fork()
if middle == 0:
    os.setpgid(0, 0)
    parent = os.getpid()
    if os.fork() == 0:
        published = root / 'outsider.pid'
        published.with_suffix('.tmp').write_text(str(os.getpid()))
        published.with_suffix('.tmp').replace(published)
        deadline = time.monotonic() + 60
        while os.getppid() == parent:
            if time.monotonic() > deadline:
                os._exit(1)
            time.sleep(0.01)
        (root / 'outsider.ready').write_text('1')
        while not (root / 'term.seen').exists():
            if time.monotonic() > deadline:
                os._exit(1)
            time.sleep(0.01)
        os.setpgid(0, group)
        (root / 'outsider.joined').write_text('1')
        time.sleep(60)
        os._exit(0)
    os._exit(0)
os.waitpid(middle, 0)
(root / 'ready').write_text('1')
while True:
    time.sleep(60)
"""

    def setUp(self):
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="cancel-group-change-", dir=scratch))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = self.root / "jobs" / "20261103-170000-12345-1"
        self.job.mkdir(parents=True)
        (self.job / "meta.json").write_text('{"lane":"triage","vendor":"codex","session_mode":"live"}')
        (self.job / "supervised").write_text("1\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_")}
        self.env["OMNILANE_HOME"] = str(self.root)

    def test_a_process_that_joins_the_group_after_term_gets_no_sigkill(self):
        worker = subprocess.Popen([sys.executable, "-c", self.WORKER, str(self.root)],
                                  start_new_session=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self.stop, worker)
        deadline = time.monotonic() + STARTUP_WAIT
        while not ((self.root / "ready").exists() and (self.root / "outsider.ready").exists()):
            self.assertLess(time.monotonic(), deadline, "fixture did not start")
            time.sleep(0.02)
        outsider = int((self.root / "outsider.pid").read_text())
        self.assertNotEqual(os.getpgid(outsider), worker.pid)
        (self.job / "pid.start").write_text(start_time(worker.pid) + "\n")
        (self.job / "pid").write_text(f"{worker.pid}\n")
        result = subprocess.run(["/bin/bash", str(JOBS), "--json", "cancel", self.job.name], env=self.env,
                                capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertTrue((self.root / "outsider.joined").exists(), "the outsider never joined the group")
        time.sleep(0.3)
        self.assertTrue(self.alive(outsider), "the outsider received the SIGKILL: " + result.stdout + result.stderr)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual((reply["ok"], reply["code"], reply["signal_sent"]), (False, "group-changed", True))
        self.assertIn(str(outsider), reply["reason"])
        self.assertIn("no SIGKILL was sent", reply["reason"])
        self.assertIsNone(worker.poll(), "the worker received SIGKILL")
        self.assertFalse((self.job / "exit").exists())

    @staticmethod
    def alive(pid):
        result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                                timeout=5, stdin=subprocess.DEVNULL)
        return result.returncode == 0 and not result.stdout.strip().startswith("Z")

    def stop(self, worker):
        # The outsider publishes its PID before it waits for anything, so a fixture
        # that never became ready is stopped as well.
        pids = [worker.pid]
        try:
            pids.append(int((self.root / "outsider.pid").read_text()))
        except (OSError, ValueError):
            pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        worker.wait(5)


class ListingShapeTests(unittest.TestCase):
    """What the process list says about the worker, shaped by a ps shim for -axo only."""

    SHIM = """#!/bin/bash
case " $* " in *" -axo "*) ;; *) exec /bin/ps "$@" ;; esac
count=$(( $(cat "$SHIM_COUNT" 2>/dev/null || echo 0) + 1 ))
printf '%s\\n' "$count" > "$SHIM_COUNT"
case "$SHIM_MODE" in
  group-zero) /bin/ps "$@" | awk -v t="$CANCEL_TARGET" '$1 == t { $3 = 0 } { print }'; exit "${PIPESTATUS[0]}" ;;
  hide) /bin/ps "$@" | awk -v t="$CANCEL_TARGET" '$1 != t'; exit "${PIPESTATUS[0]}" ;;
  hide-on-2)
    if [[ "$count" -eq 2 ]]; then
      /bin/ps "$@" | awk -v t="$CANCEL_TARGET" '$1 != t'; exit "${PIPESTATUS[0]}"
    fi ;;
  "exit-on-$count")
    # The worker exits between cancel's identity check and this listing.
    kill -KILL "$CANCEL_TARGET"
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      /bin/ps -o pid= -p "$CANCEL_TARGET" >/dev/null || break
      sleep 0.1
    done ;;
esac
exec /bin/ps "$@"
"""

    def setUp(self):
        GroupChangeTests.setUp(self)
        shims = self.root / "shims"
        shims.mkdir()
        (shims / "ps").write_text(self.SHIM)
        (shims / "ps").chmod(0o755)
        self.env["PATH"] = f"{shims}{os.pathsep}{os.environ['PATH']}"
        self.env["SHIM_COUNT"] = str(self.root / "shim.count")

    def worker(self, ignore_term=False):
        ready = self.root / "worker.ready"
        code = ("import signal, sys, time\nfrom pathlib import Path\n"
                + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
                + "Path(sys.argv[1]).write_text('ready')\n"
                + "time.sleep(60)\n")
        process = subprocess.Popen([sys.executable, "-c", code, str(ready)],
                                   start_new_session=True, stdin=subprocess.DEVNULL)
        self.addCleanup(process.wait, 5)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        # Reaped as soon as it exits, so ps stops listing it.
        threading.Thread(target=process.wait, daemon=True).start()
        deadline = time.monotonic() + STARTUP_WAIT
        while not ready.exists():
            self.assertIsNone(process.poll(), f"worker PID {process.pid} exited before readiness ({ready})")
            self.assertLess(time.monotonic(), deadline, f"worker PID {process.pid} did not become ready ({ready})")
            time.sleep(0.02)
        (self.job / "pid.start").write_text(start_time(process.pid) + "\n")
        (self.job / "pid").write_text(f"{process.pid}\n")
        self.env["CANCEL_TARGET"] = str(process.pid)
        return process

    def cancel(self, *flags):
        return subprocess.run(["/bin/bash", str(JOBS), *flags, "cancel", self.job.name], env=self.env,
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def test_a_worker_in_process_group_zero_is_refused(self):
        process = self.worker()
        self.env["SHIM_MODE"] = "group-zero"
        result = self.cancel("--json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual((reply["code"], reply["signal_sent"]), ("invalid-process-group", False))
        time.sleep(0.3)
        self.assertIsNone(process.poll(), "the worker was signalled")
        self.assertFalse((self.job / "exit").exists())

    def test_failed_term_delivery_does_not_claim_term_was_sent(self):
        process = self.worker()
        self.env["SHIM_MODE"] = "hide-on-2"
        # Bash uses its builtin kill before PATH; disable it only for this cancel.
        startup = self.root / "bash-env"
        startup.write_text("enable -n kill\n")
        self.env["BASH_ENV"] = str(startup)
        kill = self.root / "shims" / "kill"
        kill.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$KILL_CALLS"\nexit 1\n')
        kill.chmod(0o755)
        calls = self.root / "kill.calls"
        self.env["KILL_CALLS"] = str(calls)
        result = self.cancel("--json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual(reply["code"], "worker-unlisted-after-term")
        self.assertIs(reply["signal_sent"], False)
        self.assertEqual([line for line in calls.read_text().splitlines() if line.startswith("-TERM")],
                         [f"-TERM -{process.pid}", f"-TERM {process.pid}"])
        self.assertNotIn("TERM was sent", reply["reason"])
        self.assertIn("TERM could not be delivered", reply["reason"])
        self.assertIsNone(process.poll(), "the worker was signalled")
        self.assertFalse((self.job / "exit").exists())

    def test_a_live_worker_missing_from_the_listing_is_refused(self):
        process = self.worker()
        self.env["SHIM_MODE"] = "hide"
        result = self.cancel("--json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual((reply["code"], reply["signal_sent"]), ("worker-unlisted", False))
        time.sleep(0.3)
        self.assertIsNone(process.poll(), "the worker was signalled")
        self.assertFalse((self.job / "exit").exists())

    def test_a_worker_gone_before_the_first_listing_is_not_running(self):
        self.worker()
        self.env["SHIM_MODE"] = "exit-on-1"
        result = self.cancel()
        self.assertEqual((result.returncode, result.stdout), (0, "not running (no live worker to cancel)\n"),
                         result.stderr)
        self.assertFalse((self.job / "exit").exists())

    def test_a_worker_gone_before_the_sigkill_listing_records_137(self):
        self.worker(ignore_term=True)
        self.env["SHIM_MODE"] = "exit-on-2"
        result = self.cancel("--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["job"], {"id": self.job.name, "outcome": "cancelled", "exit_code": 137})
        self.assertEqual((self.job / "exit").read_text(), "137\n")
        self.assertEqual((self.root / "shim.count").read_text().strip(), "2")


if __name__ == "__main__":
    unittest.main()
