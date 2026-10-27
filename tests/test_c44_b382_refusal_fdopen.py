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

    def test_success_leaves_closing_to_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            streams = []
            original = os.fdopen

            def wrap(*args, **kwargs):
                stream = original(*args, **kwargs)
                streams.append(stream)
                return stream

            refusal = {"allowed": False, "message": "拒絕"}
            with patch.object(evidence.os, "fdopen", side_effect=wrap), \
                    patch.object(evidence.os, "close", wraps=os.close) as close:
                path = evidence.store(Path(directory), refusal, "test", "refused")
                close.assert_not_called()
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


if __name__ == "__main__":
    unittest.main()
