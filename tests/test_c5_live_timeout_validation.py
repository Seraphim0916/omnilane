import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('live', Path(__file__).resolve().parents[1] / 'scripts/runners/run-codex-live.py')
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)
BASE = ['live'] + [arg for name in ('codex-bin', 'cwd', 'model', 'effort', 'sandbox', 'inbox', 'events', 'output', 'progress', 'session-id-file') for arg in ('--' + name, 'unused')]
DEFAULTS = {'rpc-timeout': 10.0, 'close-grace': 3.0, 'app-server-eof-grace': 2.0, 'app-server-term-grace': 1.0, 'app-server-kill-grace': 1.0}


class TimeoutTests(unittest.TestCase):
    def parse(self, *extra):
        with patch('sys.argv', BASE + list(extra)), contextlib.redirect_stderr(io.StringIO()):
            return live.parse_args()

    def test_defaults(self):
        args = self.parse()
        for name, value in DEFAULTS.items():
            self.assertEqual(getattr(args, name.replace('-', '_')), value)

    def test_valid_values(self):
        for name in DEFAULTS:
            for value in (['0.25', '1'] if name == 'rpc-timeout' else ['0', '0.25', '1']):
                with self.subTest(name=name, value=value):
                    self.assertEqual(getattr(self.parse(f'--{name}={value}'), name.replace('-', '_')), float(value))

    def test_invalid_values(self):
        for name in DEFAULTS:
            values = ['-1', 'nan', 'inf', '-inf', 'not-a-number']
            if name == 'rpc-timeout':
                values += ['0']
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(SystemExit) as error:
                        self.parse(f'--{name}={value}')
                    self.assertEqual(error.exception.code, 2)
