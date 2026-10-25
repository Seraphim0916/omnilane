import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import socket
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class OriginDecodeErrorsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "fixture-policy.json"

    def test_invalid_encoding_and_deep_json_become_origin_errors(self):
        # The C JSON decoder can have a separate recursion threshold from Python.
        # Stay below MAX_BYTES without changing either runtime recursion limit.
        depth = 10000
        cases = (("invalid UTF-8", b'{"host":"\xff"}', UnicodeDecodeError),
                 ("deep JSON", b"[" * depth + b"0" + b"]" * depth, RecursionError))
        for name, raw, underlying in cases:
            with self.subTest(case=name):
                # Verify the fixture actually reaches the intended decoder failure.
                with self.assertRaises(underlying):
                    json.loads(raw)
                with patch.object(op, "_read", return_value=raw) as read, \
                        patch.object(op, "_save") as save:
                    try:
                        op.load(self.path)
                    except op.OriginError as error:
                        self.assertIs(type(error), op.OriginError)
                        self.assertTrue(str(error).startswith("invalid origin policy: "))
                        self.assertIsNone(error.__cause__)
                        self.assertTrue(error.__suppress_context__)
                        rendered = "".join(traceback.format_exception(error))
                        self.assertNotIn(underlying.__name__ + ":", rendered)
                    else:
                        self.fail("load accepted malformed policy bytes")
                    read.assert_called_once_with(self.path)
                    save.assert_not_called()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_valid_utf8_policy_loads_without_writes(self):
        data = {"schema_version": 1, "host": socket.gethostname(), "vendors": {
            "codex": {"channels": [{"kind": "location", "accepts": "script",
                "install_family": "/育苗/工具\u2028版本",
                "approved_at": "2026-10-26T00:00:00+00:00", "approved_via": "operator"}]}}}
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        with patch.object(op, "_read", return_value=raw) as read, \
                patch.object(op, "_save") as save:
            self.assertEqual(op.load(self.path), data)
            read.assert_called_once_with(self.path)
            save.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
