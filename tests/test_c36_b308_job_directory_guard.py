import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class JobDirectoryGuardTests(unittest.TestCase):
    def test_directory_guard_precedes_lock_and_state_access(self):
        for kind, is_dir, is_symlink in (("directory symlink", True, True),
                                        ("not a directory", False, False),
                                        ("ordinary directory", True, False)):
            with self.subTest(kind=kind):
                job = mock.MagicMock()
                job.is_dir.return_value = is_dir
                job.is_symlink.return_value = is_symlink
                root = mock.MagicMock()
                root.__truediv__.return_value = job
                job_id = "20261001-120000-1-2"
                args = SimpleNamespace(home="unused", job_id=job_id,
                                       action="list-state", input=None, json=False)
                stream, output = io.StringIO(), io.StringIO()
                with mock.patch.object(native, "jobs_root", return_value=root), \
                        mock.patch.object(native.os, "open", return_value=123) as opened, \
                        mock.patch.object(native.os, "fdopen", return_value=stream) as fdopen, \
                        mock.patch.object(stream, "fileno", return_value=123), \
                        mock.patch.object(native.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
                        mock.patch.object(native.fcntl, "flock") as flock, \
                        mock.patch.object(native, "read_json", return_value={
                            "job_id": job_id, "executor": "native", "state": "completed"}) as read, \
                        mock.patch.object(native, "atomic_json") as write, \
                        contextlib.redirect_stdout(output):
                    if kind == "ordinary directory":
                        self.assertEqual(native.job_command(args), 0)
                        opened.assert_called_once_with(
                            job / "native.lock", native.os.O_RDWR | native.os.O_NOFOLLOW | native.os.O_NONBLOCK)
                        fdopen.assert_called_once_with(123, "r+")
                        flock.assert_called_once_with(stream, native.fcntl.LOCK_EX)
                        read.assert_called_once_with(job / "native.json")
                        self.assertEqual(output.getvalue(), "done\n")
                        self.assertTrue(stream.closed)
                    else:
                        with self.assertRaisesRegex(ValueError, "^invalid job directory$"):
                            native.job_command(args)
                        opened.assert_not_called()
                        fdopen.assert_not_called()
                        flock.assert_not_called()
                        read.assert_not_called()
                        self.assertEqual(output.getvalue(), "")
                    write.assert_not_called()
                stream.close()


if __name__ == "__main__":
    unittest.main()
