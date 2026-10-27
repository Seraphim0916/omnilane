import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py'


class WakeupSchemaTests(unittest.TestCase):
    def test_registry_version_through_cli(self):
        for label, value in [('integer', 1), ('boolean', True), ('float', 1.0),
                             ('string', '1'), ('missing', None)]:
            with self.subTest(version=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                home = root / 'home'
                job = home / 'jobs/job-1'
                job.mkdir(parents=True)
                (job / 'meta.json').write_text(json.dumps({
                    'lane': 'triage', 'vendor': 'codex', 'model': 'test',
                    'mode': 'advise', 'workdir': str(root),
                    'foreman_session': '', 'started': '2026-09-07T00:00:00Z'}))
                binding = ['--home', str(home), '--thread-id', 'controller-1',
                           '--host-id', 'local', '--run-id', 'run-1']
                prepared = subprocess.run([sys.executable, str(SCRIPT), 'prepare',
                    *binding, '--workdir', str(root), '--job', 'job-1'],
                    capture_output=True, text=True)
                self.assertEqual(prepared.returncode, 0, prepared.stderr)
                registry = Path(json.loads(prepared.stdout)['registry'])
                state = json.loads(registry.read_text())
                if label == 'missing':
                    del state['schema_version']
                else:
                    state['schema_version'] = value
                registry.write_text(json.dumps(state))
                before = registry.read_bytes()
                result = subprocess.run([sys.executable, str(SCRIPT), 'poll', *binding],
                                        capture_output=True, text=True)
                if label == 'integer':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['status'], 'pending')
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('unsupported registry schema', result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(registry.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
