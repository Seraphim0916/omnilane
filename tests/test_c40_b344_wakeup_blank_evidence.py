import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py'


class WakeupBlankEvidenceTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('wakeup_b344', SCRIPT)
        self.wakeup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.wakeup)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'registry.json'
        self.evidence = self.root / 'evidence.txt'
        self.args = SimpleNamespace(
            action='ack-accepted', home=str(self.root), thread_id='controller',
            host_id='local', run_id='run', event_key='event', claim_token='claim',
            result='PASS', evidence=str(self.evidence))
        self.state = {
            'schema_version': 1, 'controller_thread_id': 'controller',
            'controller_host_id': 'local', 'run_id': 'run', 'home': str(self.root),
            'workdir': str(self.root), 'status': 'registered', 'expires_at': 200,
            'jobs': {'job': 'frozen'}, 'events': {'event': {
                'job_id': 'job', 'exit_code': 0, 'state': 'delivered',
                'delivered_at': 90, 'claim_token': 'claim', 'lease_until': 150}}}

    def accept(self, data):
        self.evidence.write_bytes(data)
        exit_path = self.root / 'jobs' / 'job' / 'exit'
        original_read = self.wakeup.read_bytes

        def read(path, *args):
            if Path(path) == exit_path:
                return b'0\n'
            return original_read(path, *args)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.wakeup, 'registry_lock',
                side_effect=lambda args: contextlib.nullcontext(self.path)))
            stack.enter_context(mock.patch.object(self.wakeup.os.path, 'lexists', return_value=True))
            stack.enter_context(mock.patch.object(self.wakeup, 'read_json', return_value=self.state))
            stack.enter_context(mock.patch.object(self.wakeup.time, 'time', return_value=100))
            stack.enter_context(mock.patch.object(self.wakeup, 'identity', return_value='frozen'))
            stack.enter_context(mock.patch.object(self.wakeup, 'read_bytes', side_effect=read))
            writer = stack.enter_context(mock.patch.object(self.wakeup, 'write_json'))
            before = copy.deepcopy(self.state)
            if not data.strip():
                with self.assertRaisesRegex(self.wakeup.Invalid, 'empty acceptance evidence'):
                    self.wakeup.act(self.args)
                writer.assert_not_called()
                self.assertEqual(self.state, before)
                self.assertEqual(self.state['events']['event']['state'], 'delivered')
            else:
                result = self.wakeup.act(self.args)
                self.assertEqual(result['event_state'], 'accepted')
                event = self.state['events']['event']
                self.assertEqual(event['state'], 'accepted')
                self.assertEqual(event['result'], 'PASS')
                self.assertEqual(event['evidence'], {
                    'path': str(self.evidence), 'sha256': hashlib.sha256(data).hexdigest()})
                writer.assert_called_once_with(self.path, self.state)

    def test_blank_evidence_preserves_delivery_without_writing(self):
        for data in (b'', b' ', b'   ', b'\t', b'\r', b'\n', b' \t\r\n\t '):
            with self.subTest(data=data):
                self.accept(data)

    def test_nonblank_evidence_accepts_and_persists(self):
        self.accept(b' \tverified\r\n')


if __name__ == '__main__':
    unittest.main()
