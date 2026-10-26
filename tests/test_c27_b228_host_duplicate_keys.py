import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native_context


class HostDuplicateKeysTests(unittest.TestCase):
    def load_text(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host-rows.json"
            path.write_text(text)
            return native_context.load_host_rows(path)

    def test_nested_duplicate_efforts_are_rejected(self):
        for duplicate in ('"efforts"', r'"eff\u006frts"'):
            with self.subTest(duplicate=duplicate):
                text = ('{"schema_version":1,"harnesses":{"codex":['
                        '{"model":"model-a","efforts":["low"],'
                        + duplicate + ':["high"]}]}}')
                with self.assertRaisesRegex(native_context.HostRowsError,
                                            "^duplicate key in a JSON object$"):
                    self.load_text(text)

    def test_escaped_harness_name_duplicate_is_rejected(self):
        text = (r'{"schema_version":1,"harnesses":{"codex":[],'
                r'"cod\u0065x":[]}}')
        with self.assertRaisesRegex(native_context.HostRowsError,
                                    "^duplicate key in a JSON object$"):
            self.load_text(text)

    def test_same_keys_in_distinct_objects_are_legal(self):
        text = (r'{"schema_version":1,"harnesses":{"codex":['
                r'{"model":"model-a","efforts":["low"]},'
                r'{"model":"model-b","eff\u006frts":["high"]}],'
                r'"claude-code":[{"model":"model-a","efforts":["low"]}]}}')
        self.assertEqual(self.load_text(text), {
            "codex": [("model-a", ["low"]), ("model-b", ["high"])],
            "claude-code": [("model-a", ["low"])],
        })


if __name__ == "__main__":
    unittest.main()
