import offline_env  # Activate suite isolation for direct file execution.
import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py'


class WakeupPollGateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('wakeup_b345', SCRIPT)
        self.wakeup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.wakeup)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'registry.json'
        self.args = SimpleNamespace(home=str(self.root), run_id='run', lease_seconds=60)

    def state(self, status, automation):
        state = {'status': status, 'expires_at': 100, 'workdir': str(self.root),
                 'jobs': {'job': 'frozen'}, 'events': {}}
        if automation:
            state['automation_id'] = 'automation'
        return state

    def assert_gated(self, status, now, automation, expected_status, needs_disable):
        state = self.state(status, automation)
        before = copy.deepcopy(state)
        with mock.patch.object(self.wakeup, 'identity') as identity, \
             mock.patch.object(self.wakeup, 'read_bytes') as reader, \
             mock.patch.object(self.wakeup.os.path, 'lexists') as exists:
            result = self.wakeup.poll(self.args, state, self.path, now)
        self.assertEqual(result['status'], expected_status)
        self.assertIs(result['needs_disable'], needs_disable)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['errors'], [])
        self.assertEqual(state, before)
        identity.assert_not_called()
        reader.assert_not_called()
        exists.assert_not_called()

    def test_closed_returns_before_expiry_and_job_checks(self):
        for now in (99, 100, 101):
            for automation in (False, True):
                with self.subTest(now=now, automation=automation):
                    self.assert_gated('closed', now, automation, 'closed', False)

    def test_pending_before_expiry_never_reads_jobs(self):
        self.assert_gated('pending', 99, False, 'pending', False)

    def test_exact_expiry_disables_only_an_existing_automation(self):
        for status in ('pending', 'registered'):
            for automation in (False, True):
                with self.subTest(status=status, automation=automation):
                    self.assert_gated(status, 100, automation, 'expired', automation)

    def test_registered_before_expiry_reads_job_and_claims_completion(self):
        state = self.state('registered', True)
        with mock.patch.object(self.wakeup, 'identity', return_value='frozen') as identity, \
             mock.patch.object(self.wakeup, 'read_bytes', return_value=b'0\n') as reader, \
             mock.patch.object(self.wakeup.os.path, 'lexists', return_value=True):
            result = self.wakeup.poll(self.args, state, self.path, 99)
        identity.assert_called_once_with(self.args, 'job', str(self.root), {})
        reader.assert_called_once_with(self.root / 'jobs' / 'job' / 'exit', 32)
        self.assertEqual(result['status'], 'registered')
        self.assertFalse(result['needs_disable'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['job_id'], 'job')
        self.assertEqual(result['events'][0]['exit_code'], 0)


if __name__ == '__main__':
    unittest.main()
