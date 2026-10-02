"""The value rule's candidate pool holds every vendor the default chains route to."""
import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("aa_rebaseline", REPO / "scripts" / "aa_rebaseline.py")
aa_rebaseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aa_rebaseline)


class ValuePoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows = json.loads((REPO / "config" / "aa-model-policy.json").read_text())["scored_configs"]
        cls.pool = aa_rebaseline.value_pool(rows)

    def test_every_routed_vendor_has_a_row_in_the_pool(self):
        vendors = {row["vendor"] for row in self.pool}
        self.assertEqual(vendors, {"codex", "claude", "grok", "gemini"})

    def test_pool_holds_no_max_and_no_non_reasoning_row(self):
        self.assertTrue(self.pool)
        for row in self.pool:
            self.assertNotEqual(row["effort"], "max", row["id"])
            self.assertNotEqual(row["reasoning"], "non-reasoning", row["id"])

    def test_every_family_named_by_the_rule_is_scored(self):
        models = {row["model"] for row in self.pool}
        self.assertEqual(sorted(aa_rebaseline.VALUE_FAMILIES - models), [])


if __name__ == "__main__":
    unittest.main()
