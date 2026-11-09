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

    def test_success_closes_stream_and_owned_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
            streams = []
            original = os.fdopen

            descriptors = []

            def wrap(fd, mode, **kwargs):
                self.assertIs(kwargs.get("closefd"), False)
                descriptors.append(fd)
                stream = original(fd, mode, **kwargs)
                streams.append(stream)
                return stream

            with patch.object(origin.os, "fdopen", side_effect=wrap), \
                    patch.object(origin.os, "close", wraps=os.close) as close:
                self.assertEqual(origin._read(path), b"{}")
                close.assert_called_once_with(descriptors[0])
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



    def test_partial_construction_cleanup_does_not_close_reused_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
            original_fdopen = os.fdopen
            original_open = os.open
            original_close = os.close
            descriptors = []
            replacements = []
            failure = RuntimeError("construction failed after creating stream")

            def construct(fd, *args, **kwargs):
                descriptors.append(fd)
                stream = original_fdopen(fd, *args, **kwargs)
                stream.close()
                # With closefd=True this reuses fd before caller cleanup.
                replacements.append(original_open(os.devnull, os.O_RDONLY))
                raise failure

            def close_and_reuse(fd):
                original_close(fd)
                # Force reuse after the legitimate owner closes its fd, too.
                replacement = original_open(os.devnull, os.O_RDONLY)
                if replacement != fd:
                    os.dup2(replacement, fd)
                    original_close(replacement)
                replacements.append(fd)

            try:
                with patch.object(origin.os, "fdopen", side_effect=construct), \
                        patch.object(origin.os, "close", side_effect=close_and_reuse) as close:
                    with self.assertRaises(RuntimeError) as caught:
                        origin._read(path)
                    self.assertIs(caught.exception, failure)
                    close.assert_called_once_with(descriptors[0])
                    # Both unrelated resources must survive, including reused fd.
                    self.assertNotEqual(replacements[0], descriptors[0])
                    for fd in replacements:
                        os.fstat(fd)
            finally:
                for fd in set(replacements + descriptors):
                    try:
                        original_close(fd)
                    except OSError:
                        pass

    def test_original_failure_survives_cleanup_close_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
            original_close = os.close
            failure = KeyboardInterrupt("original construction failure")

            def close_then_fail(fd):
                original_close(fd)
                raise OSError("cleanup close failed")

            with patch.object(origin.os, "fdopen", side_effect=failure), \
                    patch.object(origin.os, "close", side_effect=close_then_fail) as close:
                with self.assertRaises(KeyboardInterrupt) as caught:
                    origin._read(path)
                self.assertIs(caught.exception, failure)
                self.assertEqual(close.call_count, 1)

    def test_successful_operation_surfaces_close_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
            original_close = os.close
            failure = OSError("successful operation close failed")

            def close_then_fail(fd):
                original_close(fd)
                raise failure

            with patch.object(origin.os, "close", side_effect=close_then_fail) as close:
                with self.assertRaises(OSError) as caught:
                    origin._read(path)
                self.assertIs(caught.exception, failure)
                self.assertEqual(close.call_count, 1)


if __name__ == "__main__":
    unittest.main()
