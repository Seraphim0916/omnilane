"""Deterministic checks for the shell fixture's process handshakes."""
import offline_env  # noqa: F401
import ast
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class CloseSynchronizationTests(unittest.TestCase):
    def setUp(self):
        source_path = Path(os.environ.get("CLOSE_SYNC_UNDER_TEST", ROOT / "tests/test_live_close_deadline.sh"))
        source = source_path.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        # Helpers read the fixture's module-level hang guard (GENEROUS) as a default argument.
        definitions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                       or (isinstance(node, ast.Assign) and [getattr(t, 'id', None) for t in node.targets] == ['GENEROUS'])]
        self.helpers = dict(os=os, subprocess=subprocess, time=time)
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "close-helpers", "exec"), self.helpers)

    def test_marker_does_not_mean_sigstop_has_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "at-loop-boundary"
            child = subprocess.Popen([sys.executable, "-c", """
import os, pathlib, signal, sys
pathlib.Path(sys.argv[1]).touch()
sys.stdin.readline()
os.kill(os.getpid(), signal.SIGSTOP)
""", str(marker)], stdin=subprocess.PIPE, text=True)
            try:
                self.helpers["wait_for"](marker)
                stopped = threading.Event()
                errors = []
                def observe_stop():
                    try:
                        self.helpers["wait_for_stopped"](child.pid)
                    except Exception as error:
                        errors.append(error)
                    finally:
                        stopped.set()
                observer = threading.Thread(target=observe_stop)
                observer.start()
                # The child cannot STOP until we release its input barrier.
                self.assertFalse(stopped.wait(.03), 'marker was mistaken for stopped state')
                # Reproduce the old ordering: CONT after marker, before STOP.
                os.kill(child.pid, signal.SIGCONT)
                child.stdin.write("reach-stop\n")
                child.stdin.flush()
                # Only an observed stopped state makes the next CONT reliable.
                self.assertTrue(stopped.wait(2), 'STOP was not observed before deadline')
                self.assertEqual(errors, [])
                self.assertIsNone(child.poll())
                os.kill(child.pid, signal.SIGCONT)
                self.assertEqual(child.wait(timeout=2), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
                child.stdin.close()
                if 'observer' in locals():
                    observer.join(timeout=3)

    def test_close_does_not_accept_a_live_pid_or_wait_for_late_reaping(self):
        helper = self.helpers["wait_for_exit"]
        with patch.object(os, "kill", side_effect=ProcessLookupError):
            helper(12345)
        with patch.object(os, "kill", side_effect=[None, ProcessLookupError]):
            with self.assertRaisesRegex(AssertionError, "survived close"):
                helper(12345)

