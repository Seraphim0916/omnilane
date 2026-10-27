import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginWriteRaceTests(unittest.TestCase):
    def test_write_rechecks_original_bytes_and_cleans_own_temporary_file(self):
        data = dict(schema_version=1, host=socket.gethostname(), vendors={})
        for label, before, after in (
            ("created", None, b"other writer"),
            ("changed", b"original", b"other writer"),
            ("unchanged", b"original", b"original"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / "policy.json"
                temporary = str(path.parent / "policy.json.tmp-owned")
                manager = MagicMock()
                stream = manager.__enter__.return_value
                stream.fileno.return_value = 17
                with patch.object(origin, "_read", side_effect=[before, after]) as read, \
                        patch.object(origin.os, "open", return_value=16), \
                        patch.object(origin.os, "fdopen", return_value=manager), \
                        patch.object(origin.os, "fsync"), \
                        patch.object(origin.tempfile, "mkstemp", return_value=(17, temporary)), \
                        patch.object(origin.os, "replace") as replace, \
                        patch.object(origin.os.path, "exists", return_value=before != after), \
                        patch.object(origin.os, "unlink") as cleanup:
                    if before != after:
                        with self.assertRaisesRegex(origin.OriginError,
                                                    "^origin policy changed during write$"):
                            origin._save(path, data)
                        replace.assert_not_called()
                        cleanup.assert_called_once_with(temporary)
                    else:
                        origin._save(path, data)
                        replace.assert_called_once_with(temporary, path)
                        cleanup.assert_not_called()
                    self.assertEqual(read.call_args_list, [call(path), call(path)])
                    payload = (json.dumps(data, indent=2) + "\n").encode()
                    self.assertEqual(stream.write.call_args_list,
                                     ([call(before)] if before is not None else []) + [call(payload)])


if __name__ == "__main__":
    unittest.main()
