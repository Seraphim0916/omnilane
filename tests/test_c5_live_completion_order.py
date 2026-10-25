import offline_env  # Activate suite isolation for direct file execution.
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_c5_live_timeout_validation import live


def completed(turn_id='t1', status='completed', error=None):
    return {'method': 'turn/completed', 'params': {'threadId': 'thread-A', 'turn': {'id': turn_id, 'status': status, 'error': error}}}


class CompletionOrderTests(unittest.TestCase):
    def run_order(self, notifications, early=True):
        client = live.CodexLiveClient(SimpleNamespace(rpc_timeout=1, effort='-'))
        client.events = io.StringIO()
        client.thread_id = 'thread-A'
        client.last_turn_succeeded = True
        response = {'id': 1, 'result': {'turn': {'id': 't1'}}}
        for message in (notifications + [response] if early else [response] + notifications):
            client.messages.put(('message', json.dumps(message)))
        with patch.object(client, 'send', return_value=1):
            client.start_turn('hello')
        if not early:
            self.assertEqual(client.turn_id, 't1')
            self.assertFalse(client.last_turn_succeeded)
            for _ in notifications:
                client.receive(0)
        return client

    def test_notification_first(self):
        client = self.run_order([completed()])
        self.assertIsNone(client.turn_id)
        self.assertTrue(client.last_turn_succeeded)

    def test_response_first(self):
        client = self.run_order([completed()], early=False)
        self.assertIsNone(client.turn_id)
        self.assertTrue(client.last_turn_succeeded)

    def test_failed_completion(self):
        for early in (True, False):
            for status, error in [('failed', None), ('completed', {'message': 'failed'})]:
                with self.subTest(early=early, status=status):
                    client = self.run_order([completed(status=status, error=error)], early)
                    self.assertIsNone(client.turn_id)
                    self.assertFalse(client.last_turn_succeeded)

    def test_unrelated_turn(self):
        for early in (True, False):
            with self.subTest(early=early):
                client = self.run_order([completed('other')], early)
                self.assertEqual(client.turn_id, 't1')
                self.assertFalse(client.last_turn_succeeded)

    def test_unrelated_does_not_replace_matching_completion(self):
        client = self.run_order([completed(), completed('other', 'failed')])
        self.assertIsNone(client.turn_id)
        self.assertTrue(client.last_turn_succeeded)
