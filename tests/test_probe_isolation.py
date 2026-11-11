"""Only documented, auth-preserving environment isolation is applied."""
import offline_env  # noqa: F401
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import probe_failure

KEYS = ('GROK_CURSOR_MCPS_ENABLED', 'GROK_CURSOR_HOOKS_ENABLED',
        'GROK_CLAUDE_MCPS_ENABLED', 'GROK_CLAUDE_HOOKS_ENABLED')


class ProbeIsolationTests(unittest.TestCase):
    def test_grok_disables_only_documented_compatibility_scanners(self):
        with patch.dict(os.environ):
            for key in KEYS:
                os.environ.pop(key, None)
            os.environ['GROK_HOME'] = '/fake/grok-home'
            with probe_failure.probe_environment('grok'):
                self.assertTrue(all(os.environ[key] == '0' for key in KEYS))
                self.assertNotIn('GROK_HOME', os.environ)
            self.assertTrue(all(key not in os.environ for key in KEYS))

    def test_previous_values_restored_on_exception(self):
        with patch.dict(os.environ, {key: '1' for key in KEYS}):
            with self.assertRaises(RuntimeError):
                with probe_failure.probe_environment('grok'):
                    raise RuntimeError('fake CLI failure')
            self.assertTrue(all(os.environ[key] == '1' for key in KEYS))

    def test_unlisted_overrides_removed_and_caller_environment_restored(self):
        settings = dict.fromkeys(KEYS, '1')
        settings.update(CLAUDE_CODE_SIMPLE='0', CODEX_HOME='/fake/codex',
                        GEMINI_CLI_HOME='/fake/gemini', GROK_HOME='/fake/grok')
        with patch.dict(os.environ, settings):
            for vendor in ('claude', 'codex', 'gemini', 'agy'):
                with probe_failure.probe_environment(vendor):
                    for key, value in settings.items():
                        self.assertNotIn(key, os.environ)
                    if vendor == 'claude':
                        self.assertEqual(os.environ[probe_failure.ADVISOR_KILL_SWITCH], '1')
            for key, value in settings.items():
                self.assertEqual(os.environ[key], value)


if __name__ == '__main__':
    unittest.main()
