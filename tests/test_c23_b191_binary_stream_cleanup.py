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

    def check_macho_stream(self, error=None):
        stream = io.BytesIO(b"\xcf\xfa\xed\xfe" + b"\0" * 28)
        self.addCleanup(stream.close)
        target = Mock(spec=Path)
        target.open.return_value = stream

        def run(*args, **kwargs):
            self.assertTrue(stream.closed, "header stream must close before codesign")
            if error is not None:
                raise error
            return Mock(returncode=0, stderr="Identifier=test.binary\nSignature=adhoc\n")

        runner = Mock(side_effect=run)
        with patch.object(Path, "resolve", return_value=target), patch.object(sys, "platform", "darwin"):
            result = cli_provenance.facts(Path("binary"), runner=runner)
        self.assertTrue(stream.closed)
        target.open.assert_called_once_with("rb")
        self.assertEqual(runner.call_count, 1 if error else 2)
        self.assertEqual(runner.call_args_list[0].args[0],
                         ["codesign", "--verify", "--strict", str(target)])
        self.assertEqual(result["kind"], "unverifiable" if error else "adhoc")
        self.assertEqual(result["valid"], error is None)

    def test_macho_closes_stream_before_successful_runner(self):
        self.check_macho_stream()

    def test_macho_closes_stream_before_runner_exception(self):
        self.check_macho_stream(OSError("codesign unavailable"))

    def test_open_error_returns_unverifiable_without_signature_calls(self):
        target = Mock(spec=Path)
        target.open.side_effect = PermissionError("open denied")
        runner = Mock()
        with patch.object(Path, "resolve", return_value=target), patch.object(sys, "platform", "darwin"):
            result = cli_provenance.facts(Path("binary"), runner=runner)
        self.assertEqual(result["kind"], "unverifiable")
        self.assertFalse(result["valid"])
        target.open.assert_called_once_with("rb")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
