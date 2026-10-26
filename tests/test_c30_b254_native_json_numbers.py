import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class NativeJsonNumberTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "numbers.json"

    def test_nonstandard_constants_at_root_and_nested(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            for template in ("%s", '{"x": %s}', '[%s]', '{"x": [0, {"y": %s}]}'):
                with self.subTest(constant=constant, template=template):
                    self.path.write_text(template % constant, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "^invalid JSON number$"):
                        native.read_json(self.path)

    def test_same_named_strings_and_standard_numbers_remain_valid(self):
        for value in ("NaN", "Infinity", "-Infinity", 0, -7, 1.25, 1e20,
                      {"x": "NaN", "Infinity": "-Infinity"},
                      ["NaN", "Infinity", "-Infinity", 0, -7, 1.25],
                      {"x": [0, {"y": -1.25}]}):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual(native.read_json(self.path), value)


if __name__ == "__main__":
    unittest.main()
