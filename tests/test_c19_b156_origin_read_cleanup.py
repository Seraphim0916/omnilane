import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginReadCleanupTests(unittest.TestCase):
    def test_acquired_stream_exits_once_on_every_failure(self):
        unsafe = "origin policy must be an owned regular 0600 file"
        cases = (
            ("permissions", stat.S_IFREG | 0o644, 42, b"{}", None, origin.OriginError, unsafe, False),
            ("file-type", stat.S_IFDIR | 0o600, 42, b"{}", None, origin.OriginError, unsafe, False),
            ("owner", stat.S_IFREG | 0o600, 43, b"{}", None, origin.OriginError, unsafe, False),
            ("size", stat.S_IFREG | 0o600, 42, b"x" * (origin.MAX_BYTES + 1), None,
             origin.OriginError, "origin policy too large", True),
            ("read-error", stat.S_IFREG | 0o600, 42, b"", OSError("read failed"),
             OSError, "read failed", True),
            ("stat-error", stat.S_IFREG | 0o600, 42, b"", None,
             OSError, "stat failed", False),
        )
        for name, mode, uid, raw, read_error, error_type, message, reads in cases:
            with self.subTest(case=name):
                manager = MagicMock()
                stream = manager.__enter__.return_value
                stream.fileno.return_value = 17
                stream.read.return_value = raw
                stream.read.side_effect = read_error
                manager.__exit__.return_value = False
                info = SimpleNamespace(st_mode=mode, st_uid=uid)
                with patch.object(origin.os, "open", return_value=17), \
                        patch.object(origin.os, "fdopen", return_value=manager) as fdopen, \
                        patch.object(origin.os, "fstat", return_value=info,
                                     side_effect=OSError("stat failed") if name == "stat-error" else None), \
                        patch.object(origin.os, "getuid", return_value=42):
                    with self.assertRaisesRegex(error_type, "^" + message + "$") as caught:
                        origin._read(Path("policy.json"))
                fdopen.assert_called_once_with(17, "rb")
                manager.__enter__.assert_called_once_with()
                manager.__exit__.assert_called_once()
                exit_args = manager.__exit__.call_args[0]
                self.assertIs(exit_args[0], error_type)
                self.assertIs(exit_args[1], caught.exception)
                if reads:
                    stream.read.assert_called_once_with(origin.MAX_BYTES + 1)
                else:
                    stream.read.assert_not_called()

    def test_valid_stream_returns_bytes_and_exits_once(self):
        manager = MagicMock()
        stream = manager.__enter__.return_value
        stream.fileno.return_value = 17
        stream.read.return_value = b"{}"
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=42)
        with patch.object(origin.os, "open", return_value=17), \
                patch.object(origin.os, "fdopen", return_value=manager), \
                patch.object(origin.os, "fstat", return_value=info), \
                patch.object(origin.os, "getuid", return_value=42):
            self.assertEqual(origin._read(Path("policy.json")), b"{}")
        stream.read.assert_called_once_with(origin.MAX_BYTES + 1)
        manager.__exit__.assert_called_once_with(None, None, None)


if __name__ == "__main__":
    unittest.main()
