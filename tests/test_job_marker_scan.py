"""job_marker.py must skip processes it cannot read and never fail the whole scan because of one."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import offline_env  # noqa: F401  (test isolation bootstrap; must load before production code)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import job_marker  # noqa: E402

TOKEN = "0123456789abcdef0123456789abcdef"


def procargs(argv, env, path=b"/usr/bin/tool"):
    body = path + b"\0\0\0" + b"".join(a + b"\0" for a in argv) + b"".join(e + b"\0" for e in env)
    return struct.pack("<i", len(argv)) + body + b"\0"


class ParseProcargs2Tests(unittest.TestCase):
    def test_environment_is_separated_from_arguments(self):
        raw = procargs([b"tool", b"OMNILANE_JOB_TOKEN=" + TOKEN.encode()], [b"A=1", b"B=2"])
        self.assertEqual(job_marker.parse_procargs2(raw), [b"A=1", b"B=2"])

    def test_unusable_buffers_are_none(self):
        for raw in (None, b"", b"\x01\x00", struct.pack("<i", -1) + b"x\0",
                    struct.pack("<i", 1) + b"no-terminator", struct.pack("<i", 5) + b"/p\0a\0"):
            self.assertIsNone(job_marker.parse_procargs2(raw), raw)


class ScanSkipsUnreadableTests(unittest.TestCase):
    def source(self, table):
        def pids():
            return list(table)

        def info(pid):
            entry = table.get(pid, {})
            if isinstance(entry.get("info"), Exception):
                raise entry["info"]
            return entry.get("info")

        def environment(pid):
            entry = table.get(pid, {})
            if isinstance(entry.get("env"), Exception):
                raise entry["env"]
            return entry.get("env")
        return pids, info, environment

    def test_permission_denied_gone_and_malformed_are_skipped(self):
        wanted = b"OMNILANE_JOB_TOKEN=" + TOKEN.encode()
        table = {
            10: {"info": (1, 100)},                                  # supervisor
            20: {"info": (10, 200), "env": [wanted]},                # the job's helper
            21: {"info": (1, 210), "env": None},                     # another user: denied
            22: {"info": None},                                      # exited before info
            23: {"info": (1, 230), "env": PermissionError("EPERM")},
            24: {"info": (1, 240), "env": struct.error("short")},
            25: {"info": ValueError("bad stat")},
            26: {"info": (1, 260), "env": [wanted + b"9"]},          # longer token
            27: {"info": (1, 50), "env": [wanted]},                  # started before the job
        }
        found = job_marker.scan(TOKEN, 10, 150, source=self.source(table))
        self.assertEqual(found, [(20, 200)])

    def test_unknown_supervisor_is_unavailable(self):
        with self.assertRaises(job_marker.Unavailable):
            job_marker.scan(TOKEN, 99, 0, source=self.source({1: {"info": (0, 1)}}))


class LinuxProcSourceTests(unittest.TestCase):
    """The /proc reader runs on any platform against a fake tree, so macOS also covers it."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omnilane-fake-proc-")
        self.addCleanup(self.temporary.cleanup)
        self.proc = Path(self.temporary.name)

    def add(self, pid, ppid, start_ticks, environ=None, comm="py (x) y"):
        directory = self.proc / str(pid)
        directory.mkdir()
        # After the comm: state, ppid, then 17 more fields up to starttime (field 22).
        rest = ["S", str(ppid)] + ["0"] * 17 + [str(start_ticks)] + ["0"] * 5
        (directory / "stat").write_bytes(f"{pid} ({comm}) {' '.join(rest)}".encode())
        if environ is not None:
            (directory / "environ").write_bytes(environ)

    def test_environ_entries_are_exact_and_unreadable_ones_are_skipped(self):
        wanted = b"OMNILANE_JOB_TOKEN=" + TOKEN.encode()
        self.add(10, 1, 100, b"A=1\0")                                   # supervisor
        self.add(20, 10, 200, b"A=1\0" + wanted + b"\0B=2\0")             # the job's helper
        self.add(21, 1, 210)                                             # environ missing (gone, or denied)
        self.add(22, 1, 220, b"X=" + wanted + b"\0")                     # token inside another value
        self.add(23, 1, 230, wanted + b"9\0")                            # longer token
        self.add(24, 1, 90, wanted + b"\0")                              # started before the supervisor
        (self.proc / "25").mkdir()                                       # stat missing
        (self.proc / "26").mkdir()
        (self.proc / "26" / "stat").write_bytes(b"26 (short) S 1")       # malformed stat
        (self.proc / "self").mkdir()                                     # not a PID
        source = job_marker._linux(str(self.proc))
        pids, info, environment = source
        self.assertIsNone(environment(21))
        self.assertIsNone(info(25))
        self.assertIsNone(info(26))
        self.assertEqual(info(20)[0], 10)
        self.assertEqual(environment(20), [b"A=1", wanted, b"B=2"])
        saved = sys.platform
        try:
            sys.platform = "linux"  # the Linux baseline: the supervisor's own start time
            found = job_marker.scan(TOKEN, 10, 0, source=source)
        finally:
            sys.platform = saved
        self.assertEqual([pid for pid, _ in found], [20])


@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"),
                     "real process listing is read only on macOS and Linux")
class ScanRealProcessesTests(unittest.TestCase):
    def test_command_line_exits_zero_with_unreadable_processes_present(self):
        try:
            check = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'pid='], capture_output=True, text=True)
        except OSError:
            self.skipTest('process inspection denied by this execution environment')
        if check.returncode or check.stdout.strip() != str(os.getpid()):
            self.skipTest('process inspection denied; do not bypass it through platform APIs')
        # pid 1 belongs to root, so its environment cannot be read; the scan must still succeed.
        result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/lib/job_marker.py"), "scan",
                                 "--token", TOKEN, "--supervisor", str(os.getpid()), "--after", "0"],
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
