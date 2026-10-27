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


class BackupStream:
    def __init__(self, stream, failure):
        self.stream = stream
        self.failure = failure
        self.exits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exits += 1
        return self.stream.__exit__(*args)

    def write(self, data):
        if self.failure == "write":
            raise OSError("backup write failed")
        return self.stream.write(data)

    def flush(self):
        if self.failure == "flush":
            raise OSError("backup flush failed")
        return self.stream.flush()

    def fileno(self):
        return self.stream.fileno()


class OriginBackupFailureTests(unittest.TestCase):
    def test_each_backup_failure_prevents_temporary_policy_and_replace(self):
        for failure in ("write", "flush", "fsync"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                old = dict(schema_version=1, host=socket.gethostname(), vendors={})
                raw = json.dumps(old).encode()
                path.write_bytes(raw)
                path.chmod(0o600)
                new = dict(old, vendors={"claude": {"channels": []}})
                streams = []
                fdopen = origin.os.fdopen

                def open_stream(fd, mode):
                    stream = fdopen(fd, mode)
                    if mode != "wb":
                        return stream
                    wrapped = BackupStream(stream, failure)
                    streams.append(wrapped)
                    return wrapped

                with patch.object(origin.os, "fdopen", side_effect=open_stream), \
                        patch.object(origin.os, "fsync", side_effect=OSError("backup fsync failed")) as sync, \
                        patch.object(origin.tempfile, "mkstemp") as temporary, \
                        patch.object(origin.os, "replace") as replace:
                    with self.assertRaisesRegex(OSError, "^backup " + failure + " failed$"):
                        origin._save(path, new)
                    temporary.assert_not_called()
                    replace.assert_not_called()
                    self.assertEqual(sync.call_count, int(failure == "fsync"))
                self.assertEqual(len(streams), 1)
                self.assertEqual(streams[0].exits, 1)
                self.assertTrue(streams[0].stream.closed)
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(list(Path(directory).glob("policy.json.tmp-*")), [])
                print(failure + ": temp 0 replace 0 stream_exit 1")


if __name__ == "__main__":
    unittest.main()
