import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from goal_budget import validate_budget


class BudgetPurityTests(unittest.TestCase):
    def budget(self, **changes):
        value = dict(status="open", budget_jobs=None, budget_seconds=1,
                     spent_jobs=(1 << 63) - 2, spent_seconds=0, fuse_trips=0,
                     started_epoch=0, workdir=tempfile.gettempdir(),
                     legacy={"nested": [{"preserve": [None, True, "舊欄位"]}]})
        value.update(changes)
        return value

    def test_exact_combined_limit_accepts_without_mutation(self):
        value = self.budget(reserved_jobs=1)
        before = copy.deepcopy(value)
        result = validate_budget(value)
        self.assertEqual(result, before)
        self.assertEqual(value, before)
        self.assertNotIn("closed_epoch", value)

    def test_one_above_combined_limit_rejects_without_mutation(self):
        value = self.budget(reserved_jobs=2)
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(ValueError, "combined job counters exceed the numeric range"):
            validate_budget(value)
        self.assertEqual(value, before)
        self.assertNotIn("closed_epoch", value)

    def test_missing_optional_fields_stay_absent_after_success_and_failure(self):
        for workdir in (tempfile.gettempdir(), "relative"):
            value = self.budget(workdir=workdir)
            before = copy.deepcopy(value)
            with self.subTest(workdir=workdir):
                if workdir == "relative":
                    with self.assertRaisesRegex(ValueError, "workdir"):
                        validate_budget(value)
                else:
                    self.assertEqual(validate_budget(value), before)
                self.assertEqual(value, before)
                for optional in ("reserved_jobs", "closed_epoch"):
                    self.assertNotIn(optional, value)


if __name__ == "__main__":
    unittest.main()
