import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class FdopenTests(unittest.TestCase):
    def test_failed_transfer_closes_once_and_preserves_error(self):
        for error in (OSError("fdopen-failed"), MemoryError("fdopen-failed")):
            with self.subTest(error=type(error).__name__):
                with patch.object(native.os, "open", return_value=42), \
                        patch.object(native.os, "fdopen", side_effect=error), \
                        patch.object(native.os, "close") as close:
                    with self.assertRaises(type(error)) as caught:
                        native.read_json("fixture.json")
                    self.assertIs(caught.exception, error)
                    close.assert_called_once_with(42)

    def test_close_error_does_not_replace_original(self):
        error = OSError("fdopen-failed")
        with patch.object(native.os, "open", return_value=42), \
                patch.object(native.os, "fdopen", side_effect=error), \
                patch.object(native.os, "close", side_effect=OSError("close-failed")) as close:
            with self.assertRaises(OSError) as caught:
                native.read_json("fixture.json")
            self.assertIs(caught.exception, error)
            close.assert_called_once_with(42)

    def test_success_transfers_real_descriptor_to_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.json"
            path.write_text('{"ok": true}')
            fdopen = os.fdopen
            streams = []

            def capture(*args, **kwargs):
                stream = fdopen(*args, **kwargs)
                streams.append(stream)
                return stream

            with patch.object(native.os, "fdopen", side_effect=capture), \
                    patch.object(native.os, "close", wraps=os.close) as close:
                self.assertEqual(native.read_json(path), {"ok": True})
                close.assert_not_called()
            self.assertEqual(len(streams), 1)
            self.assertTrue(streams[0].closed)


if __name__ == "__main__":
    unittest.main()
