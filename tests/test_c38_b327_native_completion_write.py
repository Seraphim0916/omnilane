import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    'native_b327', Path(__file__).resolve().parents[1] / 'scripts/lib/native.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class CompletionWriteTests(unittest.TestCase):
    def exercise_retry(self, outcome):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            job_id = '20261027-120000-1-1'
            job = home / 'jobs' / job_id
            job.mkdir(parents=True)
            (job / 'native.lock').touch()
            state = {'job_id': job_id, 'executor': 'native', 'state': 'pending',
                     'executor_reason': 'fixture', 'agent_id': None,
                     'vendor': 'codex', 'model': 'fixture', 'effort': 'high',
                     'harness': 'codex'}
            state_path = job / 'native.json'
            state_path.write_text(json.dumps(state))
            before = state_path.read_bytes()
            receipt = {'schema_version': 1, 'job_id': job_id, 'agent_id': 'worker-1',
                       'runtime': {'vendor': 'codex', 'model': 'fixture', 'effort': 'high',
                                   'harness': 'codex', 'backend': 'collaboration.spawn_agent'},
                       'outcome': outcome, 'result': 'fixture result',
                       'evidence': ['fixture verification']}
            receipt_path = home / 'completion.json'
            receipt_path.write_text(json.dumps(receipt))
            args = SimpleNamespace(home=str(home), job_id=job_id, action='complete-native',
                                   input=receipt_path, json=True)
            locks = []
            real_fdopen = native.os.fdopen
            real_write = native.atomic_json
            writes = []
            failure = OSError('fixture completion write failed')

            def track_stream(fd, mode, *positional, **kwargs):
                stream = real_fdopen(fd, mode, *positional, **kwargs)
                if mode == 'r+':
                    locks.append(stream)
                return stream

            def write(path, value):
                writes.append(value.copy())
                self.assertEqual(path, state_path)
                self.assertEqual(value['state'], 'completed')
                self.assertEqual(value['completion'], receipt)
                self.assertFalse(locks[-1].closed)
                if len(writes) == 1:
                    raise failure
                return real_write(path, value)

            with mock.patch.object(native.os, 'fdopen', side_effect=track_stream), \
                 mock.patch.object(native, 'atomic_json', side_effect=write), \
                 mock.patch.object(native, 'validate_completion',
                                   wraps=native.validate_completion) as validated:
                failed_output = io.StringIO()
                with contextlib.redirect_stdout(failed_output):
                    with self.assertRaises(OSError) as caught:
                        native.job_command(args)
                self.assertIs(caught.exception, failure)
                validated.assert_called_once()
                self.assertEqual(state_path.read_bytes(), before)
                self.assertEqual(json.loads(state_path.read_text())['state'], 'pending')
                self.assertEqual(failed_output.getvalue(), '')
                self.assertEqual(len(locks), 1)
                self.assertTrue(locks[0].closed)

                retry_output = io.StringIO()
                with contextlib.redirect_stdout(retry_output):
                    rc = native.job_command(args)
                self.assertEqual(rc, 0)
                self.assertEqual(validated.call_count, 2)
            self.assertEqual(len(writes), 2)
            self.assertEqual(len(locks), 2)
            self.assertTrue(all(lock.closed for lock in locks))
            stored = json.loads(state_path.read_text())
            self.assertEqual(stored['state'], 'completed')
            self.assertEqual(stored['completion'], receipt)
            self.assertEqual(stored['agent_id'], receipt['agent_id'])
            expected_exit = 0 if outcome == 'success' else 1
            self.assertEqual(stored['exit_code'], expected_exit)
            published = json.loads(retry_output.getvalue())
            self.assertIs(published['ok'], True)
            self.assertEqual(published['job']['native_state'], 'completed')
            self.assertEqual(published['job']['exit_code'], expected_exit)
            self.assertEqual(json.loads(receipt_path.read_text()), receipt)

    def test_success_receipt_retries_after_write_failure(self):
        self.exercise_retry('success')

    def test_failure_receipt_retries_after_write_failure(self):
        self.exercise_retry('failure')


if __name__ == '__main__':
    unittest.main()
