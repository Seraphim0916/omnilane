import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class NativeErrorRedactionTests(unittest.TestCase):
    def assert_error_output(self, error, message, json_mode):
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as home:
            argv = ["native.py", "job", "--home", home]
            if json_mode:
                argv.append("--json")
            argv.extend(["status", "20261001-120000-1-2"])
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(native, "job_command", side_effect=error) as command, \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(native.main(), 2)
            command.assert_called_once()
        out, err = stdout.getvalue(), stderr.getvalue()
        if json_mode:
            self.assertEqual(err, "")
            self.assertEqual(json.loads(out), {
                "schema_version": 1, "command": "status", "ok": False, "error": message})
        else:
            self.assertEqual(out, "")
            self.assertEqual(err, "omnilane: " + message + "\n")
        return out + err

    def test_parser_exception_matrix_hides_input(self):
        marker = "SYNTHETIC-PRIVATE-INPUT-B309"
        for error_type in (UnicodeError, RecursionError, TypeError, KeyError):
            for json_mode in (False, True):
                with self.subTest(error_type=error_type.__name__, json_mode=json_mode):
                    output = self.assert_error_output(
                        error_type(marker), "invalid or inaccessible native protocol input/state", json_mode)
                    self.assertNotIn(marker, output)

    def test_plain_value_error_preserves_controlled_reason(self):
        reason = "invalid native state"
        for json_mode in (False, True):
            with self.subTest(json_mode=json_mode):
                output = self.assert_error_output(ValueError(reason), reason, json_mode)
                self.assertIn(reason, output)


if __name__ == "__main__":
    unittest.main()
