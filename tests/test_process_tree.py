"""Pure ownership bookkeeping tests; no signals are sent by these fixtures."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('process_tree_fixture', ROOT / 'scripts/lib/process_tree.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def row(pid, parent, group, started='old', state='S'):
    return dict(pid=pid, ppid=parent, pgid=group, started=started, state=state, command='fixture')


class ProcessOwnershipTests(unittest.TestCase):
    def make_supervisor(self):
        owner = engine.Supervisor('call', 5, ['unused'])
        owner.keeper_pid = 400001
        owner.conservative = True
        return owner

    def test_reused_pid_new_lifetime_in_pinned_group_is_not_lost(self):
        owner = self.make_supervisor()
        owner.known[400002] = row(400002, 400001, 400001)
        current = row(400002, 1, 400001, 'new')
        with patch.object(engine, 'snapshot', return_value={400002: current}):
            self.assertEqual([current], owner.observe())
        self.assertEqual('new', owner.known[400002]['started'])

    def test_reused_detached_pid_is_not_authorized_by_previous_lifetime(self):
        owner = self.make_supervisor()
        owner.known[400002] = row(400002, 400001, 499999)
        current = row(400002, 1, 499999, 'new')
        with patch.object(engine, 'snapshot', return_value={400002: current}):
            self.assertEqual([], owner.observe())
        self.assertEqual('old', owner.known[400002]['started'])

    def test_unobserved_reparented_same_group_member_is_owned(self):
        owner = self.make_supervisor()
        current = row(400002, 1, 400001)
        with patch.object(engine, 'snapshot', return_value={400002: current}):
            self.assertEqual([current], owner.observe())

    def test_zombies_do_not_count_as_live_survivors(self):
        owner = self.make_supervisor()
        current = row(400002, 1, 400001, state='Z')
        with patch.object(engine, 'snapshot', return_value={400002: current}):
            self.assertEqual([], owner.observe())

    def test_conservative_escape_never_receives_numeric_or_handle_signal(self):
        owner = self.make_supervisor()
        escape = row(400002, 1, 499999)
        with patch.object(engine.os, 'killpg') as group_kill, patch.object(engine.os, 'kill') as pid_kill:
            owner.signal_tree(engine.signal.SIGTERM, [escape])
        group_kill.assert_called_once_with(400001, engine.signal.SIGTERM)
        pid_kill.assert_not_called()

    def test_no_group_signal_after_keeper_reaped(self):
        owner = self.make_supervisor()
        owner.reaped = True
        with patch.object(engine.os, 'killpg') as group_kill:
            owner.signal_tree(engine.signal.SIGKILL, [])
        group_kill.assert_not_called()

    def test_command_name_never_keeps_path_or_control_characters(self):
        self.assertEqual('fixture?name', engine.clean_command('/private/bin/fixture\x1bname'))

    def test_unreadable_process_identity_is_not_silently_gone(self):
        import errno
        from unittest.mock import MagicMock
        process = MagicMock()
        process.name = '400002'
        process.__truediv__.return_value.read_text.side_effect = PermissionError(errno.EACCES, 'denied')
        with patch.object(engine.sys, 'platform', 'linux'), patch.object(engine.Path, 'iterdir', return_value=[process]):
            with self.assertRaisesRegex(OSError, 'identity unreadable'):
                engine.snapshot()

    def test_process_scan_has_total_deadline(self):
        from unittest.mock import MagicMock
        with patch.object(engine.sys, 'platform', 'linux'), patch.object(engine.Path, 'iterdir', return_value=[MagicMock()]), patch.object(engine.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(OSError, 'exceeded its deadline'):
                engine.snapshot()

    def test_whole_job_budget_rejects_more_than_nine_digits(self):
        with patch.object(engine.Supervisor, 'run') as run:
            self.assertEqual(2, engine.main(['job', '1000000000', 'unused']))
        run.assert_not_called()

    def test_metadata_object_shape_is_required(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            path.write_text('[]')
            with self.assertRaisesRegex(ValueError, 'must be an object'):
                engine.load_json(path)

    def test_setup_failure_cannot_launch_provider_or_leave_live_keeper(self):
        import json
        import subprocess
        import sys
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'provider-started'
            launcher = '''import os,runpy,sys
real_fork=os.fork
def fork():
    pid=real_fork()
    if pid: print(pid,flush=True)
    return pid
os.fork=fork
def fail(*args): raise OSError('injected setup failure')
os.set_blocking=fail
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
            result = subprocess.run([sys.executable, '-c', launcher,
                                     str(ROOT / 'scripts/lib/process_tree.py'), 'call', '5',
                                     sys.executable, '-c', 'from pathlib import Path;Path(%r).touch()' % str(marker)],
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(125, result.returncode, result.stderr)
            self.assertFalse(marker.exists())
            keeper = int(result.stdout.strip())
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(keeper)],
                                   text=True, capture_output=True, timeout=2)
            self.assertTrue(not state.stdout.strip() or state.stdout.lstrip().startswith('Z'))

    def test_keeper_failure_before_ready_is_bounded_without_deadline(self):
        import subprocess
        import sys
        launcher = '''import os,runpy,sys
def fail(): raise OSError('injected setsid failure')
os.setsid=fail
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        result = subprocess.run([sys.executable, '-c', launcher,
                                 str(ROOT / 'scripts/lib/process_tree.py'), 'job', '--no-deadline',
                                 sys.executable, '-c', 'raise SystemExit(0)'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(125, result.returncode, result.stderr)
        self.assertIn('keeper exited without command completion', result.stderr)

    def test_exit_and_finalization_readers_reject_noncanonical_metadata(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for raw in (b' 0\n', b'00\n', '٠\n'.encode(), b'0\n\n', b'256\n', b'0' * 100):
                with self.subTest(raw=raw):
                    (root / 'exit').write_bytes(raw)
                    (root / 'finalized').write_bytes(raw)
                    self.assertIsNone(engine.read_exit(root))
                    self.assertFalse(engine.finalized(root))
            for raw, expected in ((b'0', 0), (b'0\n', 0), (b'143\n', 143), (b'255', 255)):
                (root / 'exit').write_bytes(raw)
                (root / 'finalized').write_bytes(raw)
                self.assertEqual(expected, engine.read_exit(root))
                self.assertTrue(engine.finalized(root))

    def test_truthy_false_cleanup_is_not_successful_cancellation(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'process-state.json').write_text(json.dumps({'schema_version': 1, 'instance': 'fixture', 'processes': []}))
            (root / 'process-cleanup.json').write_text(json.dumps({'schema_version': 1, 'instance': 'fixture', 'complete': 'false', 'signals': [], 'survivors': []}))
            (root / 'exit').write_text('0\n')
            (root / 'finalized').write_text('0\n')
            with patch.object(engine.time, 'monotonic', side_effect=[0, 10]), patch.object(engine, 'snapshot', return_value={}):
                result = engine.cancel(root)
            self.assertIs(False, result['complete'])
            self.assertEqual(125, result['exit_code'])

    def test_enumeration_failure_preserves_unconfirmed_last_known_rows(self):
        owner = self.make_supervisor()
        known = row(400002, 400001, 400001)
        owner.known[400002] = known
        owner.control_fd = -1
        with patch.object(owner, 'observe', side_effect=OSError('enumeration unavailable')), patch.object(owner, 'read_status'), patch.object(engine.os, 'write'), patch.object(engine.os, 'killpg'), patch.object(engine.os, 'waitpid', return_value=(400001, 0)):
            result = owner.cleanup('cancelled')
        self.assertIs(False, result['complete'])
        self.assertTrue(result['residual_unknown'])
        self.assertEqual([dict(known, liveness_unconfirmed=True)], result['survivors'])

    def test_graceful_completion_waits_for_keeper_status_before_fingerprinting(self):
        import types
        from unittest.mock import Mock
        owner = self.make_supervisor()
        owner.report_dir = Path('/unused-owned-fixture')
        owner.control_fd = -1
        record = Mock()
        reads = []
        def delayed_status():
            reads.append(True)
            if len(reads) >= 2:
                owner.command_status = 0
                owner.command_completed_ns = 100
        with patch.object(owner, 'observe', return_value=[]), patch.object(owner, 'read_status', side_effect=delayed_status), patch.object(engine.os, 'write'), patch.object(engine.os, 'killpg'), patch.object(engine.os, 'waitpid', return_value=(400001, 0)), patch.dict('sys.modules', {'job_cancel': types.SimpleNamespace(record_completion=record)}):
            result = owner.cleanup('completed')
        self.assertTrue(result['complete'])
        record.assert_called_once_with(owner.report_dir, 0, completed_at_ns=100)

    def test_cleanup_readers_share_the_same_schema_predicate(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('cleanup_schema_fixture', ROOT / 'scripts/lib/job_cancel.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        base = dict(schema_version=1, instance='fixture', complete=True, survivors=[], signals=[])
        for change in ({}, {'residual_unknown': True}, {'residual_unknown': 'false'},
                       {'complete': 'false'}, {'schema_version': True}, {'survivors': ['bad']},
                       {'signals': [{'signal': 'SIGTERM', 'sent': True, 'args': 'private'}]}):
            with self.subTest(change=change):
                value = dict(base, **change)
                expected = helper.valid_cleanup_record(value)
                try:
                    engine.validate_cleanup(value)
                    actual = True
                except ValueError:
                    actual = False
                self.assertEqual(expected, actual)

    def test_malformed_inner_success_evidence_cannot_be_ignored(self):
        import json
        import subprocess
        import sys
        import tempfile
        for change in ({'schema_version': True}, {'complete': 'false'}, {'residual_unknown': True}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                value = dict(schema_version=1, complete=True, survivors=[], signals=[], instance='inner', **{})
                value.update(change)
                command = ('import json,os,pathlib; v=json.loads(os.environ["INNER_VALUE"]); '
                           'v["job_instance"]=os.environ["OMNILANE_PROCESS_JOB_INSTANCE"]; '
                           'pathlib.Path(os.environ["OMNILANE_PROCESS_JOB_DIR"],"call-cleanup.fixture.json").write_text(json.dumps(v))')
                environment = dict(os.environ, OMNILANE_PROCESS_JOB_DIR=directory, INNER_VALUE=json.dumps(value))
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/lib/process_tree.py'), 'job', '5',
                                         sys.executable, '-c', command], env=environment,
                                        text=True, capture_output=True, timeout=6)
                self.assertEqual(125, result.returncode, result.stderr)
                report = json.loads((Path(directory) / 'process-cleanup.json').read_text())
                self.assertIs(False, report['complete'])
                self.assertIn('invalid per-call cleanup evidence', report['errors'])
