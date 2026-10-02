"""Cancellation audit and late output remain truthful and retrievable."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/lib/job_cancel.py'


class CancelResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='omnilane-cancel-results-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.env, violations = isolated_environment(root / 'isolated', os.environ['PATH'])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.env['OMNILANE_HOME'] = str(root / 'home')
        self.job_id = '20261002-000000-123-1'
        self.job = root / 'home/jobs' / self.job_id
        self.job.mkdir(parents=True)
        (self.job / 'exit').write_text('143\n')

    def cli(self, *args):
        return subprocess.run(['bash', str(ROOT / 'scripts/jobs.sh'), *args],
                              env=self.env, text=True, capture_output=True, timeout=12)

    def helper(self):
        spec = importlib.util.spec_from_file_location('job_cancel_test', HELPER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_late_tmp_retrieved_but_not_claimed_complete(self):
        (self.job / 'out.txt.tmp').write_text('late partial answer\n')
        got = self.cli('result', self.job_id)
        self.assertEqual(got.returncode, 143)
        self.assertEqual(got.stdout, 'late partial answer\n')
        self.assertIn('partial or unknown', got.stderr)
        data = json.loads(self.cli('--json', 'result', self.job_id).stdout)['job']
        self.assertEqual(data['result_provenance']['status'], 'partial_or_unknown')
        self.assertEqual(data['result_provenance']['source'], 'out.txt.tmp')
        self.assertTrue((self.job / 'out.txt.tmp').exists())
        self.assertFalse((self.job / 'out.txt').exists())

    def test_verified_late_completion_preserves_cancel_exit(self):
        module = self.helper()
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': time.time_ns()})
        (self.job / 'out.txt.tmp').write_text('late finished answer\n')
        module.record_completion(self.job, 0)
        got = self.cli('result', self.job_id)
        self.assertEqual(got.returncode, 143)
        self.assertEqual(got.stdout, 'late finished answer\n')
        self.assertIn('completed after cancellation', got.stderr)
        data = json.loads(self.cli('--json', 'result', self.job_id).stdout)['job']
        self.assertEqual(data['result_provenance']['status'], 'completed_after_cancel')
        (self.job / 'out.txt.tmp').write_text('changed after marker\n')
        got = self.cli('result', self.job_id)
        self.assertIn('partial or unknown', got.stderr)

    def test_failed_runner_and_old_completion_not_labeled_late(self):
        module = self.helper()
        (self.job / 'out.txt').write_text('partial\n')
        module.record_completion(self.job, 143)
        self.assertIn('partial or unknown', self.cli('result', self.job_id).stderr)
        module.record_completion(self.job, 0)
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': time.time_ns()})
        self.assertNotIn('completed after cancellation', self.cli('result', self.job_id).stderr)

    def test_exit_137_stays_137_and_output_symlink_is_not_read(self):
        (self.job / 'exit').write_text('137\n')
        (self.job / 'out.txt.tmp').symlink_to(self.job / 'exit')
        got = self.cli('result', self.job_id)
        self.assertEqual(got.returncode, 137)
        self.assertEqual(got.stdout, '')

    def test_incomplete_cleanup_recorded_even_with_exit(self):
        module = self.helper()
        def engine(directory):
            self.assertEqual(directory, self.job)
            first = json.loads((self.job / 'cancel.json').read_text())
            self.assertEqual(first['status'], 'in_progress')
            return {'complete': False, 'signals': [{'signal': 'TERM'}],
                    'survivors': [{'pid': 123, 'started': 'fixture'}],
                    'reason': 'cannot confirm identity', 'exit_code': 143}
        with patch.object(module, 'cancel_engine', engine):
            self.assertEqual(module.cancel_job(self.job), 125)
        audit = json.loads((self.job / 'cancel.json').read_text())
        self.assertEqual(audit['status'], 'incomplete')
        self.assertEqual(audit['final_exit_code'], 143)
        self.assertTrue(audit['survivors'])
        self.assertEqual(audit['caller']['origin'], 'unknown')
        self.assertEqual(audit['caller']['uid'], os.getuid())
        self.assertTrue(audit['requested_at'])
        self.assertTrue(audit['signals'])

    def test_cancel_audit_survives_engine_error_and_repeated_attempts(self):
        module = self.helper()
        with patch.object(module, 'cancel_engine', side_effect=RuntimeError('fixture error')):
            self.assertEqual(module.cancel_job(self.job), 125)
            self.assertEqual(module.cancel_job(self.job), 125)
        audit = json.loads((self.job / 'cancel.json').read_text())
        self.assertEqual(audit['status'], 'incomplete')
        self.assertEqual(len(audit['attempts']), 1)
        self.assertIn('fixture error', audit['reason'])

    def test_incomplete_timeout_all_surfaces_and_safe_residuals(self):
        # Old exit alone must not hide an incomplete timeout cleanup record.
        (self.job / 'exit').write_text('124\n')
        (self.job / 'out.txt').write_text('timeout partial output\n')
        (self.job / 'process-cleanup.json').write_text(json.dumps({
            'schema_version': 1, 'instance': 'fixture', 'signals': [], 'complete': False, 'exit_code': 125, 'command_exit': 124,
            'reason': 'detached descendants', 'survivors': [
                {'pid': 987, 'started': 'boot:123', 'command': '/bin/fake --secret=HIDDEN', 'args': 'HIDDEN'}]}))
        for command in ('status', 'result'):
            got = self.cli('--json', command, self.job_id)
            self.assertEqual(got.returncode, 125 if command == 'result' else 0)
            job = json.loads(got.stdout)['job']
            self.assertEqual(job['state'], 'incomplete')
            self.assertEqual(job['exit_code'], 125)
            self.assertEqual(job['cleanup']['residual_count'], 1)
            self.assertEqual(job['cleanup']['survivors'], [{'pid': 987, 'started': 'boot:123', 'command': 'fake'}])
            self.assertNotIn('HIDDEN', got.stdout + got.stderr)
        for command in ('status', 'result', 'wait'):
            got = self.cli(command, self.job_id)
            self.assertEqual(got.returncode, 125 if command in ('result', 'wait') else 0)
            self.assertIn('cleanup incomplete; residual 1', got.stdout + got.stderr)
        got = self.cli('--json', 'list', '--status', 'incomplete')
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(json.loads(got.stdout)['jobs'][0]['state'], 'incomplete')
        self.assertIn('residual 1', self.cli('list').stdout)

    def test_incomplete_cancel_without_exit_is_not_running(self):
        (self.job / 'exit').unlink()
        (self.job / 'cancel.json').write_text(json.dumps({'status': 'incomplete', 'survivors': []}))
        got = self.cli('--json', 'status', self.job_id)
        self.assertEqual(json.loads(got.stdout)['job']['state'], 'incomplete')
        self.assertEqual(self.cli('result', self.job_id).returncode, 125)

    def test_already_finished_cancel_does_not_relabel_output(self):
        module = self.helper()
        (self.job / 'exit').write_text('0\n')
        (self.job / 'out.txt').write_text('finished\n')
        with patch.object(module, 'cancel_engine', return_value={
                'complete': True, 'already_finished': True, 'signals': [], 'survivors': [], 'exit_code': 0}):
            self.assertEqual(module.cancel_job(self.job), 0)
        got = self.cli('result', self.job_id)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stdout, 'finished\n')
        self.assertEqual(got.stderr, '')

    def test_foreground_cancel_request_precedes_result_and_exit(self):
        module = self.helper()
        (self.job / 'cancel-request.json').write_text(json.dumps({'requested_at': time.time()}))
        (self.job / 'out.txt').write_text('graceful foreground final\n')
        module.record_completion(self.job, 0)
        time.sleep(.01)
        (self.job / 'exit').write_text('143\n')
        self.assertIn('completed after cancellation', self.cli('result', self.job_id).stderr)

    def test_signal_wrapper_does_not_overwrite_success_evidence(self):
        module = self.helper()
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': time.time_ns()})
        (self.job / 'out.txt.tmp').write_text('completed provider answer\n')
        module.record_completion(self.job, 0)
        module.record_completion(self.job, 143)
        self.assertIn('completed after cancellation', self.cli('result', self.job_id).stderr)

    def check_graceful_provider(self, complete=True, foreground=False):
        root = self.job.parents[2]
        gate = root / 'graceful-provider'
        gate.write_text('''#!/usr/bin/env python3
import os, signal, sys, time
from pathlib import Path
output = Path(sys.argv[5] + '.tmp')
def finish(signum, frame):
    output.write_text('graceful provider final answer\\n')
    raise SystemExit(PROVIDER_EXIT)
signal.signal(signal.SIGTERM, finish)
Path(os.environ['GRACE_READY']).write_text(str(output))
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    time.sleep(.02)
raise SystemExit(99)
'''.replace('PROVIDER_EXIT', '0' if complete else '143'))
        gate.chmod(0o755)
        home = Path(self.env['OMNILANE_HOME'])
        (home / 'routing.local.yaml').write_text(f'fixture: exec "{gate}" -\n')
        ready = root / 'graceful.ready'
        self.env['GRACE_READY'] = str(ready)
        args = ['bash', str(ROOT / 'scripts/dispatch.sh'), '--single-shot',
                'fixture', 'graceful offline fixture']
        process = None
        if foreground:
            args.remove('--single-shot')
            process = subprocess.Popen(args, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def cleanup():
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=12)
            self.addCleanup(cleanup)
        else:
            args.insert(2, '--background')
            dispatched = subprocess.run(args, env=self.env, text=True, capture_output=True, timeout=10)
            self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        if not ready.exists() and process is not None and process.poll() is not None:
            stdout, stderr = process.communicate()
            self.fail(f'foreground ended before readiness rc={process.returncode}: {stdout} {stderr}')
        self.assertTrue(ready.exists(), 'provider did not reach signal-ready barrier')
        job_id = Path(ready.read_text()).parent.name
        if foreground:
            process.terminate()
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 143, stdout + stderr)
        else:
            cancelled = self.cli('cancel', job_id)
            self.assertEqual(cancelled.returncode, 0, cancelled.stdout + cancelled.stderr)
        got = self.cli('result', job_id)
        self.assertEqual(got.returncode, 143, got.stdout + got.stderr)
        self.assertEqual(got.stdout, 'graceful provider final answer\n')
        self.assertIn('completed after cancellation' if complete else 'partial or unknown', got.stderr)
        job = json.loads(self.cli('--json', 'result', job_id).stdout)['job']
        self.assertEqual(job['result_provenance']['status'], 'completed_after_cancel' if complete else 'partial_or_unknown')

    def test_graceful_provider_completion_during_real_cancel(self):
        self.check_graceful_provider()

    def test_partial_provider_output_during_real_cancel_stays_unknown(self):
        self.check_graceful_provider(complete=False)

    def test_graceful_provider_completion_during_foreground_term(self):
        self.check_graceful_provider(foreground=True)

    def test_legacy_completed_cancel_preserves_status_and_result(self):
        for rc in (0, 7):
            (self.job / 'exit').write_text(str(rc) + '\n')
            (self.job / 'out.txt').write_text('legacy finished\n')
            got = self.cli('cancel', self.job_id)
            self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
            self.assertIn(f'already finished (exit {rc})', got.stdout)
            result = self.cli('result', self.job_id)
            self.assertEqual(result.returncode, rc)
            self.assertEqual(result.stdout, 'legacy finished\n')
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(self.cli('--json', 'status', self.job_id).stdout)['job']['state'], 'done')

    def test_legacy_invalid_exit_cancel_preserves_invalid_metadata(self):
        (self.job / 'exit').write_text('invalid\n')
        got = self.cli('cancel', self.job_id)
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn('already finished (invalid exit metadata)', got.stdout)
        self.assertEqual(self.cli('status', self.job_id).returncode, 1)

    def test_legacy_no_worker_cancel_preserves_unfinished_state(self):
        (self.job / 'exit').unlink()
        for pid in (None, '2147483647'):
            if pid is not None:
                (self.job / 'pid').write_text(pid + '\n')
            got = self.cli('cancel', self.job_id)
            self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
            self.assertIn('not running (no live worker to cancel)', got.stdout)
            status = json.loads(self.cli('--json', 'status', self.job_id).stdout)['job']
            self.assertEqual(status['state'], 'running' if pid is None else 'dead')
            self.assertFalse((self.job / 'exit').exists())

    def test_finalization_publication_failure_is_incomplete_without_cancel(self):
        (self.job / 'exit').write_text('143\n')
        (self.job / 'process-cleanup.json').write_text(json.dumps({'schema_version': 1, 'instance': 'fixture', 'signals': [], 'complete': True, 'survivors': []}))
        (self.job / 'finalization-error').write_text('completion publication failed\n')
        job = json.loads(self.cli('--json', 'status', self.job_id).stdout)['job']
        self.assertEqual(job['state'], 'incomplete')
        self.assertEqual(job['exit_code'], 125)
        self.assertEqual(job['cleanup']['residual_count'], 0)
        self.assertEqual(job['cleanup']['reason'], 'completion publication failed')
        self.assertEqual(self.cli('result', self.job_id).returncode, 125)
        self.assertEqual((self.job / 'exit').read_text(), '143\n')


    def test_earlier_provider_success_later_wrapper_cannot_promote(self):
        module = self.helper()
        output = self.job / 'out.txt'
        output.write_text('provider completed before cancellation\n')
        module.record_completion(self.job, 0)
        first = json.loads((self.job / 'runner-completion.json').read_text())
        requested = time.time_ns()
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': requested})
        module.record_completion(self.job, 0)
        final = json.loads((self.job / 'runner-completion.json').read_text())
        self.assertEqual(final['completed_at_ns'], first['completed_at_ns'])
        self.assertEqual(module.result_provenance(self.job, 143)[1]['status'], 'partial_or_unknown')

    def test_delayed_marker_uses_keeper_observation_before_cancel(self):
        module = self.helper()
        (self.job / 'out.txt').write_text('already complete\n')
        observed = time.time_ns()
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': observed + 1})
        module.record_completion(self.job, 0, completed_at_ns=observed)
        self.assertEqual(json.loads((self.job / 'runner-completion.json').read_text())['completed_at_ns'], observed)
        self.assertEqual(module.result_provenance(self.job, 143)[1]['status'], 'partial_or_unknown')

    def test_old_output_cannot_be_promoted_by_late_observation(self):
        module = self.helper()
        output = self.job / 'out.txt'
        output.write_text('output existed before cancellation\n')
        observed_mtime = output.stat().st_mtime_ns
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': max(time.time_ns(), observed_mtime)})
        module.record_completion(self.job, 0)
        self.assertEqual(module.result_provenance(self.job, 143)[1]['status'], 'partial_or_unknown')

    def test_equal_coarse_output_timestamp_stays_unknown(self):
        module = self.helper()
        requested = time.time_ns()
        output = self.job / 'out.txt'
        output.write_text('finished but timestamp resolution is inconclusive\n')
        os.utime(output, ns=(requested, requested))
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': requested})
        module.record_completion(self.job, 0, completed_at_ns=requested + 1)
        self.assertEqual(module.result_provenance(self.job, 143)[1]['status'], 'partial_or_unknown')

    def test_earlier_keeper_observation_replaces_later_wrapper_stamp(self):
        module = self.helper()
        (self.job / 'out.txt').write_text('already complete\n')
        observed = time.time_ns()
        module.atomic_json(self.job / 'cancel.json', {'requested_at_ns': observed + 1})
        module.record_completion(self.job, 0)
        module.record_completion(self.job, 0, completed_at_ns=observed)
        self.assertEqual(json.loads((self.job / 'runner-completion.json').read_text())['completed_at_ns'], observed)
        self.assertEqual(module.result_provenance(self.job, 143)[1]['status'], 'partial_or_unknown')

    def test_unknown_residuals_preserve_last_observed_rows(self):
        (self.job / 'process-cleanup.json').write_text(json.dumps({
            'schema_version': 1, 'instance': 'fixture', 'signals': [], 'complete': False, 'residual_unknown': True,
            'survivors': [{'pid': 987, 'started': 'observed:123', 'command': '/bin/fake',
                           'liveness_unconfirmed': True}], 'exit_code': 125}))
        job = json.loads(self.cli('--json', 'status', self.job_id).stdout)['job']
        self.assertEqual(job['state'], 'incomplete')
        self.assertTrue(job['cleanup']['residual_unknown'])
        self.assertTrue(job['cleanup']['survivors'][0]['liveness_unconfirmed'])
        for command in ('status', 'result', 'wait', 'list'):
            got = self.cli(command, self.job_id) if command != 'list' else self.cli(command)
            self.assertIn('residual unknown; last observed 1', got.stdout + got.stderr)

    def test_malformed_cleanup_never_claims_done(self):
        records = [
            {'schema_version': 1, 'complete': 'true', 'survivors': []},
            {'schema_version': 2, 'complete': True, 'survivors': []},
            {'schema_version': True, 'complete': True, 'survivors': []},
            {'complete': True, 'survivors': []},
            {'schema_version': 1, 'complete': True, 'survivors': 'bad'},
            {'schema_version': 1, 'complete': True, 'survivors': [{'pid': True}]},
            {'schema_version': 1, 'complete': True, 'survivors': [{'pid': 12, 'started': 'a', 'command': 'fake'}]},
            {'schema_version': 1, 'complete': True, 'survivors': [], 'residual_unknown': 'false'},
        ]
        for record in records:
            with self.subTest(record=record):
                record = dict(instance='fixture', signals=[], **record)
                (self.job / 'process-cleanup.json').write_text(json.dumps(record))
                got = self.cli('--json', 'status', self.job_id)
                job = json.loads(got.stdout)['job']
                self.assertEqual(job['state'], 'incomplete')
                self.assertEqual(job['exit_code'], 125)
                self.assertTrue(job['cleanup']['residual_unknown'])
                self.assertIn('residual unknown', self.cli('result', self.job_id).stderr)
        (self.job / 'process-cleanup.json').write_text('{not valid JSON')
        self.assertEqual(self.cli('result', self.job_id).returncode, 125)

    def test_cancel_enumeration_failure_keeps_unknown_in_audit(self):
        module = self.helper()
        with patch.object(module, 'cancel_engine', return_value={
                'complete': False, 'residual_unknown': True, 'signals': [], 'survivors': [
                    {'pid': 987, 'started': 'observed:123', 'command': 'fake', 'liveness_unconfirmed': True}]}):
            self.assertEqual(module.cancel_job(self.job), 125)
        audit = json.loads((self.job / 'cancel.json').read_text())
        self.assertTrue(audit['residual_unknown'])
        self.assertTrue(audit['survivors'][0]['liveness_unconfirmed'])
        self.assertIn('residual unknown; last observed 1', self.cli('status', self.job_id).stdout)

    def test_malformed_cleanup_path_is_incomplete(self):
        path = self.job / 'process-cleanup.json'
        path.symlink_to(self.job / 'missing-cleanup-target')
        job = json.loads(self.cli('--json', 'status', self.job_id).stdout)['job']
        self.assertEqual(job['state'], 'incomplete')
        self.assertTrue(job['cleanup']['residual_unknown'])
        path.unlink()
        path.mkdir()
        self.assertEqual(self.cli('result', self.job_id).returncode, 125)
        self.assertEqual(json.loads(self.cli('--json', 'status', self.job_id).stdout)['job']['state'], 'incomplete')

    def test_large_late_output_remains_available_without_unbounded_hash(self):
        module = self.helper()
        output = self.job / 'out.txt.tmp'
        with output.open('wb') as handle:
            handle.truncate(16 * 1024 * 1024 + 1)
        self.assertIsNone(module.fingerprint(output))
        data = json.loads(self.cli('--json', 'result', self.job_id).stdout)['job']
        self.assertTrue(data['output_available'])
        self.assertEqual('out.txt.tmp', data['result_provenance']['source'])
        self.assertEqual('partial_or_unknown', data['result_provenance']['status'])

    def test_fingerprint_time_budget_returns_unknown(self):
        module = self.helper()
        output = self.job / 'out.txt.tmp'
        output.write_text('owned output')
        with patch.object(module.time, 'monotonic', side_effect=[0, 1]):
            self.assertIsNone(module.fingerprint(output))

    def test_survivor_command_control_bytes_are_sanitized(self):
        module = self.helper()
        rows = module.safe_survivors([{'pid': 123, 'started': 'observed:1',
                                      'command': '/bin/tool\x1b[31m --secret=HIDDEN'}])
        self.assertNotIn('\x1b', rows[0]['command'])
        self.assertNotIn('HIDDEN', rows[0]['command'])
        self.assertEqual('tool?[31m', rows[0]['command'])
