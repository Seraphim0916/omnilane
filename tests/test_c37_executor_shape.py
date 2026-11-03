"""Malformed executor fields must not interrupt self-execution statistics."""
import offline_env  # noqa: F401
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExecutorShapeTests(unittest.TestCase):
    def test_mixed_executor_shapes_are_counted_without_rewriting_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            environment = offline_env.fixture_environment_with_isolated_tools(
                self, dict(os.environ, HOME=str(home), OMNILANE_HOME=str(home / ".omnilane")))
            # Falsey values retain the existing unknown bucket; truthy non-strings are skipped.
            executors = ["cli", "cli", "native", None, "", [], {}, 0, 0.0, False,
                         ["cli"], {"name": "cli"}, 1, -2, 1.5, True, "cli"]
            entries = [{"started": "2026-01-02T00:00:00Z", "executor": value}
                       for value in executors]
            entries.append({"started": "2026-01-02T00:00:00Z"})
            before = {}
            for index, entry in enumerate(entries):
                path = home / ".omnilane" / "jobs" / str(index) / "meta.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(entry), encoding="utf-8")
                before[path] = path.read_bytes()

            result = subprocess.run(
                ["bash", str(ROOT / "bin/omnilane"), "stats", "self-exec",
                 "--since", "2026-01-01", "--json"],
                env=environment, text=True, capture_output=True, timeout=20)

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["dispatched_by_executor"],
                             {"cli": 3, "native": 1, "unknown": 8})
            self.assertEqual(report["dispatched"], 12)
            self.assertEqual(report["self_exec"], 0)
            self.assertEqual(report["self_exec_share"], 0.0)
            after = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
