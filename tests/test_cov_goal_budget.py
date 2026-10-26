import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import goal_budget as gb


def budget(**changes):
    value = {"status": "open", "budget_jobs": 3, "budget_seconds": None, "spent_jobs": 0,
             "spent_seconds": 0, "fuse_trips": 0, "started_epoch": 1, "workdir": "/srv/goal"}
    value.update(changes)
    return value


class ValidateBudgetTests(unittest.TestCase):
    def reject(self, value, message):
        with self.assertRaises(ValueError) as caught:
            gb.validate_budget(value)
        self.assertEqual(str(caught.exception), message)

    def test_valid_budget_is_returned_unchanged_with_legacy_extras(self):
        value = budget(legacy_note="kept", reserved_jobs=2, closed_epoch=0)
        self.assertIs(gb.validate_budget(value), value)
        self.assertEqual(value["legacy_note"], "kept")

    def test_non_object_and_missing_fields_are_refused(self):
        for value in (None, [], "budget", {"status": "open"}):
            self.reject(value, "invalid goal budget: required fields are missing")
        partial = budget()
        del partial["fuse_trips"]
        self.reject(partial, "invalid goal budget: required fields are missing")

    def test_status_must_be_open_or_closed_string(self):
        for status in ("paused", "", None, 1, ["open"]):
            self.reject(budget(status=status), "invalid goal budget: status")
        self.assertEqual(gb.validate_budget(budget(status="closed"))["status"], "closed")

    def test_limits_accept_null_and_bounds_and_refuse_everything_else(self):
        for key in ("budget_jobs", "budget_seconds"):
            for good in (None, 1, gb.MAX_LIMIT):
                gb.validate_budget(budget(**{key: good}))
            for bad in (0, -1, gb.MAX_LIMIT + 1, True, False, 1.0, "5"):
                self.reject(budget(**{key: bad}),
                            "invalid goal budget: " + key + " must be null or an integer in 1..999999999")

    def test_counters_accept_bounds_and_refuse_bools_floats_and_overflow(self):
        for key in ("spent_jobs", "spent_seconds", "fuse_trips", "started_epoch",
                    "reserved_jobs", "closed_epoch"):
            gb.validate_budget(budget(**{key: 0}))
            if key not in ("spent_jobs", "reserved_jobs"):
                gb.validate_budget(budget(**{key: gb.MAX_COUNTER}))
            for bad in (-1, gb.MAX_COUNTER + 1, True, 0.0, None, "0"):
                self.reject(budget(**{key: bad}),
                            "invalid goal budget: " + key + " must be a bounded nonnegative integer")

    def test_combined_job_counters_cannot_exceed_the_numeric_range(self):
        gb.validate_budget(budget(spent_jobs=gb.MAX_COUNTER - 1, reserved_jobs=1))
        gb.validate_budget(budget(spent_jobs=gb.MAX_COUNTER))
        self.reject(budget(spent_jobs=gb.MAX_COUNTER, reserved_jobs=1),
                    "invalid goal budget: combined job counters exceed the numeric range")

    def test_workdir_is_checked_after_numbers(self):
        message = "invalid goal budget: workdir must be an absolute path without field separators"
        for workdir in ("", "relative/path", None, 7, b"/srv", "/srv/a\tb", "/srv/a\nb",
                        "/srv/a\rb", "/srv/a\x00b"):
            self.reject(budget(workdir=workdir), message)
        # A numeric error wins over a bad workdir: the order is part of the contract.
        self.reject(budget(spent_jobs=-1, workdir="relative"),
                    "invalid goal budget: spent_jobs must be a bounded nonnegative integer")
        gb.validate_workdir("/srv/goal with spaces")


if __name__ == "__main__":
    unittest.main()
