import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from refusal_evidence import refusal_from


class RefusalBooleanTests(unittest.TestCase):
    VALUES = (False, True, 0, None, [], {}, "", "false")

    def test_only_boolean_false_is_refusal(self):
        for value in self.VALUES:
            with self.subTest(value=repr(value)):
                record = {"allowed": value, "message": "candidate"}
                result = refusal_from(json.dumps(record))
                if value is False:
                    self.assertEqual(result, record)
                    self.assertIs(result["allowed"], False)
                else:
                    self.assertIsNone(result)

    def test_non_boolean_values_cannot_replace_valid_refusal(self):
        first = {"allowed": False, "message": "first refusal"}
        last = {"allowed": False, "message": "last refusal"}
        lines = [json.dumps(first)]
        for value in self.VALUES[1:]:
            with self.subTest(value=repr(value)):
                invalid = json.dumps({"allowed": value, "message": "not a refusal"})
                self.assertEqual(refusal_from(json.dumps(first) + "\n" + invalid), first)
                lines.append(invalid)
                self.assertEqual(refusal_from("\n".join(lines)), first)
        lines.append(json.dumps(last))
        self.assertEqual(refusal_from("\n".join(lines)), last)


if __name__ == "__main__":
    unittest.main()
