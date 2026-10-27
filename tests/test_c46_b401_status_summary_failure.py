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


class SummaryFailureTests(unittest.TestCase):
    def test_summary_failure_preserves_failure_codes_and_hides_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status.json"
            for error in (OSError, ValueError, TypeError, KeyError):
                for action_code in (0, 2, 10, 20, 30, 40, 50, 130):
                    with self.subTest(error=error.__name__, action_code=action_code):
                        calls = []
                        output, errors = io.StringIO(), io.StringIO()
                        real_save = aa_policy.atomic_json

                        def save(target, state):
                            calls.append("save")
                            return real_save(target, state)

                        def action():
                            calls.append("action")
                            return action_code

                        def summary():
                            calls.append("summary")
                            raise error("PRIVATE_SUMMARY_MARKER")

                        with patch.object(status, "status_path", return_value=path), \
                                patch.object(aa_policy, "atomic_json", side_effect=save), \
                                patch.object(status, "record") as record, \
                                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                            code = status.run_daily(action, summary=summary)
                            state = status.load()
                        self.assertEqual(code, action_code or 40)
                        self.assertEqual(calls, ["save", "action", "summary"])
                        record.assert_not_called()
                        self.assertIn("running", state)
                        self.assertNotIn("checked_at", state)
                        self.assertEqual(output.getvalue(), "")
                        self.assertEqual(errors.getvalue(), "omnilane: automatic verification status "
                                         "could not be saved; check OMNILANE_HOME permissions. "
                                         "Verification is not reported as successful.\n")
                        self.assertNotIn("PRIVATE_SUMMARY_MARKER",
                                         output.getvalue() + errors.getvalue() + path.read_text())


if __name__ == "__main__":
    unittest.main()
