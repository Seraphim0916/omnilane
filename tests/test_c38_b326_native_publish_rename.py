import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    'native_b326', Path(__file__).resolve().parents[1] / 'scripts/lib/native.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class PublishRenameTests(unittest.TestCase):
    def exercise_publication(self, fail):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / '20261027-120000-1-1'
            plan = {'task': 'fixture task', 'quota_preflight': {'fixture': True}}
            metadata = {'mode': 'work'}
            locks = []
            real_fdopen = native.os.fdopen
            real_rename = native.os.rename
            failure = OSError('fixture rename failed')

            def track_stream(fd, mode, *args, **kwargs):
                stream = real_fdopen(fd, mode, *args, **kwargs)
                if mode == 'r+':
                    locks.append(stream)
                return stream

            def rename(source, target):
                self.assertEqual(target, job)
                self.assertFalse(job.exists())
                self.assertEqual({p.name for p in source.iterdir()},
                    {'native.lock', 'task.txt', 'meta.json', 'native.json',
                     'quota-preflight.json'})
                self.assertEqual((source / 'task.txt').read_text(), plan['task'])
                self.assertEqual(json.loads((source / 'native.json').read_text()), plan)
                self.assertEqual(json.loads((source / 'meta.json').read_text()), metadata)
                self.assertEqual(json.loads((source / 'quota-preflight.json').read_text()),
                                 plan['quota_preflight'])
                self.assertEqual(len(locks), 1)
                self.assertFalse(locks[0].closed)
                if fail:
                    raise failure
                return real_rename(source, target)

            with mock.patch.object(native.os, 'fdopen', side_effect=track_stream), \
                 mock.patch.object(native.os, 'rename', side_effect=rename) as renamed, \
                 mock.patch.object(native, 'cleanup_staging_dir',
                                   wraps=native.cleanup_staging_dir) as cleanup:
                if fail:
                    with self.assertRaises(OSError) as caught:
                        native.publish_job(root, job.name, plan, metadata)
                    self.assertIs(caught.exception, failure)
                else:
                    native.publish_job(root, job.name, plan, metadata)
                renamed.assert_called_once()
                cleanup.assert_called_once_with(renamed.call_args[0][0])
            self.assertTrue(locks[0].closed)
            self.assertEqual(list(root.glob('.native-stage-*')), [])
            self.assertEqual(job.exists(), not fail)
            if not fail:
                self.assertEqual(json.loads((job / 'native.json').read_text()), plan)
                self.assertEqual((job / 'task.txt').read_text(), plan['task'])

    def test_rename_failure_cleans_staging_and_closes_lock(self):
        self.exercise_publication(fail=True)

    def test_success_publishes_complete_job_and_closes_lock(self):
        self.exercise_publication(fail=False)


if __name__ == '__main__':
    unittest.main()
