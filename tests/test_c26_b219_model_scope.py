import offline_env  # Activate suite isolation for direct file execution.

import json
import unittest
from unittest import mock

from test_native_context_host_rows import native_context


class ModelScopeTests(unittest.TestCase):
    def load_rows(self, harnesses):
        path = mock.Mock()
        path.read_text.return_value = json.dumps({"schema_version": 1, "harnesses": harnesses})
        return native_context.load_host_rows(path)

    def test_same_harness_duplicate_model_rejected_in_either_order(self):
        rows = [{"model": "same", "efforts": ["low"]},
                {"model": "same", "efforts": ["high"]}]
        for harness in ("codex", "claude-code"):
            for order in (rows, list(reversed(rows))):
                with self.subTest(harness=harness, order=order):
                    with self.assertRaisesRegex(native_context.HostRowsError,
                                                "^harness " + harness + ": model same listed twice$"):
                        self.load_rows({harness: order})

    def test_different_harnesses_accept_same_model_in_either_order(self):
        for second_effort in ("high", "low"):
            declarations = [("codex", [{"model": "same", "efforts": ["low"]}]),
                            ("claude-code", [{"model": "same", "efforts": [second_effort]}])]
            expected = {"codex": [("same", ["low"])],
                        "claude-code": [("same", [second_effort])]}
            for order in (declarations, list(reversed(declarations))):
                with self.subTest(second_effort=second_effort, order=order):
                    self.assertEqual(self.load_rows(dict(order)), expected)


if __name__ == "__main__":
    unittest.main()
