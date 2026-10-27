import offline_env  # Activate suite isolation for direct file execution.

import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginFdopenTests(unittest.TestCase):
    def test_wrapper_failure_closes_acquired_descriptor_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"{}")
            fd = os.open(str(path), os.O_RDONLY)
            failure = OSError("synthetic fdopen failure")
            try:
                with patch.object(origin.os, "open", return_value=fd), \
                        patch.object(origin.os, "fdopen", side_effect=failure), \
                        patch.object(origin.os, "close", wraps=os.close) as close:
                    with self.assertRaises(OSError) as caught:
                        origin._read(path)
                    self.assertIs(caught.exception, failure)
                    close.assert_called_once_with(fd)
                    with self.assertRaises(OSError) as closed:
                        os.fstat(fd)
                    self.assertEqual(closed.exception.errno, errno.EBADF)
            finally:
                try:
                    os.fstat(fd)
                except OSError:
                    pass
                else:
                    os.close(fd)

    def test_success_leaves_closing_to_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
            streams = []
            original = os.fdopen

            def wrap(fd, mode):
                stream = original(fd, mode)
                streams.append(stream)
                return stream

            with patch.object(origin.os, "fdopen", side_effect=wrap), \
                    patch.object(origin.os, "close", wraps=os.close) as close:
                self.assertEqual(origin._read(path), b"{}")
                close.assert_not_called()
            self.assertEqual(len(streams), 1)
            self.assertTrue(streams[0].closed)

    def test_open_failure_does_not_close_any_descriptor(self):
        for failure in (PermissionError("denied"), FileNotFoundError("missing")):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(origin.os, "open", side_effect=failure), \
                    patch.object(origin.os, "fdopen") as fdopen, \
                    patch.object(origin.os, "close") as close, \
                    patch.object(Path, "is_symlink", return_value=False):
                if isinstance(failure, FileNotFoundError):
                    self.assertIsNone(origin._read(Path("policy.json")))
                else:
                    with self.assertRaisesRegex(origin.OriginError, "cannot open origin policy"):
                        origin._read(Path("policy.json"))
                close.assert_not_called()
                fdopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
