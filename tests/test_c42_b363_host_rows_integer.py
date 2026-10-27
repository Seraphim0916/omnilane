import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import native_context


class HostRowsIntegerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.rows = self.base / "rows.json"

    def test_oversized_integer_is_controlled(self):
        self.rows.write_text('{"schema_version": ' + '9' * 5000 + ', "harnesses": {}}')
        with self.assertRaises(native_context.HostRowsError):
            native_context.load_host_rows(self.rows)

    def test_decoder_value_error_is_wrapped(self):
        self.rows.write_text('{}')
        with patch.object(native_context.json, "loads", side_effect=ValueError("integer limit")):
            with self.assertRaisesRegex(native_context.HostRowsError, "cannot read: integer limit"):
                native_context.load_host_rows(self.rows)

    def test_main_refuses_without_writing(self):
        self.rows.write_text('{"schema_version": ' + '9' * 5000 + ', "harnesses": {}}')
        out = self.base / "output" / "context.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        caller = lambda pid: (pid, ("claude", "claude-opus-5-5", "medium"), {})
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native_context.main([
                "--workdir", str(self.base), "--mode", "work",
                "--registry", str(ROOT / "config/aa-model-policy.json"),
                "--host-rows", str(self.rows), "--out", str(out)], read_caller=caller)
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invalid host rows file", stderr.getvalue())
        self.assertFalse(out.parent.exists())

    def test_normal_version_and_duplicate_keys_unchanged(self):
        self.rows.write_text('{"schema_version": 1, "harnesses": {}}')
        self.assertEqual(native_context.load_host_rows(self.rows), {})
        for raw, message in (
                ('{"schema_version": 2, "harnesses": {}}', "unsupported schema_version"),
                ('{"schema_version": 1, "schema_version": 1, "harnesses": {}}',
                 "^duplicate key in a JSON object$")):
            self.rows.write_text(raw)
            with self.assertRaisesRegex(native_context.HostRowsError, message):
                native_context.load_host_rows(self.rows)


if __name__ == "__main__":
    unittest.main()
