import offline_env  # Activate suite isolation for direct file execution.
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


SPEC = importlib.util.spec_from_file_location(
    'c41_b354_wakeup', Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py')
wakeup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wakeup)


class AutomationReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.evidence = Path(temporary.name).resolve() / 'receipt.json'
        self.args = SimpleNamespace(evidence=str(self.evidence), automation_id='auto-1',
                                    thread_id='thread-1', host_id='host-1')

    def receipt(self, status):
        return {'source': 'automation_update', 'automation_id': 'auto-1',
                'controller_thread_id': 'thread-1', 'controller_host_id': 'host-1',
                'status': status, 'effective_interval_seconds': 120,
                'extra': {'synthetic_private_payload': 'must not be projected'}}

    def write_receipt(self, receipt):
        self.evidence.write_text(json.dumps(receipt), encoding='utf-8')

    def test_each_binding_field_must_match(self):
        for expected in ('ACTIVE', 'PAUSED'):
            for field in ('source', 'automation_id', 'controller_thread_id',
                          'controller_host_id', 'status'):
                for missing in (False, True):
                    with self.subTest(expected=expected, field=field, missing=missing):
                        receipt = self.receipt(expected)
                        if missing:
                            del receipt[field]
                        else:
                            receipt[field] = 'wrong'
                        self.write_receipt(receipt)
                        with self.assertRaisesRegex(
                                wakeup.Invalid, 'automation receipt binding/status mismatch'):
                            wakeup.automation_receipt(self.args, expected)

    def test_valid_receipt_has_only_public_projection(self):
        for expected in ('ACTIVE', 'PAUSED'):
            with self.subTest(expected=expected):
                receipt = self.receipt(expected)
                self.write_receipt(receipt)
                fingerprint = hashlib.sha256(json.dumps(
                    receipt, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                self.assertEqual(wakeup.automation_receipt(self.args, expected), {
                    'path': str(self.evidence), 'sha256': fingerprint,
                    'effective_interval_seconds': 120,
                    'trust': 'caller-attested tool result; not independent scheduler verification'})


if __name__ == '__main__':
    unittest.main()
