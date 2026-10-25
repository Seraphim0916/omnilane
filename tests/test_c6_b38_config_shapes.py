import offline_env  # Activate suite isolation for direct file execution.
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import host_context as hc


class ConfigShapeTests(unittest.TestCase):
    def test_install_cli_without_tomllib(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'config.toml').write_text(
                'model = "gpt-6-astra"\nmodel_context_window = 100000\n'
                '[features]\nmulti_agent = true\n')
            environment = dict(os.environ, CODEX_HOME=directory,
                               OMNILANE_HOME=str(base / 'output'))
            for name in ('CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CLAUDECODE',
                         'CLAUDE_CODE_ENTRYPOINT'):
                environment.pop(name, None)
            script = root / 'scripts/lib/native_context.py'
            result = subprocess.run(
                [sys.executable, '-B', '-c',
                 'import runpy, sys; sys.modules["tomllib"] = None; '
                 'sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; '
                 'runpy.run_path(sys.argv[0], run_name="__main__")',
                 str(script.parent), str(script), '--install', '--harness', 'codex'],
                env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            target = base / 'output/native-context/host/codex.json'
            self.assertEqual(json.loads(target.read_text())['current_model'], 'gpt-6-astra')
            with patch.dict(os.environ, {'OMNILANE_HOME': str(base / 'output')}):
                self.assertEqual(hc.verify('codex')['source_detail']['key'], 'model')

    def test_fallback_rejects_wrong_field_types(self):
        for text in ('model = 42', 'model = ["a"]', 'profile = false',
                     'profiles = []', '[profiles]\np = 1',
                     '[profiles.p]\nmodel = false'):
            with self.subTest(text=text), patch.dict(sys.modules, {'tomllib': None}), \
                 patch.object(Path, 'read_text', return_value=text):
                with self.assertRaises(hc.HostContextError):
                    hc.configured_model('codex', {})

    def test_install_cli_rejects_real_fixture_files(self):
        root = Path(__file__).resolve().parents[1]
        for harness, filename, content in (
                ('codex', 'config.toml', 'profiles = []'),
                ('claude-code', 'settings.json', '{"env": ["bad"]}')):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / filename).write_text(content)
                environment = dict(os.environ, CODEX_HOME=directory, CLAUDE_CONFIG_DIR=directory,
                                   OMNILANE_HOME=str(base / 'output'))
                for name in ('CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT'):
                    environment.pop(name, None)
                result = subprocess.run(
                    ['bash', str(root / 'bin/omnilane'), 'native-context', '--install',
                     '--harness', harness], env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn('not written', result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse((base / 'output/native-context/host' / (harness + '.json')).exists())

    def test_invalid_shapes_are_controlled(self):
        cases = {
            'codex': [[], {'profiles': []}, {'profiles': {'p': []}},
                      {'profile': []}, {'model': 1}, {'model': None},
                      {'profiles': {'p': {'model': False}}}],
            'claude-code': [[], {'env': []}, {'env': None}, {'model': {}},
                            {'env': {'ANTHROPIC_MODEL': []}}],
        }
        for harness, values in cases.items():
            for value in values:
                with self.subTest(harness=harness, value=value):
                    with patch.object(Path, 'read_text', return_value=json.dumps(value)), \
                         patch.object(hc, '_toml_strings', return_value=value):
                        with self.assertRaises(hc.HostContextError):
                            hc.configured_model(harness, {})

    def test_install_rejects_without_writes(self):
        for harness in ('codex', 'claude-code'):
            with self.subTest(harness=harness), \
                 patch.object(hc.caller_identity, 'load_registry', return_value=({}, None)), \
                 patch.object(hc, 'load_aliases', return_value={}), \
                 patch.object(Path, 'read_text', return_value='[]'), \
                 patch.object(hc, '_toml_strings', return_value=[]), \
                 patch.object(hc, '_write_private') as write:
                messages = []
                self.assertEqual(hc.install([harness], 'unused', {}, messages.append), (3, []))
                write.assert_not_called()
                self.assertIn('not written', messages[0])

    def test_valid_precedence(self):
        with patch.object(Path, 'read_text', return_value='{}'), \
             patch.object(hc, '_toml_strings', return_value={
                 'model': 'base', 'profile': 'a.b', 'profiles': {'a.b': {'model': 'chosen'}}}):
            self.assertEqual(hc.configured_model('codex', {})[0], 'chosen')
        with patch.object(Path, 'read_text', return_value=json.dumps({
                'model': 'base', 'env': {'ANTHROPIC_MODEL': 'chosen'}})):
            self.assertEqual(hc.configured_model('claude-code', {})[0], 'chosen')


if __name__ == '__main__':
    unittest.main()
