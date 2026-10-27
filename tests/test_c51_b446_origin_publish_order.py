import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class PublishStream:
    def __init__(self, stream, events, failure):
        self.stream = stream
        self.events = events
        self.failure = failure
        self.exits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exits += 1
        result = self.stream.__exit__(*args)
        self.events.append("close")
        return result

    def write(self, data):
        self.events.append("write")
        if self.failure == "write":
            raise OSError("temporary write failed")
        return self.stream.write(data)

    def flush(self):
        self.events.append("flush")
        if self.failure == "flush":
            raise OSError("temporary flush failed")
        return self.stream.flush()

    def fileno(self):
        return self.stream.fileno()


class OriginPublishOrderTests(unittest.TestCase):
    def test_publish_follows_write_flush_sync_and_close(self):
        self.run_save(None)

    def test_each_temporary_failure_closes_and_cleans_only_own_file(self):
        for failure in ("write", "flush", "fsync"):
            with self.subTest(failure=failure):
                self.run_save(failure)

    def run_save(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            old = dict(schema_version=1, host=socket.gethostname(), vendors={})
            raw = json.dumps(old).encode()
            path.write_bytes(raw)
            path.chmod(0o600)
            new = dict(old, vendors={"codex": {"channels": []}})
            unrelated = Path(directory) / "policy.json.tmp-unrelated"
            unrelated.write_bytes(b"another writer")
            events, created, streams = [], [], []
            mkstemp, fdopen = origin.tempfile.mkstemp, origin.os.fdopen
            fsync, replace, unlink = origin.os.fsync, origin.os.replace, origin.os.unlink

            def make_temporary(*args, **kwargs):
                result = mkstemp(*args, **kwargs)
                created.append(result)
                return result

            def open_stream(fd, mode):
                stream = fdopen(fd, mode)
                if mode != "wb" or not created or fd != created[0][0]:
                    return stream
                wrapped = PublishStream(stream, events, failure)
                streams.append(wrapped)
                return wrapped

            def sync(fd):
                if created and fd == created[0][0]:
                    events.append("fsync")
                    if failure == "fsync":
                        raise OSError("temporary fsync failed")
                return fsync(fd)

            def publish(source, target):
                self.assertTrue(streams[0].stream.closed)
                self.assertEqual(json.loads(Path(source).read_bytes()), new)
                events.append("replace")
                return replace(source, target)

            with patch.object(origin.tempfile, "mkstemp", side_effect=make_temporary), \
                    patch.object(origin.os, "fdopen", side_effect=open_stream), \
                    patch.object(origin.os, "fsync", side_effect=sync), \
                    patch.object(origin.os, "replace", side_effect=publish) as replacing, \
                    patch.object(origin.os, "unlink", wraps=unlink) as cleanup:
                if failure:
                    with self.assertRaisesRegex(OSError, "^temporary " + failure + " failed$"):
                        origin._save(path, new)
                    replacing.assert_not_called()
                    self.assertEqual(len(created), 1)
                    cleanup.assert_called_once_with(created[0][1])
                    self.assertEqual(path.read_bytes(), raw)
                    expected = {
                        "write": ["write", "close"],
                        "flush": ["write", "flush", "close"],
                        "fsync": ["write", "flush", "fsync", "close"],
                    }
                    self.assertEqual(events, expected[failure])
                else:
                    origin._save(path, new)
                    self.assertEqual(len(created), 1)
                    replacing.assert_called_once_with(created[0][1], path)
                    cleanup.assert_not_called()
                    self.assertEqual(events, ["write", "flush", "fsync", "close", "replace"])
                    self.assertEqual(origin.load(path), new)
                self.assertEqual(len(streams), 1)
                self.assertEqual(streams[0].exits, 1)
                self.assertTrue(streams[0].stream.closed)
                self.assertFalse(Path(created[0][1]).exists())
                self.assertEqual(unrelated.read_bytes(), b"another writer")
            if failure:
                print(failure + ": replace 0 cleanup 1 stream_exit 1")
            else:
                print(events)


if __name__ == "__main__":
    unittest.main()
