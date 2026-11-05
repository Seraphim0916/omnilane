import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "native_b316", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeRecordBytesTests(unittest.TestCase):
    def test_exact_utf8_record_boundaries(self):
        for size in (262144, 262145):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                value = {"text": "中文ASCII"}
                overhead = len((json.dumps(value, ensure_ascii=False,
                                          separators=(",", ":")) + "\n").encode("utf-8"))
                value["text"] += "a" * (size - overhead)
                expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
                self.assertEqual(len(expected.encode("utf-8")), size)
                self.assertLess(len(expected), size)
                path = Path(directory) / "native.json"
                with patch.object(native, "write_new") as write, \
                        patch.object(native.os, "replace") as replace:
                    if size == 262144:
                        native.atomic_json(path, value)
                        write.assert_called_once()
                        temporary, content = write.call_args.args
                        self.assertEqual(content, expected)
                        self.assertEqual(temporary.parent, path.parent)
                        self.assertNotEqual(temporary, path)
                        replace.assert_called_once_with(temporary, path)
                        print("262144 write 1 replace 1")
                    else:
                        with self.assertRaisesRegex(ValueError, "^public record too large$"):
                            native.atomic_json(path, value)
                        write.assert_not_called()
                        replace.assert_not_called()
                        print("262145 public record too large writes 0 replace 0")


if __name__ == "__main__":
    unittest.main()
