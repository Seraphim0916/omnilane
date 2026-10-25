"""Observe real mailbox descriptors and process teardown with offline runners."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]

# No close_fds sanitization: an inherited worker descriptor must remain visible.
# The child has a private stdin lifetime pipe, not a mailbox reader. Its EOF
# therefore proves that the runner exited even on the forced-close path.
PROBE = r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(os.environ["PROBE_ROOT"])
def snapshot(name):
    descriptors = {}
    for fd in range(1024):
        try:
            info = os.fstat(fd)
        except OSError:
            continue
        descriptors[str(fd)] = [info.st_dev, info.st_ino]
    target = root / (name + ".json")
    temporary = target.with_suffix(".pending")
    temporary.write_text(json.dumps({"pid": os.getpid(), "ppid": os.getppid(),
                                     "pgid": os.getpgrp(), "fds": descriptors}))
    temporary.rename(target)

if len(sys.argv) > 1 and sys.argv[1] == "child":
    snapshot("child")
    sys.stdin.buffer.read()
    sys.exit(0)

snapshot("runner")
control = os.open(root / "inheritance-control", os.O_CREAT | os.O_RDWR, 0o600)
os.set_inheritable(control, True)
child = subprocess.Popen([sys.executable, __file__, "child"],
                         stdin=subprocess.PIPE, close_fds=False)
with open(os.environ["OMNILANE_INBOX"]) as inbox:
    assert inbox.readline(), "initial message missing"
    (root / "consumed").touch()
    scenario = os.environ["PROBE_SCENARIO"]
    if scenario == "timeout":
        # A real monotonic timeout in the fake executor, not a canned exit.
        while not (root / "release").exists():
            time.sleep(0.01)
        signal.signal(signal.SIGALRM, lambda *_: sys.exit(142))
        signal.setitimer(signal.ITIMER_REAL, 2)
        inbox.read()
        raise RuntimeError("unexpected EOF before executor timeout")
    Path(sys.argv[6]).write_text("fixture result\n")
    Path(sys.argv[6] + ".events.jsonl").write_text(
        json.dumps({"type": "result", "is_error": False,
                    "result": "fixture result"}) + "\n")
    if scenario == "forced":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / "armed").touch()
    if scenario == "normal":
        while not (root / "release").exists():
            time.sleep(0.01)
    elif scenario == "operator":
        inbox.read()
    elif scenario == "forced":
        while True:
            time.sleep(0.1)
child.stdin.close()
child.wait(timeout=3)
os.close(control)
'''


class MailboxDescriptorLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-mailbox-fd-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists(), "offline guard triggered"))
        self.runtime = self.root / "runtime"
        shutil.copytree(ROOT / "scripts/lib", self.runtime / "scripts/lib")
        shutil.copytree(ROOT / "config", self.runtime / "config")
        runners = self.runtime / "scripts/runners"
        runners.mkdir()
        probe = self.root / "probe.py"
        probe.write_text(PROBE)
        runner = runners / "run-claude.sh"
        runner.write_text('#!/bin/bash\nexec python3 "$PROBE_ROOT/probe.py" "$@"\n')
        runner.chmod(0o755)
        self.job = self.root / "job"
        self.job.mkdir()
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("offline descriptor lifecycle fixture\n")
        self.env.update(PROBE_ROOT=str(self.root), OMNILANE_HOME=str(self.root / "home"),
                        OMNILANE_SESSION_MODE="live", OMNILANE_LIVE_REQUIRED="1",
                        OMNILANE_IDLE_TIMEOUT="0", OMNILANE_TIMEOUT="2")
        self.process = None
        self.addCleanup(self.cleanup_processes)

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def cleanup_processes(self):
        # Only our dedicated process group; cleanup runs AFTER assertions so
        # forced fixture cleanup cannot turn a product leak into a passing test.
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)

    def group_alive(self):
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Darwin answers EPERM, not ESRCH, while the group holds only unreaped zombies;
            # the group still exists, so keep waiting rather than erroring out.
            return True
        return True

    def await_condition(self, predicate, label, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.assertLess(time.monotonic(), deadline, label)
            time.sleep(0.02)

    def exercise(self, scenario, expected_rc):
        self.env["PROBE_SCENARIO"] = scenario
        # Files avoid a leaked descendant keeping communicate() pipes open.
        with (self.root / "stdout").open("w") as stdout, (self.root / "stderr").open("w") as stderr:
            self.process = subprocess.Popen(
                ["bash", str(self.runtime / "scripts/lib/job-worker.sh"),
                 "claude", "work", str(self.root), "claude-opus-5", "high",
                 str(self.prompt), str(self.job / "out.txt")],
                env=self.env, stdout=stdout, stderr=stderr, start_new_session=True)
        self.await_condition(lambda: all((self.root / name).exists()
                                        for name in ("runner.json", "child.json", "consumed"))
                             and (self.job / "inbox.ready").exists(), "fixture never ready")
        snapshots = {name: json.loads((self.root / (name + ".json")).read_text())
                     for name in ("runner", "child")}
        mailbox = {}
        for path in self.job.iterdir():
            if path.name in ("inbox.fifo", "runner-inbox.fifo", "out.txt.events.jsonl", "events.jsonl"):
                info = path.stat()
                mailbox[path.name] = [info.st_dev, info.st_ino]
        if scenario != "timeout":
            self.await_condition(lambda: (self.root / "armed").exists(), "runner never armed")
        if scenario in ("normal", "timeout"):
            (self.root / "release").touch()
        elif scenario in ("operator", "forced"):
            self.process.send_signal(signal.SIGUSR1)
        self.process.wait(timeout=15)
        deadline = time.monotonic() + 3
        while self.group_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        survivors = [name for name, item in snapshots.items() if self.alive(item["pid"])]
        residue = [name for name in ("inbox.ready", "inbox.holder.pid", "inbox.fifo", "runner-inbox.fifo")
                   if os.path.lexists(self.job / name)]
        leaks = {name: {fd: identity for fd, identity in item["fds"].items()
                        if identity in mailbox.values()} for name, item in snapshots.items()}
        evidence = dict(scenario=scenario, worker=self.process.pid, rc=self.process.returncode,
                        snapshots=snapshots, mailbox=mailbox, leaks=leaks,
                        survivors=survivors, group_alive=self.group_alive(), residue=residue,
                        stderr=(self.root / "stderr").read_text())
        # unittest retains this complete runtime observation on assertion failure.
        detail = json.dumps(evidence, sort_keys=True)
        with self.subTest(check="process ancestry"):
            self.assertEqual(snapshots["runner"]["ppid"], self.process.pid, detail)
            self.assertEqual(snapshots["child"]["ppid"], snapshots["runner"]["pid"], detail)
            self.assertTrue(all(item["pgid"] == self.process.pid for item in snapshots.values()), detail)
        with self.subTest(check="inheritance positive control"):
            info = (self.root / "inheritance-control").stat()
            self.assertIn([info.st_dev, info.st_ino], snapshots["child"]["fds"].values(), detail)
        with self.subTest(check="mailbox inventory"):
            self.assertEqual(len(mailbox), 4, detail)
        with self.subTest(check="descriptor inheritance"):
            self.assertFalse(any(leaks.values()), detail)
        with self.subTest(check="exit status"):
            self.assertEqual(self.process.returncode, expected_rc, detail)
        with self.subTest(check="process cleanup"):
            self.assertFalse(survivors, detail)
            self.assertFalse(evidence["group_alive"], detail)
        with self.subTest(check="mailbox cleanup"):
            self.assertFalse(residue, detail)
        with self.subTest(check="stdout"):
            self.assertEqual((self.root / "stdout").read_text(), "", detail)

    def test_normal_exit(self):
        self.exercise("normal", 0)

    def test_operator_close_after_result(self):
        self.exercise("operator", 0)

    def test_executor_timeout_without_result(self):
        self.exercise("timeout", 142)

    def test_operator_close_forces_unresponsive_runner(self):
        self.exercise("forced", 0)
