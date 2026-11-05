import offline_env  # Activate suite isolation for direct file execution.

import copy
import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class NativeDeadlineTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "native_b237", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)
        temporary = tempfile.TemporaryDirectory(prefix="native-deadline-")
        self.addCleanup(temporary.cleanup)
        self.job = Path(temporary.name)
        self.state = dict(state="pending", created="2026-10-26T12:00:00Z", timeout=60)
        self.deadline = datetime.datetime(2026, 10, 26, 12, 6, tzinfo=datetime.timezone.utc)

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.job.iterdir()}

    def observe_at(self, now):
        class FrozenDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

        with mock.patch.object(self.native.datetime, "datetime", FrozenDatetime):
            return self.native.observed_job_state(self.job, self.state)

    def test_exact_deadline_with_grace_and_read_only_observation(self):
        for use_metadata in (False, True):
            with self.subTest(use_metadata=use_metadata):
                self.state = dict(state="pending", created="2026-10-26T12:00:00Z", timeout=60)
                if use_metadata:
                    (self.job / "meta.json").write_text(json.dumps(
                        dict(started=self.state.pop("created"), timeout=self.state.pop("timeout"))))
                (self.job / "native.json").write_text(json.dumps(self.state))
                before = copy.deepcopy(self.state)
                files_before = self.snapshot()
                for offset, expected in ((-1, "pending"), (0, "pending"), (1, "expired")):
                    with self.subTest(microseconds=offset):
                        observed, reason = self.observe_at(
                            self.deadline + datetime.timedelta(microseconds=offset))
                        self.assertEqual(observed, expected)
                        if expected == "expired":
                            self.assertEqual(reason,
                                "native handoff deadline 2026-10-26T12:06:00Z exceeded "
                                "(timeout 60s + grace 300s); no completion recorded")
                        else:
                            self.assertEqual(reason, "")
                        self.assertEqual(self.state, before)
                        self.assertEqual(self.snapshot(), files_before)

    def test_overflow_is_a_controlled_rejection_without_mutation(self):
        for created, timeout in (("9999-12-31T23:59:59Z", 1),
                                 ("2026-10-26T12:00:00Z", 10 ** 100)):
            with self.subTest(created=created, timeout=timeout):
                self.state.update(created=created, timeout=timeout)
                (self.job / "native.json").write_text(json.dumps(self.state))
                before = copy.deepcopy(self.state)
                files_before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "^invalid native deadline$"):
                    self.observe_at(self.deadline)
                self.assertEqual(self.state, before)
                self.assertEqual(self.snapshot(), files_before)

    def test_terminal_states_do_not_read_metadata_or_publish_state(self):
        for terminal, expected in (("cancelled", "cancelled"), ("done", "done")):
            with self.subTest(terminal=terminal):
                self.state = dict(state=terminal)
                (self.job / "meta.json").write_text("invalid JSON must not be read")
                (self.job / "native.json").write_text(json.dumps(self.state))
                before = copy.deepcopy(self.state)
                files_before = self.snapshot()
                with mock.patch.object(self.native, "read_json",
                                       side_effect=AssertionError("unexpected metadata read")) as read:
                    self.assertEqual(self.native.observed_job_state(self.job, self.state),
                                     (expected, ""))
                    read.assert_not_called()
                self.assertEqual(self.state, before)
                self.assertEqual(self.snapshot(), files_before)


if __name__ == "__main__":
    unittest.main()
