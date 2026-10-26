import offline_env  # Activate suite isolation for direct file execution.

import json
import unittest
from unittest import mock

from test_native_context_host_rows import native_context


class HostNameBoundsTests(unittest.TestCase):
    def load_name(self, field, name):
        names = {"harness": "codex", "model": "base", "effort": "high"}
        names[field] = name
        value = {"schema_version": 1, "harnesses": {
            names["harness"]: [{"model": names["model"], "efforts": [names["effort"]]}]}}
        path = mock.Mock()
        path.read_text.return_value = json.dumps(value)
        result = native_context.load_host_rows(path)
        path.read_text.assert_called_once_with()
        return result, names

    def test_128_accepted_and_129_rejected_for_all_fields(self):
        for field in ("harness", "model", "effort"):
            with self.subTest(field=field, length=128):
                result, names = self.load_name(field, "a" * 128)
                self.assertEqual(result, {names["harness"]: [
                    (names["model"], [names["effort"]])]})
            with self.subTest(field=field, length=129):
                with self.assertRaisesRegex(native_context.HostRowsError, "invalid " + field):
                    self.load_name(field, "a" * 129)

    def test_illegal_initial_and_trailing_controls_rejected(self):
        for field in ("harness", "model", "effort"):
            for name in ("-name", "_name", ".name", " name", "éname",
                         "name\n", "name\r", "name\t", "name\x00", "name\x1f", "name\x7f"):
                with self.subTest(field=field, name=repr(name)):
                    with self.assertRaisesRegex(native_context.HostRowsError, "invalid " + field):
                        self.load_name(field, name)

    def test_legal_alphabet_and_single_character_names_preserved(self):
        for field in ("harness", "model", "effort"):
            for name in ("a", "Z", "0", "9Az._-", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"):
                with self.subTest(field=field, name=name):
                    result, names = self.load_name(field, name)
                    self.assertEqual(result, {names["harness"]: [
                        (names["model"], [names["effort"]])]})


if __name__ == "__main__":
    unittest.main()
