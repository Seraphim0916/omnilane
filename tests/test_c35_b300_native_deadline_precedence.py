import offline_env  # Activate suite isolation for direct file execution.

import copy
import datetime
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "native_b300", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 26, 12, 10, tzinfo=datetime.timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


class NativeDeadlinePrecedenceTests(unittest.TestCase):
    def observe(self, state, metadata, reads, expected="pending"):
        before = copy.deepcopy(state)
        metadata_before = copy.deepcopy(metadata)
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            with patch.object(native.datetime, "datetime", FrozenDatetime), \
                    patch.object(native, "read_json", return_value=metadata) as read:
                result, reason = native.observed_job_state(job, state)
                self.assertEqual(result, expected)
                self.assertEqual(bool(reason), expected == "expired")
                if reads:
                    read.assert_called_once_with(job / "meta.json")
                else:
                    read.assert_not_called()
            self.assertEqual(list(job.iterdir()), [])
        self.assertEqual(state, before)
        self.assertEqual(metadata, metadata_before)

    def test_missing_created_preserves_state_timeout(self):
        self.observe({"state": "pending", "timeout": 600},
                     {"started": "2026-10-26T12:04:00Z", "timeout": 1}, 1)

    def test_missing_timeout_preserves_state_created(self):
        self.observe({"state": "pending", "created": "2026-10-26T12:09:00Z"},
                     {"started": "2026-10-26T11:00:00Z", "timeout": 60}, 1)

    def test_complete_state_never_reads_conflicting_metadata(self):
        self.observe({"state": "pending", "created": "2026-10-26T12:09:00Z", "timeout": 60},
                     {"started": "2026-10-26T11:00:00Z", "timeout": 1}, 0)

    def test_default_timeout_used_only_when_both_sources_omit_it(self):
        now = FrozenDatetime.now(datetime.timezone.utc)
        for seconds_past in (-1, 1):
            with self.subTest(seconds_past=seconds_past):
                created = now - datetime.timedelta(
                    seconds=native.DEFAULT_TIMEOUT + native.DEADLINE_GRACE + seconds_past)
                self.observe({"state": "pending"},
                             {"started": created.strftime("%Y-%m-%dT%H:%M:%SZ")}, 1,
                             "pending" if seconds_past < 0 else "expired")


if __name__ == "__main__":
    unittest.main()
