import offline_env  # Activate suite isolation for direct file execution.

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import build_overlay
import verification_status as status


class AttemptResetTests(unittest.TestCase):
    def check_record(self, prior_code, prior_reason, code, summary, reason, attempts):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        old = "2026-01-01T00:00:00+00:00"
        state = {"schema_version": 1, "pending": {"codex": dict(
            reason=prior_reason, exit_code=prior_code, attempts=7,
            first_seen=old, last_seen=old)}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            with patch.object(status, "load", return_value=state), \
                    patch.object(status, "status_path", return_value=path), \
                    patch.object(status, "datetime") as clock, \
                    patch.object(build_overlay, "ROWS", {}), \
                    patch.object(aa_policy, "atomic_json") as save:
                clock.now.return_value = now
                status.record(code, vendors=["codex"], summary=summary)
                clock.now.assert_called_once_with(timezone.utc)
                save.assert_called_once_with(path, state)
        entry = state["pending"]["codex"]
        self.assertEqual(entry["reason"], reason)
        self.assertEqual(entry["exit_code"], code)
        self.assertEqual(entry["attempts"], attempts)
        self.assertEqual(entry["first_seen"], old if attempts == 8 else now.isoformat())
        self.assertEqual(entry["last_seen"], now.isoformat())
        self.assertEqual(state["checked_at"], now.isoformat())

    def test_same_code_and_reason_accumulate(self):
        self.check_record(137, "verification-interrupted", 137, {},
                          "verification-interrupted", 8)

    def test_same_code_changed_reason_resets(self):
        self.check_record(137, "verification-interrupted", 137,
                          {"sweeps": {"codex": {"outcome": "unprobeable"}}},
                          "needs-login-session", 1)

    def test_same_reason_changed_code_resets(self):
        self.check_record(137, "verification-interrupted", 138, {},
                          "verification-interrupted", 1)


if __name__ == "__main__":
    unittest.main()
