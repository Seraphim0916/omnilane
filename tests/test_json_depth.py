import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import json_depth


class JSONDepthTests(unittest.TestCase):
    def nested(self, depth):
        return "[" * depth + "0" + "]" * depth

    def test_limit_is_inclusive(self):
        self.assertEqual(json_depth.loads(self.nested(json_depth.MAX_DEPTH)), json.loads(self.nested(json_depth.MAX_DEPTH)))
        with self.assertRaisesRegex(json_depth.JSONDepthError, "deeper than 256 levels"):
            json_depth.loads(self.nested(json_depth.MAX_DEPTH + 1))

    def test_same_refusal_for_far_deeper_input(self):
        # 3.9-3.13 raise RecursionError here and 3.14 decodes it; the check makes all of them refuse alike.
        for depth in (1000, 10000, 100000):
            with self.subTest(depth=depth):
                with self.assertRaises(json_depth.JSONDepthError):
                    json_depth.loads(self.nested(depth))

    def test_is_a_value_error(self):
        self.assertTrue(issubclass(json_depth.JSONDepthError, ValueError))

    def test_brackets_inside_strings_do_not_count(self):
        text = json.dumps({"text": "[" * 1000 + '\\"{' * 500})
        self.assertEqual(json_depth.loads(text), json.loads(text))

    def test_objects_count_like_arrays(self):
        with self.assertRaises(json_depth.JSONDepthError):
            json_depth.check('{"a":' * 300 + "{}" + "}" * 300)

    def test_bytes_follow_json_encoding_detection(self):
        for encoding in ("utf-8", "utf-16", "utf-32"):
            with self.subTest(encoding=encoding):
                with self.assertRaises(json_depth.JSONDepthError):
                    json_depth.check(self.nested(300).encode(encoding))
                self.assertEqual(json_depth.loads('{"k": [1]}'.encode(encoding)), {"k": [1]})

    def test_invalid_utf8_is_left_to_the_decoder(self):
        with self.assertRaises(UnicodeDecodeError):
            json_depth.loads(b'{"host":"\xff"}')

    def test_keyword_arguments_reach_json_loads(self):
        with self.assertRaises(ValueError):
            json_depth.loads('{"a":1,"a":2}', object_pairs_hook=lambda pairs: (_ for _ in ()).throw(ValueError("dup"))
                             if len(dict(pairs)) != len(pairs) else dict(pairs))


if __name__ == "__main__":
    unittest.main()
