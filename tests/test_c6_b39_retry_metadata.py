import offline_env  # Activate suite isolation for direct file execution.
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / 'scripts/lib'
sys.path.insert(0, str(LIB))
import aa_retry
import aa_policy


class RetryMetadataTests(unittest.TestCase):
    def metadata(self):
        return dict(lane='test', vendor='codex', model='model', effort='high',
                    timeout=10, job_timeout=None, mode='work', workdir='/fixture')

    def test_every_required_field(self):
        for key in self.metadata():
            value = self.metadata()
            del value[key]
            with self.subTest(key=key), patch.object(aa_policy, '_read_json', return_value=(value, '')):
                with self.assertRaisesRegex(aa_policy.PolicyError, 'metadata.*' + key):
                    aa_retry.metadata_fields('/unused')

    def test_explicit_null_keeps_existing_behavior(self):
        with patch.object(aa_policy, '_read_json', return_value=(self.metadata(), '')):
            self.assertEqual(aa_retry.metadata_fields('/unused'),
                             ['test', 'codex', 'model', 'high', '10', 'null', 'work', '/fixture'])

    def test_cli_diagnostic_and_valid_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'meta.json'
            value = self.metadata()
            for missing in (None, *value.keys()):
                case = dict(value)
                if missing:
                    del case[missing]
                path.write_text(json.dumps(case))
                result = subprocess.run([sys.executable, '-B', str(LIB / 'aa_retry.py'),
                                         '--metadata', directory], capture_output=True, text=True)
                with self.subTest(missing=missing):
                    self.assertEqual(result.returncode, 3 if missing else 0)
                    if missing:
                        self.assertIn('metadata', result.stderr)
                        self.assertIn(missing, result.stderr)
                        self.assertNotIn('current caller is missing', result.stderr)
                        self.assertEqual(result.stdout, '')
                    else:
                        self.assertEqual(result.stdout.splitlines()[5], 'null')


if __name__ == '__main__':
    unittest.main()
