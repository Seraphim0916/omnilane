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
    def ago(self, seconds):
        return (FrozenDatetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        for timeout, elapsed, expected in (
                (native.DEFAULT_TIMEOUT + 60, native.DEFAULT_TIMEOUT + native.DEADLINE_GRACE + 1, "pending"),
                (1, native.DEADLINE_GRACE + 2, "expired")):
            with self.subTest(timeout=timeout):
                metadata_timeout = 1 if expected == "pending" else native.DEFAULT_TIMEOUT + 60
                self.observe({"state": "pending", "timeout": timeout},
                             {"started": self.ago(elapsed), "timeout": metadata_timeout}, 1, expected)

    def test_missing_timeout_preserves_state_created(self):
        timeout = native.DEFAULT_TIMEOUT
        for elapsed, metadata_elapsed, expected in (
                (timeout + native.DEADLINE_GRACE - 1, timeout + native.DEADLINE_GRACE + 1, "pending"),
                (timeout + native.DEADLINE_GRACE + 1, 0, "expired")):
            with self.subTest(expected=expected):
                self.observe({"state": "pending", "created": self.ago(elapsed)},
                             {"started": self.ago(metadata_elapsed), "timeout": timeout}, 1, expected)

    def test_metadata_timeout_beats_default_in_both_directions(self):
        for timeout, elapsed, expected in (
                (1, native.DEADLINE_GRACE + 2, "expired"),
                (native.DEFAULT_TIMEOUT + 60, native.DEFAULT_TIMEOUT + native.DEADLINE_GRACE + 1, "pending")):
            with self.subTest(timeout=timeout):
                self.observe({"state": "pending", "created": self.ago(elapsed)},
                             {"started": self.ago(elapsed), "timeout": timeout}, 1, expected)

    def test_complete_state_never_reads_conflicting_metadata(self):
        for timeout, elapsed, expected in (
                (native.DEFAULT_TIMEOUT + 60, native.DEFAULT_TIMEOUT + native.DEADLINE_GRACE + 1, "pending"),
                (1, native.DEADLINE_GRACE + 2, "expired")):
            with self.subTest(timeout=timeout):
                self.observe({"state": "pending", "created": self.ago(elapsed), "timeout": timeout},
                             {"started": self.ago(0), "timeout": native.DEFAULT_TIMEOUT + 60}, 0, expected)

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
