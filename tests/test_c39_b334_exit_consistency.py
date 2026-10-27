import offline_env  # Activate suite isolation for direct file execution.
import pathlib
import unittest
import test_completion_wakeup as existing


class Fixture:
    setUp = existing.WakeupTests.setUp
    job = existing.WakeupTests.job
    call = existing.WakeupTests.call
    prepare = existing.WakeupTests.prepare
    receipt = existing.WakeupTests.receipt
    registered = existing.WakeupTests.registered
    finish = existing.WakeupTests.finish

    def ack(self, action, event, *extra, **kwargs):
        return self.call(action, '--event-key', event['event_key'],
                         '--claim-token', event['claim_token'], *extra, **kwargs)


class ExitConsistencyTests(Fixture, unittest.TestCase):
    def test_noncanonical_zero_can_be_delivered_and_accepted(self):
        self.registered()
        for raw in ('00\n', '-0\n', '00000\n', '-00000\n'):
            with self.subTest(raw=raw):
                self.finish(raw)
                if raw == '00\n':
                    event = self.call('poll')['events'][0]
                self.assertEqual(event['exit_code'], 0)
                result = self.ack('ack-delivered', event)
                self.assertIn(result['event_state'], ('delivered', 'accepted'))
                proof = self.root / 'proof'
                proof.write_text('verified')
                self.assertEqual(self.ack('ack-accepted', event, '--result', 'PASS',
                                         '--evidence', proof)['event_state'], 'accepted')

    def test_invalid_or_changed_exit_cannot_publish_ack(self):
        registry = pathlib.Path(self.registered()['registry'])
        self.finish(0)
        event = self.call('poll')['events'][0]
        before = registry.read_bytes()
        for raw in ('1', '-1', '32768', '-32769', '000000', '+0', '0.0', 'secret-invalid'):
            for action in ('ack-delivered', 'ack-accepted'):
                with self.subTest(raw=raw, action=action):
                    self.finish(raw)
                    self.ack(action, event, ok=False)
                    self.assertEqual(registry.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
