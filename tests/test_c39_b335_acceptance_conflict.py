import offline_env  # Activate suite isolation for direct file execution.
import json
import pathlib
import unittest
from test_c39_b334_exit_consistency import Fixture


class AcceptanceConflictTests(Fixture, unittest.TestCase):
    def test_repeat_preserves_receipt_and_conflicts_publish_nothing(self):
        registry = pathlib.Path(self.registered()['registry'])
        self.finish(0)
        event = self.call('poll')['events'][0]
        self.ack('ack-delivered', event)
        proof = self.root / 'proof'
        proof.write_text('original evidence')
        self.ack('ack-accepted', event, '--result', 'PASS', '--evidence', proof)
        original = registry.read_bytes()
        accepted_at = json.loads(original)['events'][event['event_key']]['accepted_at']
        repeated = self.ack('ack-accepted', event, '--result', 'PASS', '--evidence', proof)
        self.assertEqual(repeated['event_state'], 'accepted')
        self.assertEqual(json.loads(registry.read_bytes())['events'][event['event_key']]['accepted_at'],
                         accepted_at)
        self.assertEqual(registry.read_bytes(), original)
        alternative = self.root / 'same-content-other-path'
        alternative.write_bytes(proof.read_bytes())
        for conflict in ('result', 'content', 'path'):
            with self.subTest(conflict=conflict):
                proof.write_text('changed evidence' if conflict == 'content' else 'original evidence')
                result = self.ack('ack-accepted', event, '--result',
                                  'FAIL' if conflict == 'result' else 'PASS',
                                  '--evidence', alternative if conflict == 'path' else proof, ok=False)
                self.assertEqual(json.loads(result.stderr), {'error': 'acceptance receipt conflict'})
                self.assertEqual(registry.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
