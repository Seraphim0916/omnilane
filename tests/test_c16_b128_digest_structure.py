import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from transport_compat import digest


class DigestStructureTests(unittest.TestCase):
    def test_nested_key_permutations_preserve_digest_and_inputs(self):
        original = {"中文": {"甲": 1, "乙": {"左": True, "右": None}},
                    "陣列": [{"先": "值", "後": 2}, "終"]}
        reordered = {"陣列": [{"後": 2, "先": "值"}, "終"],
                     "中文": {"乙": {"右": None, "左": True}, "甲": 1}}
        snapshots = copy.deepcopy((original, reordered))
        # Dict equality alone cannot detect an in-place key-order change.
        representations = (repr(original), repr(reordered))
        self.assertEqual(digest(original), digest(reordered))
        self.assertEqual((original, reordered), snapshots)
        self.assertEqual((repr(original), repr(reordered)), representations)

    def test_array_order_values_and_types_affect_digest_without_mutation(self):
        original = {"中文": {"數值": 1}, "陣列": ["甲", "乙"]}
        variants = (
            {"中文": {"數值": 1}, "陣列": ["乙", "甲"]},
            {"中文": {"數值": 2}, "陣列": ["甲", "乙"]},
            {"中文": {"數值": "1"}, "陣列": ["甲", "乙"]},
            {"中文": {"數值": True}, "陣列": ["甲", "乙"]},
            {"中文": {"數值": 1.0}, "陣列": ["甲", "乙"]},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                snapshots = copy.deepcopy((original, variant))
                representations = (repr(original), repr(variant))
                self.assertNotEqual(digest(original), digest(variant))
                self.assertEqual((original, variant), snapshots)
                self.assertEqual((repr(original), repr(variant)), representations)


if __name__ == "__main__":
    unittest.main()
