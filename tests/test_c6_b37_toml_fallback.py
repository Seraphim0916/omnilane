import offline_env  # Activate suite isolation for direct file execution.
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import host_context as hc


class TomlFallbackTests(unittest.TestCase):
    def test_legal_config_corpus_matches_tomllib(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest('tomllib comparison requires Python 3.11+')

        corpus = [
            'developer_instructions = """\nhello\nmodel = "not-a-setting"\n"""\n',
            "developer_instructions = '''\nhello\n[profiles.fake]\n'''\n",
            'features.multi_agent = true\n',
            '# comment\nmodel = "base#tag" # trailing\n',
            'model = "base"\r\nfeatures.multi_agent = true\r\n',
            '[features.nested]\nenabled = true\n',
            '[[agents]]\nname = "one"\n[[agents]]\nname = "two"\n',
            'items = [\n"""multi\nline""", {a.b = true},\n]\n',
            'options = {nested = {enabled = true}, a.b = [1, 2]}\n',
            'profile = "p"\nprofiles.p.model = "selected"\n',
            'profile = "p.q"\n[profiles."p.q"]\nmodel = "selected"\n',
            'model = """\nbase"""\n',
            "model = '''\nbase'''\n",
            'model = """ba\\\n   se"""\n',
            'developer_instructions = """ends with quote""""\n',
            "developer_instructions = '''ends with two quotes'''''\n",
            'stamp = 1979-05-27 07:32:00Z\ndate = 1979-05-27\ntime = 07:32:00\n',
            '[[agents]]\n[agents.options]\nx = true\n[[agents]]\n[agents.options]\nx = false\n',
            'profile = "p"\n[profiles.p]\nmodel = "selected"\n'
            '[[profiles.p.tools]]\nname = "one"\n[[profiles.p.tools]]\nname = "two"\n',
            'profile = "p.q"\nprofiles."p.q".model = "selected"\n',
            'model = """base\\\\\nnext\\u0041"""\n',
            'model = """base\\   \r\n  continued"""\r\n',
            'model = """base\\"quoted\\""""\n',
            "model = '''base'''''\n",
            'profile = "p"\nprofiles = {p.model = "selected"}\n',
        ]

        def fields(config):
            result = {key: config[key] for key in ('model', 'profile') if key in config}
            if 'profiles' in config:
                result['profiles'] = {
                    name: {'model': section['model']} if 'model' in section else {}
                    for name, section in config['profiles'].items()}
            return result

        for text in corpus:
            with self.subTest(text=text):
                expected = tomllib.loads(text)
                self.assertEqual(fields(hc._toml_fallback(text)), fields(expected))

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
                     'x = {a = 1}\n[x]\nb = 2',
                     'x = """unterminated', "x = '''unterminated",
                     r'x = """bad\q"""', 'x = """bad\x00"""',
                     'features.enabled = true\nfeatures.enabled = false',
                     '[[agents]\nname = "bad"'):
            with self.subTest(text=text), self.assertRaises(hc.HostContextError):
                hc._toml_fallback(text)


if __name__ == '__main__':
    unittest.main()
