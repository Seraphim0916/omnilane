"""Exercise real jobs.sh readers while the supervisor publishes non-atomically."""
import offline_env  # noqa: F401
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "20260905-160000-12345-1"


class ExitPublishRaceTests(unittest.TestCase):
    def exercise(self, command, late=False):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            job = base / "jobs" / JOB_ID
            job.mkdir(parents=True)
            (job / "meta.json").write_text(
                '{"lane":"hardest-coding","vendor":"codex","session_mode":"live"}')
            receipt = job / "exit"
            marker = base / "observed"
            shims = base / "bin"
            shims.mkdir()
            # Observe the actual read (or poller entry), without process queries.
            tool = "perl" if late else "cat"
            real_tool = shutil.which(tool)
            self.assertIsNotNone(real_tool)
            shim = shims / tool
            shim.write_text(
                '#!/bin/bash\n'
                + (': > "$RACE_EXIT"\n: > "$RACE_MARKER"\n' if late else
                   'if [[ "$1" == "$RACE_EXIT" ]]; then : > "$RACE_MARKER"; fi\n')
                + 'exec "' + real_tool + '" "$@"\n')
            shim.chmod(0o755)
            if not late:
                receipt.touch()
            env = dict(os.environ, OMNILANE_HOME=str(base),
                       PATH=str(shims) + os.pathsep + os.environ["PATH"],
                       RACE_EXIT=str(receipt), RACE_MARKER=str(marker))
            stop = threading.Event()
            published = threading.Event()

            def publish():
                while not marker.exists():
                    if stop.wait(0.01):
                        return
                if stop.wait(0.3):
                    return
                # Same inode, no rename: expose empty then complete content.
                with receipt.open("w") as stream:
                    stream.write("23\n")
                    stream.flush()
                published.set()

            writer = threading.Thread(target=publish)
            writer.start()
            try:
                args = ["/bin/bash", str(ROOT / "scripts/jobs.sh"), command, JOB_ID]
                if command == "wait":
                    args += ["--timeout", "3"]
                result = subprocess.run(args, env=env, capture_output=True,
                                        text=True, timeout=20)
                self.assertEqual(result.returncode, 23, (result.stdout, result.stderr))
                self.assertTrue(published.is_set())
                self.assertEqual(result.stderr, "")
                self.assertIn("exit=23" if command == "wait" else "exit 23", result.stdout)
            finally:
                stop.set()
                writer.join(timeout=2)
                self.assertFalse(writer.is_alive())

    def test_close_existing_empty_exit(self):
        self.exercise("close")

    def test_close_empty_exit_arrives_during_poll(self):
        self.exercise("close", late=True)

    def test_wait_existing_empty_exit(self):
        self.exercise("wait")

    def test_python39_syntax(self):
        ast.parse(Path(__file__).read_text(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
