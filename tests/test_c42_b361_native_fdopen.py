import offline_env  # Activate suite isolation for direct file execution.

import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class FdopenTests(unittest.TestCase):
    def test_failure_before_construction_closes_once_and_preserves_error(self):
        for error in (OSError("fdopen-failed"), MemoryError("fdopen-failed")):
            with self.subTest(error=type(error).__name__):
                with patch.object(native.os, "open", return_value=42), \
                        patch.object(native.os, "fdopen", side_effect=error), \
                        patch.object(native.os, "close") as close:
                    with self.assertRaises(type(error)) as caught:
                        native.read_json("fixture.json")
                    self.assertIs(caught.exception, error)
                    close.assert_called_once_with(42)

    def test_close_error_does_not_replace_original(self):
        error = OSError("fdopen-failed")
        with patch.object(native.os, "open", return_value=42), \
                patch.object(native.os, "fdopen", side_effect=error), \
                patch.object(native.os, "close", side_effect=OSError("close-failed")) as close:
            with self.assertRaises(OSError) as caught:
                native.read_json("fixture.json")
            self.assertIs(caught.exception, error)
            close.assert_called_once_with(42)

    def test_success_closes_stream_and_owned_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.json"
            path.write_text('{"ok": true}')
            fdopen = os.fdopen
            streams = []
            descriptors = []

            def capture(*args, **kwargs):
                stream = fdopen(*args, **kwargs)
                streams.append(stream)
                descriptors.append(stream.fileno())
                return stream

            with patch.object(native.os, "fdopen", side_effect=capture), \
                    patch.object(native.os, "close", wraps=os.close) as close:
                self.assertEqual(native.read_json(path), {"ok": True})
                close.assert_called_once_with(descriptors[0])
            self.assertEqual(len(streams), 1)
            self.assertTrue(streams[0].closed)
            with self.assertRaises(OSError) as caught:
                os.fstat(descriptors[0])
            self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_failure_after_stream_construction_keeps_reused_fd_open(self):
        for error in (OSError("wrapper-failed"), MemoryError("wrapper-failed")):
            with self.subTest(error=type(error).__name__), \
                    tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "value.json"
                path.write_text('{"ok": true}')
                fdopen, real_close = os.fdopen, os.close
                streams, descriptors, replacements = [], [], []

                def replace_closed_fd(fd):
                    replacement = os.open(path, os.O_RDONLY)
                    if replacement != fd:
                        os.dup2(replacement, fd)
                        real_close(replacement)
                    replacements.append(fd)

                def fail_after_construction(fd, *args, **kwargs):
                    descriptors.append(fd)
                    stream = fdopen(fd, *args, **kwargs)
                    streams.append(stream)
                    stream.close()
                    # The old implementation lets stream teardown close fd.
                    # Reuse it immediately to expose the unsafe second close.
                    try:
                        os.fstat(fd)
                    except OSError as exc:
                        if exc.errno != errno.EBADF:
                            raise
                        replace_closed_fd(fd)
                    raise error

                def close_owned_fd(fd):
                    real_close(fd)
                    if not replacements:
                        replace_closed_fd(fd)

                try:
                    with patch.object(native.os, "fdopen", side_effect=fail_after_construction), \
                            patch.object(native.os, "close", side_effect=close_owned_fd) as close:
                        with self.assertRaises(type(error)) as caught:
                            native.read_json(path)
                        self.assertIs(caught.exception, error)
                        close.assert_called_once_with(descriptors[0])
                    self.assertTrue(streams[0].closed)
                    self.assertEqual(replacements, descriptors)
                    os.fstat(replacements[0])
                finally:
                    for fd in replacements:
                        try:
                            real_close(fd)
                        except OSError:
                            pass


if __name__ == "__main__":
    unittest.main()
