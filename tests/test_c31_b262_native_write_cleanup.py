import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location(
    "native_b262", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeWriteCleanupTests(unittest.TestCase):
    def test_created_temp_is_removed_on_write_flush_or_sync_failure(self):
        for stage in ("write", "flush", "fsync"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "native.json"
                path.write_text('{"old":true}\n', encoding="utf-8")
                original = path.read_bytes()
                temp = path.with_name(".native.json.fixture")
                real_fdopen = native.os.fdopen
                streams = []
                error = OSError("fixture disk full after creation")

                def fdopen(*args, **kwargs):
                    stream = real_fdopen(*args, **kwargs)
                    streams.append(stream)
                    proxy = MagicMock(wraps=stream)
                    proxy.__enter__.return_value = proxy
                    proxy.__exit__.side_effect = stream.__exit__
                    if stage != "fsync":
                        getattr(proxy, stage).side_effect = error
                    return proxy

                with patch.object(native.secrets, "token_hex", return_value="fixture"), \
                        patch.object(native.os, "fdopen", side_effect=fdopen), \
                        patch.object(native.os, "fsync", side_effect=error if stage == "fsync" else None), \
                        patch.object(native.os, "replace") as replace:
                    with self.assertRaises(OSError) as caught:
                        native.atomic_json(path, {"new": True})
                self.assertIs(caught.exception, error)
                self.assertEqual(path.read_bytes(), original)
                replace.assert_not_called()
                self.assertTrue(streams[0].closed)
                self.assertFalse(temp.exists(), stage + " leaked the created temp")

    def test_exclusive_collision_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.json"
            path.write_bytes(b"old state")
            temp = path.with_name(".native.json.fixture")
            temp.write_bytes(b"owned by another writer")
            with patch.object(native.secrets, "token_hex", return_value="fixture"), \
                    patch.object(native.os, "replace") as replace, \
                    patch.object(Path, "unlink") as unlink:
                with self.assertRaises(FileExistsError):
                    native.atomic_json(path, {"new": True})
            replace.assert_not_called()
            unlink.assert_not_called()
            self.assertEqual(temp.read_bytes(), b"owned by another writer")
            self.assertEqual(path.read_bytes(), b"old state")

    def test_success_replaces_state_without_leftover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.json"
            path.write_bytes(b"old state")
            native.atomic_json(path, {"new": True})
            self.assertEqual(native.read_json(path), {"new": True})
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
