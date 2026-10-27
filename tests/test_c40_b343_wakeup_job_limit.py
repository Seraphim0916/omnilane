import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py'


class WakeupJobLimitTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('wakeup_b343', SCRIPT)
        self.wakeup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.wakeup)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'registry.json'
        self.args = SimpleNamespace(
            action='prepare', home=str(self.root), workdir=str(self.root),
            thread_id='controller', host_id='local', run_id='run',
            job=[], ttl_seconds=600, interval_seconds=60, adoption_evidence=None)
        self.saved = None
        self.writer = self.patch('write_json', side_effect=self.save)
        self.patch('registry_lock', side_effect=lambda args: contextlib.nullcontext(self.path))
        self.patch('assert_unused_historical_run')
        self.patch('read_json', side_effect=lambda path: copy.deepcopy(self.saved))
        self.patch('identity', side_effect=lambda args, job, *a, **kw: 'identity-' + job)
        self.patch('time.time', return_value=100)
        self.patch('os.path.lexists', side_effect=lambda path: self.saved is not None)

    def patch(self, name, **kwargs):
        patcher = mock.patch.object(self.wakeup, name, **kwargs) if '.' not in name else mock.patch(
            'time.time' if name == 'time.time' else 'os.path.lexists', **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def save(self, path, state):
        self.assertEqual(path, self.path)
        self.saved = copy.deepcopy(state)

    def prepare(self, jobs):
        self.args.job = jobs
        return self.wakeup.act(self.args)

    def test_one_hundred_distinct_jobs_can_be_registered(self):
        jobs = ['job-%03d' % index for index in range(100)]
        result = self.prepare(jobs)
        self.assertEqual(result['tracked_jobs'], jobs)
        self.assertEqual(set(self.saved['jobs']), set(jobs))
        self.writer.assert_called_once()

    def test_one_hundred_and_one_jobs_never_publish_registry(self):
        with self.assertRaisesRegex(self.wakeup.Invalid, 'job limit exceeded'):
            self.prepare(['job-%03d' % index for index in range(101)])
        self.writer.assert_not_called()
        self.assertIsNone(self.saved)

    def test_duplicates_at_capacity_do_not_consume_slots(self):
        jobs = ['job-%03d' % index for index in range(100)]
        self.prepare(jobs)
        before = copy.deepcopy(self.saved)
        self.writer.reset_mock()
        result = self.prepare([jobs[0], jobs[0], jobs[-1]])
        self.assertEqual(result['tracked_jobs'], jobs)
        self.assertEqual(self.saved, before)
        self.writer.assert_called_once()
        self.writer.reset_mock()
        with self.assertRaisesRegex(self.wakeup.Invalid, 'job limit exceeded'):
            self.prepare(['job-100'])
        self.writer.assert_not_called()
        self.assertEqual(self.saved, before)


if __name__ == '__main__':
    unittest.main()
