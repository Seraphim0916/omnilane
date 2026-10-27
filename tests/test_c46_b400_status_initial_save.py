import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import verification_status as status


class InitialSaveTests(unittest.TestCase):
    def test_save_failure_stops_action_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status.json"
            for error in (OSError, ValueError, TypeError, KeyError):
                with self.subTest(error=error.__name__):
                    calls, messages = [], []
                    output, errors = io.StringIO(), io.StringIO()

                    def save(*args):
                        calls.append("save")
                        raise error("PRIVATE_FAILURE_MARKER")

                    def log(message):
                        calls.append("log")
                        messages.append(message)

                    with patch.object(status, "status_path", return_value=path), \
                            patch.object(aa_policy, "atomic_json", side_effect=save), \
                            patch.object(status, "record") as record, \
                            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = status.run_daily(lambda: calls.append("action"),
                                                summary=lambda: calls.append("summary"), log=log)
                    self.assertEqual(code, 40)
                    self.assertEqual(calls, ["save", "log"])
                    record.assert_not_called()
                    self.assertEqual(messages, ["omnilane: automatic verification status could not "
                                                "be saved; check OMNILANE_HOME permissions."])
                    self.assertEqual(output.getvalue() + errors.getvalue(), "")
                    self.assertFalse(path.exists())

    def test_success_saves_start_before_action_summary_and_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status.json"
            calls = []
            real_save, real_record = aa_policy.atomic_json, status.record

            def save(target, state):
                calls.append("save")
                return real_save(target, state)

            def action():
                calls.append("action")
                self.assertIn("running", status.load())
                return 0

            def summary():
                calls.append("summary")
                return {}

            def record(*args):
                calls.append("record")
                return real_record(*args)

            with patch.object(status, "status_path", return_value=path), \
                    patch.object(aa_policy, "atomic_json", side_effect=save), \
                    patch.object(status, "record", side_effect=record):
                self.assertEqual(status.run_daily(action, vendors=["codex"], summary=summary), 0)
                self.assertNotIn("running", status.load())
            self.assertEqual(calls, ["save", "action", "summary", "record", "save"])


if __name__ == "__main__":
    unittest.main()
