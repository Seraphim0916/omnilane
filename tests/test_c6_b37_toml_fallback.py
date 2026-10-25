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

    def test_legal_non_string_settings(self):
        text = '''model = "base"
model_context_window = 100000
ratio = 1.25e-2
limits = [1, true, ["x,#", 2], {enabled = false}]
options = {count = 2, ratio = -0.5, names = ["a", "b"]}
[features]
multi_agent = true
'''
        with patch.dict(sys.modules, {'tomllib': None}), \
             patch.object(Path, 'read_text', return_value=text):
            self.assertEqual(hc.configured_model('codex', {})[0], 'base')
            parsed = hc._toml_strings(text)
        self.assertEqual(parsed['model_context_window'], 100000)
        self.assertEqual(parsed['ratio'], 0.0125)
        self.assertEqual(parsed['features'], {'multi_agent': True})
        self.assertEqual(parsed['limits'], [1, True, ['x,#', 2], {'enabled': False}])
        self.assertEqual(parsed['options'], {'count': 2, 'ratio': -0.5, 'names': ['a', 'b']})

    def test_multiline_array(self):
        self.assertEqual(hc._toml_fallback('items = [\n1, # comment\ntrue,\n]\n'),
                         {'items': [1, True]})

    def test_numeric_forms_and_empty_containers(self):
        for literal, expected in (('1_000', 1000), ('-42', -42), ('0xff', 255),
                                  ('0o17', 15), ('0b10', 2), ('+2e3', 2000.0),
                                  ('-0.5', -0.5), ('[]', []), ('{}', {})):
            with self.subTest(literal=literal):
                self.assertEqual(hc._toml_fallback('x = ' + literal)['x'], expected)

    def test_unsupported_or_invalid_is_rejected(self):
        for text in ('model = "unterminated',
                     'model = "a"\nmodel = "b"', '[profiles..bad]',
                     'model = "a"\n[model]\nx = "b"', r'model = "bad\q"',
                     'x = truth', 'x = 01', 'x = 1.2.3', 'x = [1,,2]',
                     'x = [1 2]', 'x = [1', 'x = {a = 1,}',
                     'x = {a = 1, a = 2}', 'x = true junk',
                     'x = {a = 1}\n[x]\nb = 2'):
            with self.subTest(text=text), self.assertRaises(hc.HostContextError):
                hc._toml_fallback(text)


if __name__ == '__main__':
    unittest.main()
