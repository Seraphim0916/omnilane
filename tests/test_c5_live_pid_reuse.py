import offline_env  # Activate suite isolation for direct file execution.
import signal
import unittest
from unittest.mock import call, patch
from test_c5_live_timeout_validation import live


class PidReuseTests(unittest.TestCase):
    def check(self, identities, expected, errors=None):
        with patch.object(live.CodexLiveClient, 'process_identity', side_effect=identities) as identity, patch.object(live.os, 'kill', side_effect=errors) as kill, patch.object(live.time, 'sleep') as sleep:
            live.CodexLiveClient.terminate_tracked_descendants({123: 'old'})
        self.assertEqual(kill.call_args_list, [call(123, sig) for sig in expected])
        self.assertEqual(identity.call_args_list, [call(123)] * len(identities))
        self.assertLessEqual(sleep.call_count, 2)

    def test_initial_mismatch(self):
        self.check(['reused'], [])

    def test_reused_before_kill(self):
        self.check(['old', 'reused'], [signal.SIGTERM])

    def test_disappeared(self):
        self.check([None], [])
        self.check(['old', None], [signal.SIGTERM])
        self.check(['old'], [signal.SIGTERM], ProcessLookupError())
        self.check(['old', 'old'], [signal.SIGTERM, signal.SIGKILL], [None, ProcessLookupError()])

    def test_permission_denied(self):
        self.check(['old'], [signal.SIGTERM], PermissionError())
        self.check(['old', 'old'], [signal.SIGTERM, signal.SIGKILL], [None, PermissionError()])

    def test_same_identity(self):
        self.check(['old', 'old'], [signal.SIGTERM, signal.SIGKILL])
