"""Real offline subprocess checks for probe and preparation containment."""
import offline_env  # noqa: F401
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import probe_failure
import probe_sweep

POISON = {'SYNTHETIC_PRIVATE_VALUE': 'fixture-secret', 'PYTHONINSPECT': '1',
          'PYTHONPATH': '/fixture/injection', 'PYTHONSTARTUP': '/fixture/startup',
          'NODE_OPTIONS': '--require /fixture/injection', 'LD_PRELOAD': '/fixture/injection',
          'ANTHROPIC_BASE_URL': 'https://fixture.invalid', 'OPENAI_BASE_URL': 'https://fixture.invalid',
          'GROK_HOME': '/fixture/override', 'CLAUDE_CODE_SIMPLE': '1',
          'XAI_API_KEY': 'fake-not-a-credential', 'ANTHROPIC_API_KEY': 'fake-not-a-credential'}


class ProcessEnvironmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def script(self, source):
        path = self.root / 'fake.py'
        path.write_text(source)
        return [sys.executable, str(path)]

    def test_probe_child_cannot_echo_secrets_or_behavior_overrides(self):
        model = probe_sweep.plan("claude")[0]["model"]
        argv = self.script("import json,os\nprint(json.dumps({'result':'CLAUDE_SELECTOR_OK','modelUsage':{" + repr(model) + ":{}},'environment':dict(os.environ)}))\n")
        argv += ["--model", model]
        with patch.dict(os.environ, POISON):
            before = dict(os.environ)
            with probe_failure.probe_environment('claude'):
                record = probe_sweep.probe_module.probe('isolated', argv, root=self.root,
                            vendor='claude', expected_token='CLAUDE_SELECTOR_OK')
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(record['verdict'], 'pass')
        env = json.loads(Path(record['stdout']).read_text())['environment']
        self.assertTrue(set(POISON).isdisjoint(env))
        self.assertEqual(env['HOME'], os.environ['HOME'])
        self.assertEqual(env['PATH'], os.environ['PATH'])
        self.assertEqual(env['CLAUDE_CODE_DISABLE_ADVISOR_TOOL'], '1')

    def test_preparation_child_is_isolated_and_stdin_is_eof(self):
        argv = self.script("import json,os,sys\nprint(json.dumps({'env':dict(os.environ),'isolated':sys.flags.isolated,'stdin':sys.stdin.read()}))\n")
        with patch.dict(os.environ, POISON):
            result = probe_failure.prepare_agy(argv)
        data = json.loads(result.stdout)
        self.assertTrue(set(POISON).isdisjoint(data['env']))
        self.assertEqual(data['isolated'], 1)
        self.assertEqual(data['stdin'], '')

    def test_preparation_really_blocking_child_times_out(self):
        argv = self.script('import time\ntime.sleep(60)\n')
        start = time.monotonic()
        with patch.object(probe_failure, 'PREPARATION_TIMEOUT', 0.2):
            with self.assertRaises(subprocess.TimeoutExpired):
                probe_failure.prepare_agy(argv)
        self.assertLess(time.monotonic() - start, 4)

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX process groups required')
    def test_preparation_timeout_stops_descendants_in_its_group(self):
        pid_path = self.root / 'child.pid'
        argv = self.script(
            "import os,time\n"
            "pid=os.fork()\n"
            "if pid == 0:\n time.sleep(60)\n"
            "else:\n open(" + repr(str(pid_path)) + ", 'w').write(str(pid))\n time.sleep(60)\n")
        with patch.object(probe_failure, 'PREPARATION_TIMEOUT', 0.5):
            with self.assertRaises(subprocess.TimeoutExpired):
                probe_failure.prepare_agy(argv)
        pid = int(pid_path.read_text())
        deadline = time.monotonic() + 3
        while True:
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                                   capture_output=True, text=True).stdout.strip()
            if not state or state.startswith('Z'):
                break
            if time.monotonic() > deadline:
                os.kill(pid, 9)
                self.fail('preparation descendant survived its process-group timeout')
            time.sleep(0.05)

    def test_preparation_timeout_restores_pinned_dependency(self):
        original = probe_sweep.subprocess
        with patch.object(probe_failure, 'prepare_agy', side_effect=subprocess.TimeoutExpired([], 0.1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                probe_failure.run_sweep('gemini', self.root, log=lambda *_:None)
        self.assertIs(probe_sweep.subprocess, original)

    def test_normal_gemini_sweep_uses_controlled_preparation(self):
        entry = probe_sweep.plan('gemini')[0]
        def fake(name, argv, **kwargs):
            return {'verdict': 'pass'}
        with patch.object(probe_sweep, 'plan', return_value=[entry]), \
                patch.object(probe_sweep.probe_module, 'probe', fake), \
                patch.object(probe_failure, 'prepare_agy', return_value=subprocess.CompletedProcess([],0,'/fixture/app','')) as prepare:
            probe_failure.run_sweep('gemini', self.root, log=lambda *_:None)
        self.assertEqual(prepare.call_count, 1)

    def test_refresh_invalidated_is_forwarded_and_selected_even_if_record_exists(self):
        entry = probe_sweep.plan('claude')[0]
        (self.root / 'evidence').mkdir()
        (self.root / 'evidence' / (entry['name'] + '.json')).write_text('invalid')
        with patch.object(sys, 'platform', 'darwin'), \
                patch.object(probe_sweep, 'session_manager', return_value='Background'), \
                patch.object(probe_sweep, 'plan', return_value=[entry]), \
                patch.object(probe_failure, 'session_preflight', return_value=None) as preflight, \
                patch.object(probe_failure, '_run_sweep', return_value={'outcome':'done'}) as sweep:
            probe_failure.run_sweep('claude', self.root, only_missing=True, refresh_invalidated=[entry['config_id']])
        self.assertEqual(preflight.call_count, 1)
        self.assertEqual(sweep.call_args.kwargs['refresh_invalidated'], [entry['config_id']])

    def test_safe_failure_categories_never_include_provider_text(self):
        for reason, category in [('timeout: private-token', 'timeout'), ('model-mismatch: private-token','model-mismatch'), ('rate-limited: private-token','rate-limited'), ('unrecognized private-token','probe-failed')]:
            with self.subTest(reason=reason), patch.object(sys,'platform','darwin'), \
                    patch.object(probe_sweep,'session_manager',return_value='Background'), \
                    patch.object(probe_sweep.probe_module,'probe',return_value={'verdict':'fail','verdict_reason':reason}):
                result = probe_failure.run_sweep('claude',self.root,log=lambda *_:None)
            self.assertEqual(result['outcome'],'unprobeable')
            self.assertIn(category,result['detail'])
            self.assertNotIn('private-token',result['detail'])
            self.assertNotIn('only an Aqua',result['detail'])


if __name__ == '__main__':
    unittest.main()
