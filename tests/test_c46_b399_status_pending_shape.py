import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import verification_status as status


class PendingShapeTests(unittest.TestCase):
    def test_invalid_entries_rejected_before_any_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status.json"
            for entry in ([], [1], None, True, False, 0, 1.5, "text"):
                with self.subTest(entry=entry):
                    original = json.dumps({"schema_version": 1, "pending": {
                        "claude": {"exit_code": 10}, "codex": entry}})
                    path.write_text(original)
                    action, summary, log = Mock(return_value=0), Mock(), Mock()
                    with patch.object(status, "status_path", return_value=path), \
                            patch.object(aa_policy, "atomic_json") as save, \
                            patch.object(status, "record") as record:
                        with self.assertRaisesRegex(aa_policy.PolicyError,
                                                    "^invalid local verification status$"):
                            status.load()
                        self.assertEqual(status.run_daily(action, summary=summary, log=log), 40)
                    save.assert_not_called()
                    action.assert_not_called()
                    summary.assert_not_called()
                    record.assert_not_called()
                    log.assert_called_once()
                    self.assertEqual(path.read_text(), original)

    def test_valid_entries_preserve_load_and_daily_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status.json"
            state = {"schema_version": 1, "pending": {
                "codex": {}, "claude": {"exit_code": 10}}}
            path.write_text(json.dumps(state))
            with patch.object(status, "status_path", return_value=path):
                self.assertEqual(status.load(), state)

                def action():
                    self.assertIn("running", status.load())
                    return 0

                self.assertEqual(status.run_daily(action, vendors=["codex"]), 0)
                result = status.load()
            self.assertEqual(result["pending"], {"claude": {"exit_code": 10}})
            self.assertNotIn("running", result)


if __name__ == "__main__":
    unittest.main()
