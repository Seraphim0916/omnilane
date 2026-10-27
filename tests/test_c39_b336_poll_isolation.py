import offline_env  # Activate suite isolation for direct file execution.
import json
import pathlib
import unittest
from test_c39_b334_exit_consistency import Fixture


class PollIsolationTests(Fixture, unittest.TestCase):
    def test_bad_record_does_not_hide_good_or_resend_accepted(self):
        registry = pathlib.Path(self.registered()['registry'])
        self.finish(0)
        accepted = self.call('poll')['events'][0]
        self.ack('ack-delivered', accepted)
        proof = self.root / 'proof'
        proof.write_text('verified')
        self.ack('ack-accepted', accepted, '--result', 'PASS', '--evidence', proof)
        original = json.loads(registry.read_bytes())['events'][accepted['event_key']]
        secret = 'PRIVATE-CONTENT-SENTINEL'
        self.job('a-bad', secret)
        self.job('b-good', 0)
        self.call('prepare', '--workdir', self.work, '--job', 'a-bad', '--job', 'b-good')
        result = self.call('poll')
        self.assertEqual([event['job_id'] for event in result['events']], ['b-good'])
        self.assertEqual(result['errors'], [{'job_id': 'a-bad', 'code': 'invalid_exit'}])
        self.assertFalse(result['needs_disable'])
        self.assertNotIn(secret, json.dumps(result))
        persisted = json.loads(registry.read_bytes())
        self.assertEqual(persisted['status'], 'registered')
        self.assertEqual(persisted['events'][accepted['event_key']], original)
        good = result['events'][0]
        self.assertEqual(persisted['events'][good['event_key']]['claim_token'], good['claim_token'])
        again = self.call('poll')
        self.assertEqual(again['events'], [])
        self.assertEqual(again['errors'], result['errors'])
        self.assertFalse(again['needs_disable'])


if __name__ == '__main__':
    unittest.main()
