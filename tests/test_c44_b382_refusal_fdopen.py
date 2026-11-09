import offline_env  # Activate suite isolation for direct file execution.

import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import refusal_evidence as evidence


class RefusalFdopenTests(unittest.TestCase):
    def test_wrapper_failure_closes_acquired_descriptor_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            descriptors = []
            original = os.open

            def acquire(*args, **kwargs):
                fd = original(*args, **kwargs)
                descriptors.append(fd)
                return fd

            failure = OSError("synthetic fdopen failure")
            try:
                with patch.object(evidence.os, "open", side_effect=acquire), \
                        patch.object(evidence.os, "fdopen", side_effect=failure), \
                        patch.object(evidence.os, "close", wraps=os.close) as close:
                    with self.assertRaises(OSError) as caught:
                        evidence.store(home, {"allowed": False}, "test", "refused")
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(len(descriptors), 1)
                    close.assert_called_once_with(descriptors[0])
                    with self.assertRaises(OSError) as closed:
                        os.fstat(descriptors[0])
                    self.assertEqual(closed.exception.errno, errno.EBADF)
            finally:
                for fd in descriptors:
                    try:
                        os.fstat(fd)
                    except OSError:
                        pass
                    else:
                        os.close(fd)

    def test_success_closes_stream_and_owned_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            streams = []
            original = os.fdopen
            descriptors = []

            def wrap(*args, **kwargs):
                self.assertIs(kwargs.get("closefd"), False)
                descriptors.append(args[0])
                stream = original(*args, **kwargs)
                streams.append(stream)
                return stream

            refusal = {"allowed": False, "message": "拒絕"}
            with patch.object(evidence.os, "fdopen", side_effect=wrap), \
                    patch.object(evidence.os, "close", wraps=os.close) as close:
                path = evidence.store(Path(directory), refusal, "test", "refused")
                close.assert_called_once_with(descriptors[0])
            self.assertEqual(len(streams), 1)
            self.assertTrue(streams[0].closed)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["refusal"], refusal)
            self.assertEqual(record["lane"], "test")
            self.assertEqual(record["headline"], "refused")

    def test_open_failure_propagates_without_closing(self):
        with tempfile.TemporaryDirectory() as directory:
            failure = FileExistsError("exclusive create failed")
            with patch.object(evidence.os, "open", side_effect=failure), \
                    patch.object(evidence.os, "fdopen") as fdopen, \
                    patch.object(evidence.os, "close") as close:
                with self.assertRaises(FileExistsError) as caught:
                    evidence.store(Path(directory), {"allowed": False}, "test", "refused")
                self.assertIs(caught.exception, failure)
                close.assert_not_called()
                fdopen.assert_not_called()



    def test_partial_construction_cleanup_does_not_close_reused_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
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
                with patch.object(evidence.os, "fdopen", side_effect=construct), \
                        patch.object(evidence.os, "close", side_effect=close_and_reuse) as close:
                    with self.assertRaises(RuntimeError) as caught:
                        evidence.store(Path(directory), {"allowed": False}, "test", "refused")
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
            original_close = os.close
            failure = KeyboardInterrupt("original construction failure")

            def close_then_fail(fd):
                original_close(fd)
                raise OSError("cleanup close failed")

            with patch.object(evidence.os, "fdopen", side_effect=failure), \
                    patch.object(evidence.os, "close", side_effect=close_then_fail) as close:
                with self.assertRaises(KeyboardInterrupt) as caught:
                    evidence.store(Path(directory), {"allowed": False}, "test", "refused")
                self.assertIs(caught.exception, failure)
                self.assertEqual(close.call_count, 1)

    def test_successful_operation_surfaces_close_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            original_close = os.close
            failure = OSError("successful operation close failed")

            def close_then_fail(fd):
                original_close(fd)
                raise failure

            with patch.object(evidence.os, "close", side_effect=close_then_fail) as close:
                with self.assertRaises(OSError) as caught:
                    evidence.store(Path(directory), {"allowed": False}, "test", "refused")
                self.assertIs(caught.exception, failure)
                self.assertEqual(close.call_count, 1)


if __name__ == "__main__":
    unittest.main()
