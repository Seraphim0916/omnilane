"""Offline session preflight and auth-preserving environment regressions."""
import offline_env  # noqa: F401
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import probe_failure
import probe_sweep

KEY = 'OMNILANE_PROBE_ANY_SESSION'


class ProbeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.calls = []
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(KEY, None)

    def fake(self, name, argv, **kwargs):
        self.calls.append((name, argv, kwargs, os.environ.get(KEY)))
        evidence = kwargs['root'] / 'evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        record = {'verdict': 'pass'}
        (evidence / (name + '.json')).write_text(json.dumps(record))
        return record

    def run_sweep(self, vendor='claude', fake=None, platform='darwin', manager='Background', **kwargs):
        with patch.object(sys, 'platform', platform), \
                patch.object(probe_sweep, 'session_manager', return_value=manager), \
                patch.object(probe_sweep.probe_module, 'probe', fake or self.fake):
            return probe_failure.run_sweep(vendor, self.root, log=lambda *_: None, **kwargs)

    def test_background_success_probes_cheapest_then_full_sweep(self):
        result = self.run_sweep()
        entries = probe_sweep.plan('claude')
        cheapest = min(entries, key=lambda e: (probe_sweep.build_overlay.ROWS[e['config_id']]['score'], e['config_id']))
        self.assertEqual(result['outcome'], 'done')
        self.assertEqual(len(self.calls), len(entries) + 1)
        first = self.calls[0]
        self.assertEqual(first[0], cheapest['name'])
        self.assertEqual(first[1], probe_sweep.command('claude', cheapest['model'], cheapest['effort']))
        self.assertNotEqual(first[2]['root'], self.root)
        self.assertIsNone(first[3])
        self.assertTrue(all(call[3] is None for call in self.calls[1:]))
        self.assertNotIn(KEY, os.environ)

    def test_any_preflight_failure_is_unprobeable_without_retry(self):
        for reason in ('missing-expected-token', 'model-mismatch', 'timeout', 'rate-limited', 'not-authenticated', 'invalid-json'):
            with self.subTest(reason=reason):
                self.calls.clear()
                def failed(*args, **kwargs):
                    self.fake(*args, **kwargs)
                    return {'verdict': 'fail', 'verdict_reason': reason}
                result = self.run_sweep(fake=failed)
                self.assertEqual(result['outcome'], 'unprobeable')
                self.assertEqual(result['passed'], [])
                self.assertEqual(result['failed'], [])
                self.assertIn('Aqua (GUI)', result['detail'])
                self.assertEqual(len(self.calls), 1)
                self.assertFalse((self.root / 'evidence').exists())
                self.assertNotIn(KEY, os.environ)

    def test_exception_fails_closed(self):
        for error in (OSError('private'), ValueError('private')):
            with self.subTest(error=type(error)):
                result = self.run_sweep(fake=unittest.mock.Mock(side_effect=error))
                self.assertEqual(result['outcome'], 'unprobeable')
                self.assertNotIn('private', result['detail'])

    def test_explicit_override_is_preserved(self):
        os.environ[KEY] = '1'
        self.run_sweep()
        self.assertEqual(len(self.calls), len(probe_sweep.plan('claude')))
        self.assertEqual(os.environ[KEY], '1')

    def test_other_sessions_and_codex_need_no_preflight(self):
        for vendor, platform, manager in [('claude', 'linux', 'Background'), ('claude', 'darwin', 'Aqua'), ('claude', 'darwin', ''), ('codex', 'darwin', 'Background')]:
            with self.subTest(vendor=vendor, platform=platform, manager=manager):
                self.calls.clear()
                self.run_sweep(vendor, platform=platform, manager=manager)
                self.assertEqual(len(self.calls), len(probe_sweep.plan(vendor)))

    def test_no_missing_rows_means_no_preflight(self):
        (self.root / 'evidence').mkdir()
        for entry in probe_sweep.plan('claude'):
            (self.root / 'evidence' / (entry['name'] + '.json')).write_text('{}')
        result = self.run_sweep(only_missing=True)
        self.assertEqual(result['outcome'], 'done')
        self.assertEqual(self.calls, [])

    def test_failed_preflight_preserves_expiring_evidence(self):
        entry = probe_sweep.plan('claude')[0]
        (self.root / 'evidence').mkdir()
        old = self.root / 'evidence' / (entry['name'] + '.json')
        old.write_text('unchanged')
        result = self.run_sweep(fake=lambda *a, **k: {'verdict': 'fail'}, refresh_expired=[entry['config_id']])
        self.assertEqual(result['outcome'], 'unprobeable')
        self.assertEqual(old.read_text(), 'unchanged')
        self.assertFalse((self.root / 'retry-before').exists())

    def test_fake_grok_executable_receives_isolation_in_preflight_and_sweep(self):
        executable = self.root / "grok"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "keys = " + repr(probe_failure.GROK_COMPAT_SWITCHES) + "\n"
            "assert all(os.environ.get(key) == '0' for key in keys)\n"
            "model = sys.argv[sys.argv.index('-m') + 1]\n"
            "print(json.dumps({'result': 'GROK_SELECTOR_OK', 'modelUsage': {model: {}}}))\n")
        executable.chmod(0o755)
        with patch.object(sys, 'platform', 'darwin'), \
                patch.object(probe_sweep, 'session_manager', return_value='Background'), \
                patch.object(probe_sweep.build_overlay, 'cli_command', return_value=str(executable)):
            result = probe_failure.run_sweep('grok', self.root, log=lambda *_: None)
        self.assertEqual(result['outcome'], 'done')
        self.assertEqual(len(result['passed']), len(probe_sweep.plan('grok')))
        self.assertTrue(list((self.root / 'session-preflight' / 'evidence').glob('*.json')))
        self.assertNotIn(KEY, os.environ)

    def test_grok_uses_same_preflight_contract(self):
        result = self.run_sweep('grok')
        self.assertEqual(result['outcome'], 'done')
        self.assertEqual(len(self.calls), len(probe_sweep.plan('grok')) + 1)
        self.assertEqual(self.calls[0][2]['vendor'], 'grok')

    def test_gemini_preparation_failure_is_unprobeable(self):
        with patch.object(probe_failure, 'prepare_agy', return_value=unittest.mock.Mock(returncode=1)):
            result = self.run_sweep('gemini')
        self.assertEqual(result['outcome'], 'unprobeable')
        self.assertEqual(self.calls, [])

    def test_gemini_preserves_command_and_uses_private_preparation(self):
        prepared = unittest.mock.Mock(returncode=0, stdout='/fake/app-data')
        with patch.object(probe_failure, 'prepare_agy', return_value=prepared) as prepare:
            result = self.run_sweep('gemini')
        self.assertEqual(result['outcome'], 'done')
        self.assertEqual(len(self.calls), len(probe_sweep.plan('gemini')) + 1)
        name, argv, kwargs, _ = self.calls[0]
        entry = next(e for e in probe_sweep.plan('gemini') if e['name'] == name)
        self.assertEqual(argv, probe_sweep.command('gemini', entry['model'], entry['effort'], '/fake/app-data'))
        self.assertEqual(kwargs['vendor'], 'agy')
        self.assertIn('preflight-', kwargs['app_root'].name)
        self.assertIn(str(self.root / 'session-preflight' / 'work'), prepare.call_args_list[0].args[0])

    def test_override_restored_when_normal_sweep_raises(self):
        os.environ[KEY] = '0'
        with patch.object(probe_sweep, 'sweep', side_effect=RuntimeError('sweep failed')):
            with self.assertRaises(RuntimeError):
                self.run_sweep()
        self.assertEqual(os.environ[KEY], '0')

    def test_preflight_pass_does_not_prove_other_rows(self):
        def mismatch(name, argv, **kwargs):
            record = self.fake(name, argv, **kwargs)
            if kwargs['root'] == self.root:
                record.update(verdict='fail', verdict_reason='model-mismatch')
                for suffix in ('stdout', 'stderr'):
                    (self.root / 'evidence' / (name + '.' + suffix)).write_text('')
            return record
        result = self.run_sweep(fake=mismatch)
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual(result['passed'], [])
        self.assertEqual(len(result['failed']), len(probe_sweep.plan('claude')))

    def test_per_selector_option_reaches_rebased_sweep(self):
        for manager in ('Aqua', 'Background'):
            with self.subTest(manager=manager), \
                    patch.object(probe_failure, '_run_sweep', return_value={'outcome': 'done'}) as sweep:
                self.run_sweep(manager=manager, per_selector=True)
                self.assertTrue(sweep.call_args.kwargs['per_selector'])

    def test_unlisted_behavior_overrides_do_not_reach_probe(self):
        settings = {'CLAUDE_CODE_SIMPLE': '0', 'CODEX_HOME': '/fake/codex', 'GROK_HOME': '/fake/grok', 'GEMINI_CLI_HOME': '/fake/gemini'}
        os.environ.update(settings)
        def checked(*args, **kwargs):
            for key, value in settings.items():
                self.assertNotIn(key, os.environ)
            self.assertEqual(os.environ[probe_failure.ADVISOR_KILL_SWITCH], '1')
            return self.fake(*args, **kwargs)
        self.run_sweep(fake=checked)
        for key, value in settings.items():
            self.assertEqual(os.environ[key], value)


if __name__ == '__main__':
    unittest.main()
