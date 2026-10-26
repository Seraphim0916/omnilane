import offline_env  # Activate suite isolation for direct file execution.
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance


class BrokenStream(io.BytesIO):
    def read(self, size=-1):
        raise OSError("header read failed")


class BinaryStreamCleanupTests(unittest.TestCase):
    def check_stream(self, stream, expected_kind):
        self.addCleanup(stream.close)
        target = Mock(spec=Path)
        target.open.return_value = stream
        runner = Mock()
        with patch.object(Path, "resolve", return_value=target), patch.object(sys, "platform", "darwin"):
            result = cli_provenance.facts(Path("binary"), runner=runner)
        self.assertEqual(result["kind"], expected_kind)
        self.assertFalse(result["valid"])
        self.assertTrue(stream.closed)
        target.open.assert_called_once_with("rb")
        runner.assert_not_called()

    def test_script_closes_stream_without_signature_calls(self):
        self.check_stream(io.BytesIO(b"#!/bin/sh\n"), "script")

    def test_unknown_header_closes_stream_without_signature_calls(self):
        self.check_stream(io.BytesIO(b"????rest"), "unverifiable")

    def test_read_error_closes_stream_without_signature_calls(self):
        self.check_stream(BrokenStream(b"data"), "unverifiable")

    def test_open_error_does_not_enter_or_close_unacquired_stream(self):
        target = Mock(spec=Path)
        with io.BytesIO(b"unused") as stream:
            target.open.return_value = stream
            target.open.side_effect = PermissionError("open denied")
            runner = Mock()
            with patch.object(Path, "resolve", return_value=target), patch.object(sys, "platform", "darwin"):
                result = cli_provenance.facts(Path("binary"), runner=runner)
            self.assertEqual(result["kind"], "unverifiable")
            self.assertFalse(result["valid"])
            self.assertFalse(stream.closed)
            target.open.assert_called_once_with("rb")
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
