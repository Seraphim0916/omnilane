"""Doctor must describe the watchdog dependency actually used by dispatch."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class WatchdogDoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='omnilane-watchdog-doctor-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / 'tools', os.environ['PATH'])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))

    def watchdog(self):
        result = subprocess.run(['bash', str(ROOT / 'scripts/doctor.sh'), '--json'],
                                env=self.env, text=True, capture_output=True, timeout=15)
        report = json.loads(result.stdout)
        return result, next(row for row in report['checks'] if row['check'] == 'watchdog')

    def test_available_python_supervisor_is_reported(self):
        _, check = self.watchdog()
        self.assertEqual('PASS', check['level'])
        self.assertIn('owned-process supervisor', check['message'])

    def test_missing_python_does_not_pass_because_timeout_or_perl_exists(self):
        for name in ('python', 'python3'):
            (self.root / 'tools/utilities' / name).unlink()
        result, check = self.watchdog()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual('FAIL', check['level'])
        self.assertIn('Python 3.9+', check['message'])

    def test_missing_engine_is_reported(self):
        fixture = self.root / 'partial-checkout'
        fixture.mkdir()
        self.env['OMNILANE_DOCTOR_REPO'] = str(fixture)
        _, check = self.watchdog()
        self.assertEqual('FAIL', check['level'])
        self.assertIn('supervisor is missing', check['message'])
