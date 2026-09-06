"""CLI contract tests; only synthetic public metadata is used."""
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py'


class WakeupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.home = self.root / 'home'
        self.work = self.root / 'work'
        self.work.mkdir()
        self.job('job-1')

    def job(self, name, rc=None):
        path = self.home / 'jobs' / name
        path.mkdir(parents=True)
        (path / 'meta.json').write_text(json.dumps({'lane': 'triage', 'vendor': 'codex',
            'model': 'test', 'mode': 'advise', 'workdir': str(self.work),
            'foreman_session': '', 'started': '2026-09-07T00:00:00Z'}))
        if rc is not None:
            (path / 'exit').write_text(str(rc) + '\n')

    def call(self, action, *args, ok=True, thread='controller-1', run='run-1'):
        p = subprocess.run([sys.executable, str(SCRIPT), action, '--home', str(self.home),
            '--thread-id', thread, '--host-id', 'local', '--run-id', run, *map(str, args)],
            capture_output=True, text=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        return p

    def prepare(self):
        return self.call('prepare', '--workdir', self.work, '--job', 'job-1')

    def receipt(self, status):
        path = self.root / (status + '.json')
        path.write_text(json.dumps({'source': 'automation_update', 'automation_id': 'auto-1',
            'controller_thread_id': 'controller-1', 'controller_host_id': 'local', 'status': status}))
        return path

    def registered(self):
        state = self.prepare()
        self.call('record-registration', '--automation-id', 'auto-1', '--evidence', self.receipt('ACTIVE'))
        return state

    def finish(self, rc=0):
        (self.home / 'jobs/job-1/exit').write_text(str(rc))

    def test_prepare_is_pending_and_never_creates_automation(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['tool_args']['targetThreadId'], 'controller-1')
        self.assertEqual(self.call('poll')['status'], 'pending')

    def test_unknown_thread_and_run_fail_closed(self):
        self.prepare()
        self.call('poll', thread='other-controller', ok=False)

    def test_registration_evidence_binding(self):
        self.prepare()
        evidence = self.receipt('ACTIVE')
        data = json.loads(evidence.read_text()); data['controller_host_id'] = 'wrong'
        evidence.write_text(json.dumps(data))
        self.call('record-registration', '--automation-id', 'auto-1', '--evidence', evidence, ok=False)

    def test_terminal_claim_delivery_acceptance_and_close(self):
        self.registered(); self.finish()
        event = self.call('poll')['events'][0]
        self.assertEqual(self.call('poll')['events'], [])
        self.call('ack-delivered', '--event-key', event['event_key'], '--claim-token', event['claim_token'])
        evidence = self.root / 'verification.txt'; evidence.write_text('fixture check passed')
        self.call('ack-accepted', '--event-key', event['event_key'], '--claim-token', event['claim_token'],
                  '--result', 'PASS', '--evidence', evidence)
        self.assertTrue(self.call('poll')['needs_disable'])
        result = self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('PAUSED'))
        self.assertEqual(result['status'], 'closed')
        self.assertEqual(self.call('poll')['events'], [])

    def test_failed_job_can_be_verified_as_fail(self):
        self.registered(); self.finish(9)
        event = self.call('poll')['events'][0]
        self.assertEqual(event['exit_code'], 9)
        self.call('ack-delivered', '--event-key', event['event_key'], '--claim-token', event['claim_token'])
        evidence = self.root / 'failure.txt'; evidence.write_text('expected failure reproduced')
        self.call('ack-accepted', '--event-key', event['event_key'], '--claim-token', event['claim_token'],
                  '--result', 'FAIL', '--evidence', evidence)
        self.assertTrue(self.call('poll')['needs_disable'])

    def test_concurrent_poll_only_one_claim(self):
        self.registered(); self.finish()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.call('poll'), range(2)))
        self.assertEqual(sum(len(r['events']) for r in results), 1)

    def test_expiry_requires_disable_not_delivery(self):
        result = self.registered()
        path = pathlib.Path(result['registry']); data = json.loads(path.read_text())
        data['expires_at'] = 0; path.write_text(json.dumps(data))
        self.finish()
        result = self.call('poll')
        self.assertEqual(result['status'], 'expired'); self.assertTrue(result['needs_disable'])
        self.assertEqual(result['events'], [])

    def test_metadata_mutable_fields_ignored_identity_change_rejected(self):
        self.registered()
        path = self.home / 'jobs/job-1/meta.json'; data = json.loads(path.read_text())
        data['finished'] = 'later'; path.write_text(json.dumps(data)); self.finish()
        self.assertEqual(len(self.call('poll')['events']), 1)
        data['model'] = 'different'; path.write_text(json.dumps(data))
        self.assertEqual(self.call('poll')['errors'][0]['code'], 'binding_changed')

    def test_symlinks_and_traversal_rejected(self):
        self.call('prepare', '--workdir', self.work, '--job', '../job-1', ok=False)
        path = self.home / 'jobs/job-1/meta.json'; path.unlink()
        path.symlink_to(self.root / 'elsewhere')
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', ok=False)

    def test_native_job_explicitly_unsupported(self):
        (self.home / 'jobs/job-1/native.json').write_text('private content never read')
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', ok=False)

    def test_accept_requires_delivery_and_valid_token(self):
        self.registered(); self.finish()
        event = self.call('poll')['events'][0]
        self.call('ack-delivered', '--event-key', event['event_key'], '--claim-token', 'wrong', ok=False)
        evidence = self.root / 'proof'; evidence.write_text('proof')
        self.call('ack-accepted', '--event-key', event['event_key'], '--claim-token', event['claim_token'],
                  '--result', 'PASS', '--evidence', evidence, ok=False)

    def test_adding_job_reuses_registration(self):
        self.registered(); self.job('job-2')
        result = self.call('prepare', '--workdir', self.work, '--job', 'job-2')
        self.assertEqual(result['status'], 'registered')
        self.assertEqual(result['automation_id'], 'auto-1')
        self.assertEqual(result['tracked_jobs'], ['job-1', 'job-2'])

    def test_closed_requires_paused_receipt(self):
        self.registered()
        self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('ACTIVE'), ok=False)

    def test_lease_expiry_redelivers_and_invalidates_old_token(self):
        result = self.registered(); self.finish()
        first = self.call('poll')['events'][0]
        path = pathlib.Path(result['registry']); data = json.loads(path.read_text())
        data['events'][first['event_key']]['lease_until'] = 0; path.write_text(json.dumps(data))
        second = self.call('poll')['events'][0]
        self.assertEqual(first['event_key'], second['event_key'])
        self.assertNotEqual(first['claim_token'], second['claim_token'])
        self.call('ack-delivered', '--event-key', first['event_key'], '--claim-token', first['claim_token'], ok=False)

    def test_terminal_changed_between_poll_and_ack_rejected(self):
        self.registered(); self.finish()
        event = self.call('poll')['events'][0]; self.finish(7)
        self.call('ack-delivered', '--event-key', event['event_key'], '--claim-token', event['claim_token'], ok=False)

    def test_untracked_job_never_delivered_and_explicit_stop(self):
        self.registered(); self.job('untracked', 0)
        self.assertEqual(self.call('poll')['events'], [])
        self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('PAUSED'), ok=False)
        result = self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('PAUSED'),
                           '--close-reason', 'stopped')
        self.assertEqual(result['status'], 'closed')

    def test_closed_new_run_archives_history_and_rejects_old_run(self):
        prepared = self.registered(); self.finish()
        event = self.call('poll')['events'][0]
        self.call('ack-delivered', '--event-key', event['event_key'], '--claim-token', event['claim_token'])
        proof = self.root / 'old-accepted.md'; proof.write_text('old run verification only')
        self.call('ack-accepted', '--event-key', event['event_key'], '--claim-token', event['claim_token'],
                  '--result', 'PASS', '--evidence', proof)
        self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('PAUSED'),
                  '--close-reason', 'stopped')
        self.job('job-2')
        fresh = self.call('prepare', '--workdir', self.work, '--job', 'job-2', run='run-2')
        self.assertEqual(fresh['status'], 'pending')
        self.assertEqual(fresh['tracked_jobs'], ['job-2'])
        self.assertIsNone(fresh['automation_id'])
        history = json.loads(pathlib.Path(fresh['previous_run_history']).read_text())
        self.assertEqual(history['run_id'], 'run-1')
        self.assertEqual(history['status'], 'closed')
        self.assertIn(event['event_key'], history['events'])
        self.assertEqual(history['events'][event['event_key']]['state'], 'accepted')
        self.assertEqual(history['events'][event['event_key']]['evidence']['path'], str(proof))
        self.call('poll', ok=False)
        self.call('ack-delivered', '--event-key', event['event_key'], '--claim-token', event['claim_token'], ok=False)
        current = json.loads(pathlib.Path(prepared['registry']).read_text())
        self.assertEqual(current['run_id'], 'run-2')
        self.assertEqual(current['events'], {})

    def test_active_run_never_overwritten(self):
        self.registered()
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', run='run-2', ok=False)
        self.assertEqual(self.call('poll')['status'], 'registered')

    def test_historical_run_id_never_reused(self):
        self.registered()
        self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('PAUSED'),
                  '--close-reason', 'stopped')
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', run='run-2')
        self.call('record-registration', '--automation-id', 'auto-1', '--evidence', self.receipt('ACTIVE'), run='run-2')
        self.call('closed', '--automation-id', 'auto-1', '--evidence', self.receipt('PAUSED'),
                  '--close-reason', 'stopped', run='run-2')
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', ok=False)
        self.call('poll', ok=False)
        self.assertEqual(self.call('poll', run='run-2')['status'], 'closed')

    def foreign_foreman(self):
        path = self.home / 'jobs/job-1/meta.json'
        data = json.loads(path.read_text()); data['foreman_session'] = 'foreign-claude-controller'
        path.write_text(json.dumps(data))
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def adoption(self, identity):
        path = self.root / 'adoption.json'
        path.write_text(json.dumps({'source': 'operator_adoption', 'operator_confirmed': True,
            'controller_thread_id': 'controller-1', 'controller_host_id': 'local', 'run_id': 'run-1',
            'job_identity_sha256': {'job-1': identity}}))
        return path

    def test_foreign_foreman_needs_explicit_scoped_adoption(self):
        frozen = self.foreign_foreman()
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', ok=False)
        receipt = self.adoption(frozen)
        self.call('prepare', '--workdir', self.work, '--job', 'job-1', '--adoption-evidence', receipt)
        self.call('record-registration', '--automation-id', 'auto-1', '--evidence', self.receipt('ACTIVE'))
        self.finish(); self.assertEqual(len(self.call('poll')['events']), 1)

    def test_foreign_adoption_wrong_binding_identity_or_unconfirmed_rejected(self):
        frozen = self.foreign_foreman()
        for field, value in [('controller_thread_id', 'wrong'), ('run_id', 'wrong'),
                             ('operator_confirmed', False), ('job_identity_sha256', {'job-1': 'wrong'})]:
            receipt = self.adoption(frozen); data = json.loads(receipt.read_text()); data[field] = value
            receipt.write_text(json.dumps(data))
            self.call('prepare', '--workdir', self.work, '--job', 'job-1', '--adoption-evidence', receipt, ok=False)


if __name__ == '__main__':
    unittest.main()
