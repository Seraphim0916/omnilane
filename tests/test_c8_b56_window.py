import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import self_exec


class WindowTests(unittest.TestCase):
    def test_shared_inclusive_window_preserves_sources(self):
        until = datetime.datetime(2026, 10, 26, tzinfo=datetime.timezone.utc)
        stamps = ["2026-10-18T23:59:59Z", "2026-10-19T00:00:00Z",
                  "2026-10-26T00:00:00Z", "2026-10-26T00:00:01Z", "2099-01-01T00:00:00Z"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / self_exec.LOG_NAME
            log.write_text("".join(json.dumps({"id": str(i), "recorded_at": stamp}) + "\n"
                                   for i, stamp in enumerate(stamps)))
            for i, stamp in enumerate(stamps):
                meta = root / "jobs" / str(i) / "meta.json"
                meta.parent.mkdir(parents=True)
                meta.write_text(json.dumps({"started": stamp, "executor": "cli"}))
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            output = io.StringIO()
            with patch.dict(os.environ, {"OMNILANE_HOME": directory}), \
                    patch.object(self_exec, "now", return_value=until) as clock, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(self_exec.main(["stats", "--since", "7d", "--json"]), 0)
            clock.assert_called_once_with()
            report = json.loads(output.getvalue())
            self.assertEqual(report["until"], stamps[2])
            self.assertEqual(report["since"], stamps[1])
            self.assertEqual([r["id"] for r in report["records"]], ["1", "2"])
            self.assertEqual(report["dispatched_by_executor"], {"cli": 2})
            self.assertEqual((report["self_exec"], report["dispatched"], report["self_exec_share"]), (2, 2, 0.5))
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_cli_excludes_future_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / self_exec.LOG_NAME).write_text('{"recorded_at":"2099-01-01T00:00:00Z"}\n')
            meta = root / "jobs" / "future" / "meta.json"
            meta.parent.mkdir(parents=True)
            meta.write_text('{"started":"2099-01-01T00:00:00Z"}')
            result = subprocess.run(["bash", str(ROOT / "bin/omnilane"), "stats", "self-exec", "--json"],
                                    env=dict(os.environ, OMNILANE_HOME=directory),
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual((report["self_exec"], report["dispatched"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
