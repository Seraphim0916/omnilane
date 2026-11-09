import offline_env  # Activate suite isolation for direct file execution.

import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginReadLimitTests(unittest.TestCase):
    def test_exact_limit_and_one_byte_over_use_one_bounded_read(self):
        for size in (131072, 131073):
            with self.subTest(size=size):
                raw = b"x" * size
                stream = io.BytesIO(raw)
                read = Mock(wraps=stream.read)
                info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=42)
                with patch.object(origin.os, "open", return_value=17), \
                        patch.object(origin.os, "fdopen", return_value=stream), \
                        patch.object(stream, "fileno", return_value=17), \
                        patch.object(stream, "read", read), \
                        patch.object(origin.os, "fstat", return_value=info), \
                        patch.object(origin.os, "getuid", return_value=42), \
                        patch.object(origin.os, "close") as close:
                    if size == 131072:
                        self.assertEqual(origin._read(Path("policy.json")), raw)
                    else:
                        with self.assertRaisesRegex(origin.OriginError, "^origin policy too large$"):
                            origin._read(Path("policy.json"))
                read.assert_called_once_with(131073)
                close.assert_called_once_with(17)


if __name__ == "__main__":
    unittest.main()
