import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "native_b298", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeReplaceCleanupTests(unittest.TestCase):
    def test_replace_outcomes_preserve_cleanup_contract(self):
        for outcome in ("denied", "success", "already_absent"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "native.json"
                path.write_bytes(b"old target")
                temp = path.with_name(".native.json.fixture")
                real_replace, real_unlink = native.os.replace, Path.unlink
                error = PermissionError("replace-denied")

                def replace(source, target):
                    self.assertEqual((source, target), (temp, path))
                    self.assertEqual(source.read_text(), native.json_text({"new": True}))
                    self.assertEqual(target.read_bytes(), b"old target")
                    if outcome == "success":
                        return real_replace(source, target)
                    if outcome == "already_absent":
                        real_unlink(source)
                    raise error

                with patch.object(native.secrets, "token_hex", return_value="fixture"), \
                        patch.object(native, "write_new", wraps=native.write_new) as write, \
                        patch.object(native.os, "replace", side_effect=replace) as replacement, \
                        patch.object(Path, "unlink", autospec=True, side_effect=real_unlink) as unlink:
                    if outcome == "success":
                        native.atomic_json(path, {"new": True})
                    else:
                        with self.assertRaises(PermissionError) as caught:
                            native.atomic_json(path, {"new": True})
                        self.assertIs(caught.exception, error)
                    write.assert_called_once_with(temp, native.json_text({"new": True}))
                    replacement.assert_called_once_with(temp, path)
                    if outcome == "denied":
                        unlink.assert_called_once_with(temp)
                    else:
                        unlink.assert_not_called()
                self.assertFalse(temp.exists())
                expected = native.json_text({"new": True}).encode() if outcome == "success" else b"old target"
                self.assertEqual(path.read_bytes(), expected)
                self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
