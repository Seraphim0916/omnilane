import offline_env  # Activate suite isolation for direct file execution.
import io
import json
from types import SimpleNamespace
import unittest
from test_c5_live_timeout_validation import live


class EnvelopeTests(unittest.TestCase):
    def client(self, *messages):
        client = live.CodexLiveClient(SimpleNamespace(rpc_timeout=1))
        client.events = io.StringIO()
        for message in messages:
            client.messages.put(('message', json.dumps(message)))
        return client

    def test_success_only(self):
        self.assertEqual(self.client({'id': 1, 'result': {'ok': True}}).wait_response(1, 'test'), {'ok': True})

    def test_invalid_envelopes(self):
        for payload in ({'error': {'code': -1}}, {'result': {}, 'error': {'code': -1}}, {}, {'result': {}, 'error': None}):
            with self.subTest(payload=payload), self.assertRaises(live.ProtocolError):
                self.client(dict(id=1, **payload)).wait_response(1, 'test')

    def test_unrelated_id_ignored(self):
        client = self.client({'id': 2, 'result': {}, 'error': {}}, {'id': 1, 'result': {'correct': True}})
        self.assertEqual(client.wait_response(1, 'test'), {'correct': True})
