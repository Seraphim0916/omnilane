import offline_env  # Activate suite isolation for direct file execution.

import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as quota


class QuotaUtf8SizeTests(unittest.TestCase):
    def read_fixture(self, raw, reported_size, error=None):
        # BytesIO enforces the requested read bound even for a grown file.
        with io.BytesIO(raw) as contents:
            stream = MagicMock()
            stream.__enter__.return_value = stream
            stream.fileno.return_value = 102
            stream.read.side_effect = contents.read
            with patch.object(quota.os, "open", side_effect=[101, 102]), \
                    patch.object(quota.os, "close"), \
                    patch.object(quota.os, "fdopen", return_value=stream), \
                    patch.object(quota.os, "fstat", return_value=SimpleNamespace(
                        st_mode=stat.S_IFREG, st_size=reported_size)):
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        quota.read_snapshot("synthetic-quota")
                else:
                    self.assertEqual(quota.read_snapshot("synthetic-quota"),
                                     raw.decode("utf-8"))
            return stream, contents.tell()

    def test_exact_four_byte_character_and_byte_limits_are_allowed(self):
        raw = ("\U0001f331" * 65536).encode("utf-8")
        self.assertEqual(len(raw), 262144)
        stream, consumed = self.read_fixture(raw, len(raw))
        stream.read.assert_called_once_with(262145)
        self.assertEqual(consumed, 262144)

    def test_ascii_over_character_limit_is_rejected(self):
        raw = b"a" * 65537
        stream, consumed = self.read_fixture(raw, len(raw), "^oversized snapshot$")
        stream.read.assert_called_once_with(262145)
        self.assertEqual(consumed, 65537)

    def test_oversized_stat_prevents_read(self):
        stream, consumed = self.read_fixture(b"{}", 262145,
                                             "^unsafe or oversized snapshot$")
        stream.read.assert_not_called()
        self.assertEqual(consumed, 0)

    def test_growth_after_stat_is_rejected_by_bounded_read(self):
        # Invalid UTF-8 distinguishes the byte guard from decoding failures.
        stream, consumed = self.read_fixture(b"\xff" * 300000, 2,
                                             "^oversized snapshot$")
        stream.read.assert_called_once_with(262145)
        self.assertEqual(consumed, 262145)


if __name__ == "__main__":
    unittest.main()
