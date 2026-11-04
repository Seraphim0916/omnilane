import offline_env  # Activate suite isolation for direct file execution.

import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class JobLockCleanupTests(unittest.TestCase):
    def test_lock_failures_close_once_without_state_access(self):
        for failure in ("nonregular", "fstat", "flock"):
            with self.subTest(failure=failure):
                job = mock.MagicMock()
                job.is_dir.return_value = True
                job.is_symlink.return_value = False
                root = mock.MagicMock()
                root.__truediv__.return_value = job
                stream = io.StringIO()
                error = OSError("synthetic " + failure)
                args = SimpleNamespace(home="unused", job_id="20261001-120000-1-2",
                                       action="cancel", input=None, json=False)
                with mock.patch.object(native, "jobs_root", return_value=root), \
                        mock.patch.object(native.os, "open", return_value=123) as opened, \
                        mock.patch.object(native.os, "fdopen", return_value=stream) as fdopen, \
                        mock.patch.object(stream, "fileno", return_value=123), \
                        mock.patch.object(stream, "close", wraps=stream.close) as close, \
                        mock.patch.object(native.os, "fstat") as fstat, \
                        mock.patch.object(native.fcntl, "flock") as flock, \
                        mock.patch.object(native, "read_json") as read, \
                        mock.patch.object(native, "atomic_json") as write:
                    fstat.return_value = SimpleNamespace(
                        st_mode=stat.S_IFDIR if failure == "nonregular" else stat.S_IFREG)
                    if failure == "fstat":
                        fstat.side_effect = error
                    if failure == "flock":
                        flock.side_effect = error
                    with self.assertRaises(ValueError if failure == "nonregular" else OSError) as caught:
                        native.job_command(args)
                    if failure == "nonregular":
                        self.assertEqual(str(caught.exception), "invalid native lock")
                    else:
                        self.assertIs(caught.exception, error)
                    opened.assert_called_once()
                    fdopen.assert_called_once_with(123, "r+")
                    fstat.assert_called_once_with(123)
                    close.assert_called_once_with()
                    self.assertTrue(stream.closed)
                    read.assert_not_called()
                    write.assert_not_called()
                    if failure == "flock":
                        flock.assert_called_once_with(stream, native.fcntl.LOCK_EX)
                    else:
                        flock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
