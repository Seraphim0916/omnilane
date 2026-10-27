import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class FirstUseSaveFailureTests(unittest.TestCase):
    def test_save_failures_return_false_and_success_persists(self):
        for error_type in (origin.OriginError, OSError, RuntimeError, None):
            with self.subTest(error=error_type), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                path = root / "policy.json"
                current = {"kind": "script"}
                with patch.object(origin, "_save", wraps=origin._save) as save:
                    if error_type is not None:
                        save.side_effect = error_type("injected save failure")
                    result = origin.record_first_use("claude", current, root / "cli", path=path)
                    self.assertIs(result, error_type is None)
                    save.assert_called_once()
                    saved_path, data = save.call_args[0]
                    self.assertEqual(saved_path, path)
                    origin.validate(data)
                    channel, = data["vendors"]["claude"]["channels"]
                    self.assertEqual(channel["kind"], "location")
                    self.assertEqual(channel["accepts"], "script")
                    self.assertEqual(channel["approved_via"], "first-use")
                    self.assertEqual(channel["install_family"], str(root / "cli"))
                if error_type is None:
                    self.assertEqual(origin.load(path), data)
                else:
                    self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
