"""job-timeout.pl must name itself, not the scanner, as --supervisor when it runs the marker scan.

Perl passes list arguments of a piped open by alias, so `$$` read in the forked child is the
child's own PID. The scan then measured "after the job started" from the scanner's start time
and found nothing on Linux. A recording stand-in for job_marker.py checks the argument.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import offline_env  # noqa: F401  (test isolation bootstrap; must load before production code)

ROOT = Path(__file__).resolve().parents[1]

RECORDER = """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
record = {"argv": args, "ppid": os.getppid(), "pid": os.getpid()}
with open(os.environ["SCAN_RECORD"], "a") as handle:
    handle.write(json.dumps(record) + "\\n")
"""


class SupervisorArgumentTests(unittest.TestCase):
    def test_scan_names_the_supervisor_itself(self):
        with tempfile.TemporaryDirectory(prefix="omnilane-scan-args-") as directory:
            root = Path(directory)
            recorder = root / "recorder.py"
            recorder.write_text(RECORDER, encoding="utf-8")
            record = root / "scans.jsonl"
            env = dict(os.environ, OMNILANE_JOB_MARKER_HELPER=str(recorder),
                       OMNILANE_JOB_LEFTOVERS_FILE=str(root / "leftovers"), SCAN_RECORD=str(record))
            env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
            supervisor = subprocess.Popen(["perl", str(ROOT / "scripts/lib/job-timeout.pl"), "1",
                                           sys.executable, "-c", "import time; time.sleep(30)"], env=env)
            self.assertEqual(supervisor.wait(timeout=30), 124)
            scans = [json.loads(line) for line in record.read_text().splitlines()]
            self.assertTrue(scans, "the supervisor never ran the marker scan")
            for scan in scans:
                argv = scan["argv"]
                named = int(argv[argv.index("--supervisor") + 1])
                self.assertEqual(named, supervisor.pid, scan)
                self.assertEqual(scan["ppid"], supervisor.pid, scan)
                self.assertNotEqual(named, scan["pid"], "the scan named itself as the supervisor")


if __name__ == "__main__":
    unittest.main()
