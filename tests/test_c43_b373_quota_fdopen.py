import offline_env  # Activate suite isolation for direct file execution.

import os
import stat
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as qp


class QuotaFdopenTests(unittest.TestCase):
    def test_original_failure_survives_close_failure(self):
        for stage in ("construction", "verification", "read"):
            with self.subTest(stage=stage):
                failure = RuntimeError("synthetic " + stage + " failure")
                stream = MagicMock()
                stream.__enter__.return_value = stream
                stream.fileno.return_value = 71
                if stage == "read":
                    stream.read.side_effect = failure
                with patch.object(qp.os, "open", side_effect=[70, 71]), \
                        patch.object(qp.os, "fdopen", return_value=stream,
                                     side_effect=failure if stage == "construction" else None), \
                        patch.object(qp.os, "fstat", return_value=MagicMock(
                            st_mode=stat.S_IFREG, st_size=0),
                            side_effect=failure if stage == "verification" else None), \
                        patch.object(qp.os, "close", side_effect=[None, OSError("close failure")]) as close:
                    with self.assertRaises(RuntimeError) as caught:
                        qp.read_snapshot("quota")
                self.assertIs(caught.exception, failure)
                self.assertEqual([call.args[0] for call in close.call_args_list], [70, 71])

    def test_success_path_close_failure_surfaces(self):
        failure = OSError("close failure")
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.fileno.return_value = 71
        stream.read.return_value = b"{}"
        with patch.object(qp.os, "open", side_effect=[70, 71]), \
                patch.object(qp.os, "fdopen", return_value=stream), \
                patch.object(qp.os, "fstat", return_value=MagicMock(
                    st_mode=stat.S_IFREG, st_size=2)), \
                patch.object(qp.os, "close", side_effect=[None, failure]) as close:
            with self.assertRaises(OSError) as caught:
                qp.read_snapshot("quota")
        self.assertIs(caught.exception, failure)
        self.assertEqual([call.args[0] for call in close.call_args_list], [70, 71])

    def test_constructor_failure_closes_leaf_once(self):
        failure = OSError("synthetic fdopen failure")
        with patch.object(qp.os, "open", side_effect=[70, 71]), \
                patch.object(qp.os, "fdopen", side_effect=failure), \
                patch.object(qp.os, "close") as close:
            with self.assertRaises(OSError) as caught:
                qp.read_snapshot("quota")
        self.assertIs(caught.exception, failure)
        self.assertEqual([call.args[0] for call in close.call_args_list], [70, 71])

    def test_partial_constructor_cleanup_cannot_close_reused_descriptor(self):
        real_fdopen, real_close = os.fdopen, os.close
        with tempfile.TemporaryDirectory() as directory:
            leaf = Path(directory) / "codex.json"
            leaf.write_text('{}', encoding="utf-8")
            reused = []
            def failing_fdopen(fd, *args, **kwargs):
                stream = real_fdopen(fd, *args, **kwargs)
                stream.close()
                # A constructor may clean its partially created stream before
                # raising. An owning stream would free fd for immediate reuse.
                reused.append(os.open(str(leaf), os.O_RDONLY))
                if kwargs.get("closefd", True) and reused[0] != fd:
                    os.dup2(reused[0], fd)
                    real_close(reused[0])
                    reused[0] = fd
                raise OSError("partial constructor failure")
            try:
                with patch.object(qp.os, "fdopen", side_effect=failing_fdopen), \
                        patch.object(qp.os, "close", wraps=real_close) as close:
                    with self.assertRaisesRegex(OSError, "partial constructor"):
                        qp.read_snapshot(directory)
                self.assertEqual(os.read(reused[0], 2), b"{}")
                self.assertEqual(close.call_count, 2)
            finally:
                for fd in reused:
                    real_close(fd)

    def test_success_and_read_failure_close_stream_and_raw_fd(self):
        real_fdopen, real_close = os.fdopen, os.close
        for raw, error in [(b'{"ok":true}', False), (b'\xff', True)]:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "codex.json").write_bytes(raw)
                streams = []
                def capture(fd, *args, **kwargs):
                    stream = real_fdopen(fd, *args, **kwargs)
                    streams.append((fd, stream))
                    return stream
                with patch.object(qp.os, "fdopen", side_effect=capture), \
                        patch.object(qp.os, "close", wraps=real_close) as close:
                    if error:
                        with self.assertRaises(UnicodeDecodeError):
                            qp.read_snapshot(directory)
                    else:
                        self.assertEqual(qp.read_snapshot(directory), raw.decode("utf-8"))
                self.assertTrue(streams[0][1].closed)
                self.assertEqual(close.call_count, 2)
                self.assertEqual(
                    [call.args[0] for call in close.call_args_list].count(streams[0][0]), 1)
                with self.assertRaises(OSError):
                    os.fstat(streams[0][0])


if __name__ == "__main__":
    unittest.main()
