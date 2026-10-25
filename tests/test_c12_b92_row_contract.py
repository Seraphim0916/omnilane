import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import transport_compat as compat


class RowContractTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(id="fixture-row", vendor="codex", effort="high",
                        transport_mapping=dict(model="fixture-model", options={"mode": "work"}))

    def contract(self, row):
        before = copy.deepcopy(row)
        result = compat.row_contract(row)
        self.assertEqual(row, before, "row_contract must not mutate its input")
        return result

    def test_each_score_field_is_ignored(self):
        baseline = self.contract(self.row)
        self.assertTrue(compat.SCORE_FIELDS)
        for field in sorted(compat.SCORE_FIELDS):
            for value in (None, 0, {"nested": ["changed"]}):
                with self.subTest(field=field, value=value):
                    row = copy.deepcopy(self.row)
                    row[field] = value
                    self.assertEqual(self.contract(row), baseline)

    def test_each_transport_annotation_is_ignored(self):
        baseline = self.contract(self.row)
        for field in ("status", "runtime_verified", "reason"):
            for value in (None, False, "changed", {"nested": [1]}):
                with self.subTest(field=field, value=value):
                    row = copy.deepcopy(self.row)
                    row["transport_mapping"][field] = value
                    self.assertEqual(self.contract(row), baseline)

    def test_unknown_fields_remain_significant_at_each_depth(self):
        baseline = self.contract(self.row)
        for path in ((), ("transport_mapping",), ("transport_mapping", "options")):
            for name in ("future_field", "score", "reason"):
                # Only the explicitly exempt fields at their own level are ignored.
                if not path and name in compat.SCORE_FIELDS:
                    continue
                if path == ("transport_mapping",) and name == "reason":
                    continue
                with self.subTest(path=path, name=name):
                    row = copy.deepcopy(self.row)
                    target = row
                    for key in path:
                        target = target[key]
                    target[name] = {"value": 1}
                    added = self.contract(row)
                    self.assertNotEqual(added, baseline)
                    # Snapshot the contract: nested values may legitimately alias input.
                    added = copy.deepcopy(added)
                    target[name] = {"value": 2}
                    self.assertNotEqual(self.contract(row), added)


if __name__ == "__main__":
    unittest.main()
