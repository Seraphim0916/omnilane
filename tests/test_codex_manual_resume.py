"""Manual continuation decisions and the public CLI, without provider access."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import select
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import offline_env  # noqa: F401 - bootstrap before any production code loads
import test_codex_resume as fixture
from test_codex_resume import ROOT, codex_resume, error_events
import job_marker
import aa_retry


class ManualDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='manual-resume-')
        self.addCleanup(temporary.cleanup)
        self.job = Path(temporary.name)
        self.sid = '11111111-2222-4333-8444-555555555555'
        (self.job / 'meta.json').write_text(json.dumps(dict(
            lane='fixture', vendor='codex', mode='work', session_mode='single-shot',
            model='synthetic-model', effort='high', workdir=str(self.job), timeout=15, job_timeout=None)))
        codex_resume.start(self.job, 1)
        self.events = [{'type': 'thread.started', 'thread_id': self.sid}] + error_events()
        (self.job / 'out.txt.progress.log').write_text(''.join(json.dumps(e) + '\n' for e in self.events))
        codex_resume.end(self.job, 1)
        (self.job / 'exit').write_text('75\n')

    def command(self, args, **kwargs):
        if args[0] == 'ps':
            return subprocess.CompletedProcess(args, 0, str(os.getpid()) + '\n', '')
        return subprocess.CompletedProcess(args, 0, 'Usage: codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]\n', '')

    def prepare(self, matches=()):
        with mock.patch.object(codex_resume.subprocess, 'run', side_effect=self.command), \
                mock.patch.object(job_marker, 'live_matches', return_value=list(matches)) as scanner:
            result = codex_resume.manual_session(self.job)
            scanner.assert_called_once()
            return result

    def test_original_session_selected_without_process_stop_proof(self):
        self.assertEqual(self.prepare(), self.sid)
        self.assertFalse((self.job / 'attempt-1-process.json').exists())
        state = codex_resume.read_json(self.job / codex_resume.STATE_NAME)
        self.assertNotIn(self.sid, json.dumps(codex_resume.public_fields(state)))
        self.assertFalse(codex_resume.public_fields(state)['auto_resume_eligible'])

    def test_live_job_marker_refuses_with_only_pid_and_start(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream), self.assertRaises(ValueError):
            self.prepare([(123, 456), (124, 457)])
        self.assertEqual(stream.getvalue().splitlines(), [
            'omnilane: job marker still alive: PID=123 START_US=456',
            'omnilane: job marker still alive: PID=124 START_US=457'])

    def test_process_inspection_denial_does_not_touch_lower_level_api(self):
        with mock.patch.object(codex_resume.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'denied')), \
                mock.patch.object(job_marker, 'live_matches') as scanner:
            with self.assertRaises(job_marker.Unavailable):
                codex_resume.manual_session(self.job)
            scanner.assert_not_called()

    def test_changed_binding_refuses(self):
        metadata = codex_resume.read_json(self.job / 'meta.json')
        metadata['workdir'] = '/different-synthetic-job'
        codex_resume.atomic_json(self.job / 'meta.json', metadata)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_replaced_progress_refuses(self):
        with (self.job / 'out.txt.progress.log').open('a') as handle:
            handle.write('{}\n')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_changed_saved_session_and_new_completion_refuse(self):
        state = codex_resume.read_json(self.job / codex_resume.STATE_NAME)
        state['attempts'][0]['session_id'] = '22222222-2222-4333-8444-555555555555'
        codex_resume.save(self.job, state)
        with self.assertRaises(ValueError):
            self.prepare()
        state['attempts'][0]['session_id'] = self.sid
        codex_resume.save(self.job, state)
        (self.job / 'out.txt').write_text('late final output')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_stale_progress_refuses(self):
        state = codex_resume.read_json(self.job / codex_resume.STATE_NAME)
        state['started_ns'] = state['attempts'][0]['progress_identity']['mtime_ns'] + 1
        codex_resume.save(self.job, state)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_wrong_terminal_exit_refuses(self):
        (self.job / 'exit').write_text('143\n')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_completed_or_ephemeral_turn_cannot_be_continued(self):
        for key, value in [('persistent_session', False), ('completion_observed', True)]:
            state = codex_resume.read_json(self.job / codex_resume.STATE_NAME)
            if key == 'persistent_session':
                state[key] = value
            else:
                state['persistent_session'] = True
                state['attempts'][0][key] = value
            codex_resume.save(self.job, state)
            with self.assertRaises(ValueError):
                self.prepare()

    def test_unsupported_resume_refuses_without_launch(self):
        with mock.patch.object(codex_resume.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, str(os.getpid()), ''),
                subprocess.CompletedProcess([], 2, '', '')]), \
                mock.patch.object(job_marker, 'live_matches', return_value=[]):
            with self.assertRaises(ValueError):
                codex_resume.manual_session(self.job)

    def test_ambiguous_invalid_and_quoted_session_ids_are_rejected(self):
        for events in ([self.events[0]] * 2 + error_events(),
                       [{'type': 'thread.started', 'thread_id': 'some-other-thread'}] + error_events(),
                       [{'type': 'item.completed', 'item': {'text': self.sid}}] + error_events()):
            (self.job / 'out.txt.progress.log').write_text(''.join(json.dumps(e) + '\n' for e in events))
            evidence = codex_resume.progress_evidence(self.job / 'out.txt.progress.log', 1)
            self.assertIsNone(evidence['session_id'])

    def test_sysops_metadata_allowed_only_for_explicit_continuation(self):
        metadata = codex_resume.read_json(self.job / 'meta.json')
        metadata['mode'] = 'sysops'
        codex_resume.atomic_json(self.job / 'meta.json', metadata)
        with self.assertRaises(aa_retry.aa_policy.PolicyError):
            aa_retry.metadata_fields(self.job)
        self.assertEqual(aa_retry.metadata_fields(self.job, continuation=True)[6], 'sysops')


class ManualMarkerTests(unittest.TestCase):
    def test_unrelated_churn_and_unreadable_processes_do_not_refuse(self):
        token = 'a' * 32
        wanted = ('OMNILANE_JOB_TOKEN=' + token).encode()
        pids = mock.Mock(return_value=[101, 102, 103, 104, 105])
        def info(pid):
            return None if pid == 102 else (1, pid * 100)
        def environment(pid):
            if pid == 103:
                raise PermissionError('denied')
            return {101: [b'UNRELATED=1'], 104: [wanted + b'9'], 105: [wanted]}.get(pid)
        self.assertEqual(job_marker.live_matches(token, source=(pids, info, environment)), [(105, 10500)])
        pids.assert_called_once()

    def test_only_matching_identity_is_rechecked(self):
        token = 'a' * 32
        info = mock.Mock(side_effect=[(1, 100), (1, 200)])
        self.assertEqual(job_marker.live_matches(token, source=(lambda: [101], info,
            lambda pid: [('OMNILANE_JOB_TOKEN=' + token).encode()])), [])


class ManualPublicCliTests(unittest.TestCase):
    def fixture(self):
        case = fixture.ResumeCliTests('test_normal_failure_never_resumes')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.require_process_inspection()
        return case

    def continue_job(self, mode, next_step):
        case = self.fixture()
        first = case.dispatch([{'events': error_events(), 'exit': 1, 'effect': True}, next_step], mode=mode)
        self.assertEqual(first.returncode, 75)
        original = case.job
        result = case.cli('jobs', 'resume', original.name, '--caller-context', str(case.caller))
        calls = [json.loads(line) for line in (case.root / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 2, result.stderr)
        self.assertEqual(calls[1]['argv'][:2], ['exec', 'resume'])
        self.assertIn('11111111-2222-4333-8444-555555555555', calls[1]['argv'])
        self.assertIn(codex_resume.CONTINUE_PROMPT.strip(), calls[1]['prompt'])
        self.assertNotIn('SYNTHETIC task canary', calls[1]['prompt'])
        self.assertEqual((case.workdir / 'append-only-ledger.txt').read_text().splitlines(), ['non-idempotent effect'])
        self.assertEqual((original / 'exit').read_text().strip(), '75')
        jobs = [p for p in (case.home / 'jobs').iterdir() if p != original]
        self.assertEqual(len(jobs), 1)
        status = json.loads(case.cli('jobs', 'status', jobs[0].name, '--json').stdout)['job']
        return case, result, status

    def test_work_manual_success_keeps_original_session_and_authority(self):
        case, result, status = self.continue_job('work', {'output': 'continued\n', 'effect': True})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(status['reason_class'])

    def test_sysops_manual_success(self):
        _, result, status = self.continue_job('sysops', {'output': 'continued\n'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(status['reason_class'])

    def test_manual_task_failure_preserves_exit_and_clears_transport_reason(self):
        _, result, status = self.continue_job('work', {'events': [{'type': 'error', 'message': 'task failed'}], 'exit': 7})
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIsNone(status['reason_class'])

    def test_manual_second_cut_still_does_not_auto_restart(self):
        _, result, status = self.continue_job('work', {'events': error_events(), 'exit': 1})
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(status['transport_reason'], 'upstream_eof')

    def test_live_marker_blocks_public_command(self):
        case = self.fixture()
        case.dispatch([{'events': error_events(), 'exit': 1}], mode='work')
        state = codex_resume.read_json(case.job / codex_resume.STATE_NAME)
        # System sleep may hide its environment on macOS. Keep a marker-readable
        # stand-in alive until this test stops it, rather than relying on a timer.
        child = subprocess.Popen(
            [sys.executable, '-c', "import sys; print('ready', flush=True); sys.stdin.read()"],
            env=dict(case.env, OMNILANE_JOB_TOKEN=state['process_marker']),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'stand-in did not start')
            self.assertEqual(child.stdout.readline(), b'ready\n')
            matches = job_marker.live_matches(state['process_marker'])
            self.assertIn(child.pid, [pid for pid, _ in matches], 'stand-in has no readable job marker')
            result = case.cli('jobs', 'resume', case.job.name, '--caller-context', str(case.caller))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('PID=' + str(child.pid) + ' START_US=', result.stderr)
            self.assertEqual(len((case.root / 'calls.jsonl').read_text().splitlines()), 1)
            self.assertEqual(len(list((case.home / 'jobs').iterdir())), 1)
            self.assertIsNone(child.poll(), 'stand-in exited before the refusal check')
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()


class ManualShellHandoffTests(unittest.TestCase):
    """Only the shell handoff/AA path; no scanner, dispatcher or provider runs."""
    def test_resume_handoff_uses_continuation_and_narrowed_authority(self):
        self.handoff()

    def test_background_resume_handoff_passes_real_parser(self):
        self.handoff(background=True)

    def handoff(self, background=False):
        case = fixture.ResumeCliTests('test_normal_failure_never_resumes')
        case.setUp()
        self.addCleanup(case.doCleanups)
        repo = case.root / 'shell-only'
        lib = repo / 'scripts/lib'
        lib.mkdir(parents=True)
        for name in ('common.sh', 'grok-work.sh', 'live-protocol.sh'):
            shutil.copy2(ROOT / 'scripts/lib' / name, lib / name)
        (lib / 'aa_retry.py').symlink_to(ROOT / 'scripts/lib/aa_retry.py')
        # This substitute returns test data only. It never invokes the denied
        # process API, and the receiving dispatcher only records arguments.
        (lib / 'codex_resume.py').write_text(
            "import sys\n"
            "print('11111111-2222-4333-8444-555555555555' if sys.argv[1] == 'manual-session' else "
            + repr(codex_resume.CONTINUE_PROMPT.strip()) + ")\n")
        shutil.copy2(ROOT / 'scripts/jobs.sh', repo / 'scripts/jobs.sh')
        (repo / 'scripts/dispatch.sh').write_text(
            'exec python3 -c \'import json,os,sys; print(json.dumps(dict(args=sys.argv[1:], '
            'mode=os.environ.get("OMNILANE_THREAD_MODE"), sid=os.environ.get("OMNILANE_THREAD_ID"), '
            'prompt=sys.stdin.read(), human=os.environ.get("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN"))))\' "$@"\n')
        job = case.home / 'jobs/20261111-120000-12345-12345'
        job.mkdir(parents=True)
        (job / 'meta.json').write_text(json.dumps(dict(lane='fixture', vendor='codex', model=case.model,
            effort=case.effort, mode='sysops', workdir=str(case.workdir), timeout=15, job_timeout=30)))
        (job / 'exit').write_text('75\n')
        (job / 'task.txt').write_text('ORIGINAL TASK MUST NOT BE REPLAYED')
        for original, name in [(case.root / 'policy.json', 'aa-registry.json'),
                               (case.caller, 'aa-authorizer.json'),
                               (case.root / 'overlay.json', 'aa-transport-overlay.json')]:
            shutil.copy2(original, job / name)
        digest = lambda name: hashlib.sha256((job / name).read_bytes()).hexdigest()
        (job / 'aa-lineage.json').write_text(json.dumps(dict(schema_version=1,
            registry_sha256=digest('aa-registry.json'), authorizer_sha256=digest('aa-authorizer.json'),
            transport_overlay_sha256=digest('aa-transport-overlay.json'))))
        result = subprocess.run(['bash', str(repo / 'scripts/jobs.sh'), 'resume', job.name,
            '--caller-context', str(case.caller), *(['--background'] if background else [])],
            env=case.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        handoff = json.loads(result.stdout)
        self.assertEqual(handoff['mode'], 'resume')
        self.assertEqual(handoff['sid'], '11111111-2222-4333-8444-555555555555')
        self.assertEqual(handoff['prompt'], codex_resume.CONTINUE_PROMPT)
        self.assertNotIn('ORIGINAL TASK', result.stdout)
        self.assertIsNone(handoff['human'])
        args = handoff['args']
        for flag, value in [('--mode', 'sysops'), ('--vendor', 'codex'), ('--model', case.model),
                            ('--effort', case.effort), ('--timeout', '15'), ('--job-timeout', '30'),
                            ('--executor', 'cli')]:
            self.assertEqual(args[args.index(flag) + 1], value)
        # The real foreground parser rejects --single-shot without --thread;
        # its default is already single-shot. Background continuation needs it.
        self.assertEqual('--single-shot' in args, background)
        context = Path(args[args.index('--caller-context') + 1])
        self.assertEqual(context.parent, job)
        self.assertLessEqual(json.loads(context.read_text())['inherited_ceiling'],
                             json.loads(case.caller.read_text())['inherited_ceiling'])
        dry_run = subprocess.run(['bash', str(ROOT / 'scripts/dispatch.sh'), '--dry-run', *args],
            input=handoff['prompt'], env=dict(case.env, OMNILANE_THREAD_MODE=handoff['mode'],
                                            OMNILANE_THREAD_ID=handoff['sid']),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)


if __name__ == '__main__':
    unittest.main()
