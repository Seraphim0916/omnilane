import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from refusal_evidence import refusal_from


class RefusalDepthTests(unittest.TestCase):
    def setUp(self):
        self.deep = '{"allowed":false,"data":' + '[' * 10000 + '0' + ']' * 10000 + '}'
        self.first = {"allowed": False, "code": "first"}
        self.last = {"allowed": False, "code": "last"}

    def test_only_deep_line_is_skipped(self):
        self.assertIsNone(refusal_from(self.deep))

    def test_refusal_before_deep_line_is_preserved(self):
        self.assertEqual(refusal_from(json.dumps(self.first) + '\n' + self.deep), self.first)

    def test_refusal_after_deep_line_is_parsed(self):
        self.assertEqual(refusal_from(self.deep + '\n' + json.dumps(self.last)), self.last)

    def test_last_valid_refusal_still_wins(self):
        lines = [json.dumps(self.first), self.deep, json.dumps(self.last)]
        self.assertEqual(refusal_from('\n'.join(lines)), self.last)

    def test_normal_nested_data_is_preserved(self):
        value = {"allowed": False, "data": {"nested": [1, {"reason": ["fixture"]}]}}
        self.assertEqual(refusal_from(json.dumps(value)), value)


if __name__ == "__main__":
    unittest.main()
