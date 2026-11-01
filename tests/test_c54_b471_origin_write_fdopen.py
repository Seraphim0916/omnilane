"""Temporary write descriptors transfer ownership only after fdopen succeeds."""
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


class WriteDescriptorTests(unittest.TestCase):
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
                    close.assert_called_once_with(fd)
                self.assertFalse(Path(name).exists())
                with self.assertRaises(OSError):
                    os.fstat(fd)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_success_transfers_descriptor_to_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            data = dict(schema_version=1, host=socket.gethostname(), vendors={})
            fd, name = tempfile.mkstemp(dir=directory)
            with patch.object(op.tempfile, "mkstemp", return_value=(fd, name)), \
                    patch.object(op.os, "close", wraps=os.close) as close:
                op._save(path, data)
                close.assert_not_called()
            with self.assertRaises(OSError):
                os.fstat(fd)
            self.assertFalse(Path(name).exists())
            self.assertEqual(op.load(path), data)


if __name__ == "__main__":
    unittest.main()
