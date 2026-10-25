import offline_env  # Activate suite isolation for direct file execution.
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from refusal_evidence import summary


class RefusalSummaryTests(unittest.TestCase):
    def test_every_rendered_field_is_one_line_without_mutation(self):
        for separator in ("\r", "\n", "\r\n", "\u0085", "\u2028", "\u2029"):
            for field in ("failed_gate", "code", "reason", "message", "path"):
                with self.subTest(separator=repr(separator), field=field):
                    refusal = dict(failed_gate="gate", code="code", reason="reason", message="quote")
                    path = Path("evidence.json")
                    if field == "path":
                        path = Path("before" + separator + "after")
                    else:
                        refusal[field] = "before" + separator + "after"
                    original = copy.deepcopy(refusal)
                    result = summary(refusal, path)
                    self.assertEqual(len(result.splitlines()), 1, repr(result))
                    self.assertIn("before after", result)
                    self.assertEqual(refusal, original)

    def test_defaults_and_quote_limit_remain(self):
        result = summary({"message": '"' + "x" * 200}, None)
        self.assertIn("failed_gate=unknown code=unknown", result)
        self.assertNotIn("evidence=", result)
        self.assertIn("…", result)


if __name__ == "__main__":
    unittest.main()
