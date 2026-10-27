import offline_env  # Activate suite isolation for direct file execution.
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from goal_budget import validate_budget

FIELDS = ("budget_jobs", "budget_seconds", "spent_jobs", "spent_seconds",
          "fuse_trips", "started_epoch", "reserved_jobs", "closed_epoch")


class BudgetFloatTypesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.budget = dict.fromkeys(FIELDS, 1)
        self.budget.update(status="open", workdir=temporary.name)

    def test_integer_controls_remain_valid(self):
        self.assertEqual(validate_budget(self.budget), self.budget)

    def test_each_field_rejects_floats_even_when_integral(self):
        integral_rejected = 0
        for field in FIELDS:
            for value in (1.0, 1.5, float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    budget = dict(self.budget, **{field: value})
                    with self.assertRaisesRegex(ValueError, field):
                        validate_budget(budget)
                    if value == 1.0:
                        integral_rejected += 1
        self.assertEqual(integral_rejected, 8)
        print(f"B109 float rejected {integral_rejected} / 8")


if __name__ == "__main__":
    unittest.main()
