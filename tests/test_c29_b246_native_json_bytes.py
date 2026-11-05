import offline_env  # Activate suite isolation for direct file execution.

import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class NativeJsonBytesTests(unittest.TestCase):
    def read_underestimated(self, raw, expected=None, error=None):
        stream = io.StringIO(raw)
        self.addCleanup(stream.close)
        stream.fileno = mock.Mock(return_value=123)
        stream.read = mock.Mock(wraps=stream.read)
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=0)
        with mock.patch.object(native.os, "open", return_value=123), \
                mock.patch.object(native.os, "fdopen", return_value=stream), \
                mock.patch.object(native.os, "fstat", return_value=info) as fstat:
            if error:
                with self.assertRaisesRegex(ValueError, "^" + error + "$"):
                    native.read_json("fixture.json")
            else:
                self.assertEqual(native.read_json("fixture.json"), expected)
        fstat.assert_called_once_with(123)
        stream.read.assert_called_once_with(native.MAX_BYTES + 1)
        self.assertTrue(stream.closed)

    def test_utf8_exact_byte_limit_is_accepted(self):
        count, padding = divmod(native.MAX_BYTES - 2, 3)
        value = "中" * count + "a" * padding
        raw = json.dumps(value, ensure_ascii=False)
        self.assertEqual(len(raw.encode("utf-8")), native.MAX_BYTES)
        self.assertLess(len(raw), native.MAX_BYTES)
        self.read_underestimated(raw, expected=value)

    def test_utf8_one_byte_over_limit_is_rejected_and_closed(self):
        count, padding = divmod(native.MAX_BYTES - 2, 3)
        raw = json.dumps("中" * count + "a" * (padding + 1), ensure_ascii=False)
        self.assertEqual(len(raw.encode("utf-8")), native.MAX_BYTES + 1)
        self.assertLess(len(raw), native.MAX_BYTES)
        self.read_underestimated(raw, error="JSON file too large")

    def test_90000_chinese_characters_are_rejected_and_closed(self):
        raw = json.dumps("中" * 90000, ensure_ascii=False)
        self.assertGreater(len(raw.encode("utf-8")), native.MAX_BYTES)
        self.assertLess(len(raw), native.MAX_BYTES)
        self.read_underestimated(raw, error="JSON file too large")

    def test_read_is_bounded_even_when_stream_has_more_characters(self):
        raw = json.dumps("a" * (native.MAX_BYTES * 2))
        self.read_underestimated(raw, error="JSON file too large")


if __name__ == "__main__":
    unittest.main()
