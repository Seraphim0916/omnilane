import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "native_b263", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class MemoryStream(io.StringIO):
    def fileno(self):
        return 42


class FailingStream(MemoryStream):
    def __init__(self, error):
        super().__init__("")
        self.error = error

    def read(self, size=-1):
        raise self.error


class NativeReadCleanupTests(unittest.TestCase):
    def read_stream(self, stream):
        with patch.object(native.os, "open", return_value=42), \
                patch.object(native.os, "fdopen", return_value=stream), \
                patch.object(native.os, "fstat", return_value=SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600, st_size=2)):
            return native.read_json("memory.json")

    def test_valid_json_closes_stream(self):
        stream = MemoryStream("{}")
        self.assertEqual(self.read_stream(stream), {})
        self.assertTrue(stream.closed)

    def test_invalid_json_propagates_and_closes_stream(self):
        stream = MemoryStream("{broken")
        with self.assertRaises(json.JSONDecodeError):
            self.read_stream(stream)
        self.assertTrue(stream.closed)

    def test_decoding_failure_propagates_and_closes_stream(self):
        stream = io.TextIOWrapper(io.BytesIO(b"\xff"), encoding="utf-8")
        # TextIOWrapper over BytesIO has no OS descriptor; fstat is also mocked.
        with patch.object(stream, "fileno", return_value=42):
            with self.assertRaises(UnicodeDecodeError):
                self.read_stream(stream)
        self.assertTrue(stream.closed)

    def test_read_exception_is_preserved_and_stream_closed(self):
        error = OSError("fixture read failed")
        stream = FailingStream(error)
        with self.assertRaises(OSError) as caught:
            self.read_stream(stream)
        self.assertIs(caught.exception, error)
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
