import offline_env  # Activate suite isolation for direct file execution.
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_c5_live_timeout_validation import live


class EventScopeTests(unittest.TestCase):
    def check_scope(self, thread, accepted, pending=False):
        client = live.CodexLiveClient(SimpleNamespace())
        client.events = io.StringIO()
        client.thread_id = 'thread-A'
        client.turn_id = 't1'
        if pending:
            client.pending_turn_completions = {}
            client.turn_id = None
        params = {} if thread is None else {'threadId': thread}
        messages = [
            {'method': 'item/completed', 'params': dict(params, item={'type': 'agentMessage', 'text': 'hello'})},
            {'method': 'turn/completed', 'params': dict(params, turn={'id': 't1', 'status': 'completed'})},
        ]
        with patch.object(client, 'append_agent_text') as append:
            for message in messages:
                raw = json.dumps(message)
                self.assertEqual(client.record_event(raw), message)
        self.assertEqual(append.call_count, int(accepted))
        if accepted:
            append.assert_called_once_with('hello')
        if pending:
            self.assertEqual(client.pending_turn_completions, {'t1': True} if accepted else {})
        else:
            self.assertEqual(client.turn_id, None if accepted else 't1')
            self.assertEqual(client.last_turn_succeeded, accepted)
        self.assertEqual([json.loads(line) for line in client.events.getvalue().splitlines()], messages)

    def test_same_thread(self):
        self.check_scope('thread-A', True)

    def test_foreign_thread(self):
        self.check_scope('thread-B', False)

    def test_missing_thread_preserves_compatibility(self):
        self.check_scope(None, True)

    def test_pending_completion_scope(self):
        for thread, accepted in [('thread-A', True), ('thread-B', False), (None, True)]:
            with self.subTest(thread=thread):
                self.check_scope(thread, accepted, pending=True)
