import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "native_b299", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativePublishLockTypeTests(unittest.TestCase):
    def test_descriptor_type_checked_before_lock_and_publication(self):
        for mode in (stat.S_IFDIR, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFREG):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                staging = root / ".native-stage-job-fixture"
                job = root / "job"
                real_fdopen = native.os.fdopen
                streams = []

                def fdopen(*args, **kwargs):
                    stream = real_fdopen(*args, **kwargs)
                    if (args[1] if len(args) > 1 else kwargs.get("mode", "r")) == "r+":
                        streams.append(stream)
                    return stream

                def fstat(fd):
                    self.assertEqual(fd, streams[0].fileno())
                    self.assertTrue((staging / "native.json").is_file())
                    return SimpleNamespace(st_mode=mode | 0o600)

                with patch.object(native.secrets, "token_hex", return_value="fixture"), \
                        patch.object(native.os, "fdopen", side_effect=fdopen), \
                        patch.object(native.os, "fstat", side_effect=fstat) as inspect, \
                        patch.object(native.fcntl, "flock", wraps=native.fcntl.flock) as flock, \
                        patch.object(native.os, "rename", wraps=native.os.rename) as rename, \
                        patch.object(native, "cleanup_staging_dir", wraps=native.cleanup_staging_dir) as cleanup:
                    if mode == stat.S_IFREG:
                        native.publish_job(root, "job", {"task": "fixture"}, {})
                        flock.assert_called_once_with(streams[0], native.fcntl.LOCK_EX)
                        rename.assert_called_once_with(staging, job)
                    else:
                        with self.assertRaisesRegex(ValueError, "^invalid native publication lock$"):
                            native.publish_job(root, "job", {"task": "fixture"}, {})
                        flock.assert_not_called()
                        rename.assert_not_called()
                        self.assertFalse(job.exists())
                    inspect.assert_called_once()
                    cleanup.assert_called_once_with(staging)
                self.assertEqual(len(streams), 1)
                self.assertTrue(streams[0].closed)
                self.assertFalse(staging.exists())
                if mode == stat.S_IFREG:
                    self.assertEqual(native.read_json(job / "native.json"), {"task": "fixture"})
                    self.assertEqual((job / "task.txt").read_text(), "fixture")

    def test_real_nonregular_lock_open_cleans_staging(self):
        for kind in ("directory", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                lock = root / ".native-publish.lock"
                staging = root / ".native-stage-job-fixture"
                if kind == "directory":
                    lock.mkdir()
                else:
                    native.os.mkfifo(lock)
                real_open = native.os.open
                opened = []

                def open_lock(path, flags, *args, **kwargs):
                    if path == lock:
                        opened.append(path)
                        self.assertTrue((staging / "native.json").is_file())
                        # Fail before a potentially blocking FIFO open.
                        self.assertTrue(flags & native.os.O_NONBLOCK)
                    return real_open(path, flags, *args, **kwargs)

                with patch.object(native.secrets, "token_hex", return_value="fixture"), \
                        patch.object(native.os, "open", side_effect=open_lock), \
                        patch.object(native.fcntl, "flock") as flock, \
                        patch.object(native.os, "rename") as rename, \
                        patch.object(native, "cleanup_staging_dir", wraps=native.cleanup_staging_dir) as cleanup:
                    with self.assertRaises((OSError, ValueError)):
                        native.publish_job(root, "job", {"task": "fixture"}, {})
                    cleanup.assert_called_once_with(staging)
                    flock.assert_not_called()
                    rename.assert_not_called()
                self.assertEqual(opened, [lock])
                self.assertEqual(list(root.iterdir()), [lock])


if __name__ == "__main__":
    unittest.main()
