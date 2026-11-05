"""_save alone owns the temporary descriptor and closes it once on every path."""
import errno
import offline_env  # noqa: F401
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class FailingStream:
    def __init__(self, stream, failure, error):
        self.stream, self.failure, self.error = stream, failure, error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, data):
        if self.failure == "write":
            raise self.error
        return self.stream.write(data)

    def flush(self):
        if self.failure == "flush":
            raise self.error
        return self.stream.flush()

    def fileno(self):
        return self.stream.fileno()


class WriteDescriptorTests(unittest.TestCase):
    def assert_closed_once(self, close, fd):
        close.assert_called_once_with(fd)
        with self.assertRaises(OSError) as caught:
            os.fstat(fd)
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_a_failure_while_writing_closes_descriptor_once_and_removes_temporary(self):
        for failure in ("write", "flush", "fsync"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                data = dict(schema_version=1, host=socket.gethostname(), vendors={})
                fd, name = tempfile.mkstemp(dir=directory)
                error = OSError("WRITE_FAIL")
                real_fdopen = os.fdopen
                with patch.object(op.tempfile, "mkstemp", return_value=(fd, name)), \
                        patch.object(op.os, "fdopen", side_effect=lambda *args, **kwargs: FailingStream(
                            real_fdopen(*args, **kwargs), failure, error)), \
                        patch.object(op.os, "fsync", side_effect=error if failure == "fsync" else None), \
                        patch.object(op.os, "close", wraps=os.close) as close:
                    with self.assertRaises(OSError) as caught:
                        op._save(path, data)
                    self.assertIs(caught.exception, error)
                    self.assert_closed_once(close, fd)
                self.assertFalse(Path(name).exists())
                self.assertFalse(path.exists())

    def test_fdopen_failure_closes_descriptor_once_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            data = dict(schema_version=1, host=socket.gethostname(), vendors={})
            fd, name = tempfile.mkstemp(dir=directory)
            error = OSError("FDOPEN_FAIL")
            try:
                with patch.object(op.tempfile, "mkstemp", return_value=(fd, name)), \
                        patch.object(op.os, "fdopen", side_effect=error), \
                        patch.object(op.os, "close", wraps=os.close) as close:
                    with self.assertRaises(OSError) as caught:
                        op._save(path, data)
                    self.assertIs(caught.exception, error)
                    self.assert_closed_once(close, fd)
                self.assertFalse(Path(name).exists())
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_success_closes_descriptor_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            data = dict(schema_version=1, host=socket.gethostname(), vendors={})
            fd, name = tempfile.mkstemp(dir=directory)
            with patch.object(op.tempfile, "mkstemp", return_value=(fd, name)), \
                    patch.object(op.os, "close", wraps=os.close) as close:
                op._save(path, data)
                self.assert_closed_once(close, fd)
            self.assertFalse(Path(name).exists())
            self.assertEqual(op.load(path), data)


if __name__ == "__main__":
    unittest.main()
