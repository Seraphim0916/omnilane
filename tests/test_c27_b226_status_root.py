import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import verification_status as status


class StatusRootTests(unittest.TestCase):
    def test_non_object_reader_results_are_controlled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text("[]")
            for value in ([], [1], None, True, False, 1, 1.5, "text"):
                with self.subTest(value=value), \
                        patch.object(status, "status_path", return_value=path), \
                        patch.object(aa_policy, "_read_json", return_value=(value, None)), \
                        patch.object(aa_policy, "atomic_json") as writer:
                    with self.assertRaisesRegex(aa_policy.PolicyError,
                                                "^invalid local verification status$"):
                        status.load()
                    writer.assert_not_called()
                    self.assertEqual(path.read_text(), "[]")

    def test_real_invalid_files_are_not_overwritten_by_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            with patch.object(status, "status_path", return_value=path):
                for value in ([], None, True, 7, 2.5, "text"):
                    with self.subTest(value=value):
                        original = json.dumps(value)
                        path.write_text(original)
                        with self.assertRaises(aa_policy.PolicyError):
                            status.record(10, vendors=["codex"])
                        self.assertEqual(path.read_text(), original)

    def test_valid_state_and_missing_file_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            with patch.object(status, "status_path", return_value=path):
                self.assertEqual(status.load(), {"schema_version": 1, "pending": {}})
                self.assertFalse(path.exists())
                value = {"schema_version": 1, "pending": {"codex": {"exit_code": 10}}}
                original = json.dumps(value)
                path.write_text(original)
                self.assertEqual(status.load(), value)
                self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
