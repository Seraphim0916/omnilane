import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from refusal_evidence import refusal_from


class RefusalJsonlTests(unittest.TestCase):
    def test_unicode_separators_are_json_string_content(self):
        for separator in ("\u0085", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                value = {"allowed": False, "message": "before" + separator + "after"}
                self.assertEqual(refusal_from(json.dumps(value, ensure_ascii=False)), value)

    def test_last_refusal_wins_amid_noise_and_crlf(self):
        first = {"allowed": False, "message": "first"}
        last = {"allowed": False, "message": "last\u2028line"}
        text = "\r\n".join(["noise", json.dumps(first), "{bad", "[]",
                               json.dumps(last, ensure_ascii=False), '{"allowed": true}'])
        self.assertEqual(refusal_from(text), last)
        self.assertIsNone(refusal_from('noise\n{bad\n{"allowed": true}'))


if __name__ == "__main__":
    unittest.main()
