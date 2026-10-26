import offline_env  # Activate suite isolation for direct file execution.

import io
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class DescriptorStream(io.StringIO):
    def __init__(self):
        super().__init__('{"valid": true}')
        self.reads = 0
        self.closes = 0

    def fileno(self):
        return 123

    def read(self, *args):
        self.reads += 1
        return super().read(*args)

    def close(self):
        self.closes += 1
        super().close()


class NativeFileKindTests(unittest.TestCase):
    def test_nonregular_files_rejected_before_read_and_closed(self):
        for kind in (stat.S_IFDIR, stat.S_IFIFO, stat.S_IFSOCK):
            with self.subTest(kind=kind):
                stream = DescriptorStream()
                with mock.patch.object(native.os, "open", return_value=123) as opened, \
                        mock.patch.object(native.os, "fdopen", return_value=stream) as wrapped, \
                        mock.patch.object(native.os, "fstat", return_value=SimpleNamespace(
                            st_mode=kind | 0o600, st_size=0)) as inspected:
                    with self.assertRaisesRegex(ValueError, "^invalid JSON file$"):
                        native.read_json("descriptor-fixture.json")
                opened.assert_called_once_with(
                    "descriptor-fixture.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                wrapped.assert_called_once_with(123, "r", encoding="utf-8")
                inspected.assert_called_once_with(123)
                self.assertEqual(stream.reads, 0)
                self.assertEqual(stream.closes, 1)
                self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
