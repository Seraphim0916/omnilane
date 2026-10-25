import offline_env  # Activate suite isolation for direct file execution.
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import host_context as hc


class TomlFallbackTests(unittest.TestCase):
    def test_strings_and_quoted_section(self):
        text = '''model = "name#variant" # comment
profile = 'a.b'
[profiles."a.b"]
model = 'example-model'
'''
        expected = {'model': 'name#variant', 'profile': 'a.b',
                    'profiles': {'a.b': {'model': 'example-model'}}}
        self.assertEqual(hc._toml_fallback(text), expected)
        with patch.dict(sys.modules, {'tomllib': None}):
            self.assertEqual(hc._toml_strings(text), expected)

    def test_escapes(self):
        self.assertEqual(hc._toml_fallback(r'model = "a\"b\\c\u0041"'),
                         {'model': 'a"b\\cA'})

    def test_unsupported_or_invalid_is_rejected(self):
        for text in ('model = 42', 'model = ["a"]', 'model = "unterminated',
                     'model = "a"\nmodel = "b"', '[profiles..bad]',
                     'model = "a"\n[model]\nx = "b"', r'model = "bad\q"'):
            with self.subTest(text=text), self.assertRaises(hc.HostContextError):
                hc._toml_fallback(text)


if __name__ == '__main__':
    unittest.main()
