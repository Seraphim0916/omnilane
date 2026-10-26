"""Keep exit publication behind receipt finalization on the real dispatch path."""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class JobFinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-finalization-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "jobs-home"
        self.home.mkdir()
        self.bins = self.root / "bin"
        self.bins.mkdir()
        self.env.update(OMNILANE_HOME=str(self.home), FIXTURE_ROOT=str(self.root),
                        PATH=str(self.bins) + os.pathsep + self.env["PATH"])
        self.pids = []
        self.addCleanup(self.stop_owned_processes)

    def alive(self, pid):
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], env=self.env,
                                capture_output=True, text=True, timeout=5)
        state = result.stdout.strip()
        # A zombie cannot execute; the host init process owns reaping it.
        return bool(state) and not state.startswith("Z")

    def stop_owned_processes(self):
        (self.root / "publish.release").touch()
        deadline = time.monotonic() + 3
        while any(self.alive(pid) for pid in self.pids) and time.monotonic() < deadline:
            time.sleep(0.02)
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def wait_for(self, predicate, description, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("deadline waiting for " + description)
            time.sleep(0.02)

    def check_timeout_finalization(self, receipt_failure=False, exit_code=124):
        self.env["FIXTURE_RECEIPT_FAILURE"] = "1" if receipt_failure else "0"
        gate = self.bins / "gate"
        gate.write_text('#!/bin/bash\ntrap "" TERM\nsleep 30 &\n'
                        'printf "%s %s\\n" "$$" "$!" > "$FIXTURE_ROOT/gate.pids"\n'
                        + ('wait\n' if exit_code == 124 else f'exit {exit_code}\n'))
        gate.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f"fixture: exec {shlex.quote(str(gate))} -\n")
        self.env["FIXTURE_REAL_MV"] = str(Path(self.env["OMNILANE_TEST_UTIL_PATH"]) / "mv")
        wrapper = self.bins / "mv"
        wrapper.write_text('''#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
root = Path(os.environ['FIXTURE_ROOT'])
if Path(sys.argv[-1]).parent == Path(os.environ['OMNILANE_HOME']) / 'inbox':
    (root / 'publish.waiting').touch()
    deadline = time.monotonic() + 10
    while not (root / 'publish.release').exists():
        if time.monotonic() >= deadline:
            raise SystemExit(98)
        time.sleep(0.02)
    if os.environ['FIXTURE_RECEIPT_FAILURE'] == '1':
        raise SystemExit(73)
os.execv(os.environ['FIXTURE_REAL_MV'], ['mv', *sys.argv[1:]])
''')
        wrapper.chmod(0o755)
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--background", "--single-shot",
             "--timeout", "30", "--job-timeout", "1" if exit_code == 124 else "10",
             "fixture", "offline finalization fixture"],
            env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        job_id = result.stdout.strip()
        job_dir = self.home / "jobs" / job_id
        self.wait_for(lambda: (job_dir / "pid").exists(), "controller pid")
        self.pids.append(int((job_dir / "pid").read_text()))
        self.wait_for(lambda: (self.root / "gate.pids").exists(), "workload pids")
        self.pids.extend(int(pid) for pid in (self.root / "gate.pids").read_text().split())
        self.wait_for(lambda: (self.root / "publish.waiting").exists(), "receipt publication")
        if exit_code == 124:
            self.assertFalse((job_dir / "exit").exists(), "exit published before receipt finalization")
        else:
            self.assertEqual((job_dir / "exit").read_text().strip(), str(exit_code))
        self.assertFalse((self.home / "inbox" / (job_id + ".json")).exists())
        self.assertTrue(self.alive(self.pids[0]), "controller must still own the finalization")
        self.assertFalse(any(self.alive(pid) for pid in self.pids[1:]), "workload survived timeout")
        (self.root / "publish.release").touch()
        self.wait_for(lambda: (job_dir / "exit").exists(), "exit publication")
        self.assertEqual((job_dir / "exit").read_text().strip(), str(exit_code))
        receipt_path = self.home / "inbox" / (job_id + ".json")
        if receipt_failure:
            self.assertFalse(receipt_path.exists())
        else:
            self.wait_for(receipt_path.exists, "finalized receipt")
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["exit"], exit_code)
            self.assertNotIn("controller_exit_confirmed", receipt)
        self.wait_for(lambda: not any(self.alive(pid) for pid in self.pids), "natural process exit")

    def test_timeout_exit_waits_for_receipt_and_controller_exits_naturally(self):
        self.check_timeout_finalization()

    def test_receipt_failure_preserves_timeout_and_natural_exit(self):
        self.check_timeout_finalization(receipt_failure=True)

    def test_cancellation_status_remains_recorded_during_finalization(self):
        self.check_timeout_finalization(exit_code=143)


if __name__ == "__main__":
    unittest.main()
