"""Transport recovery on the real CLI, with synthetic events and no provider access."""
import json
import contextlib
import io
import hashlib
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

from offline_env import isolated_environment, wait_for_job_controller

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import codex_resume
import aa_policy
import build_overlay
from probe_fixture import write_probe


def error_events(reason="upstream_eof"):
    message = "Incomplete response returned, reason: " + reason
    return [{"type": "error", "message": message},
            {"type": "turn.failed", "error": {"message": message}}]


FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
if '--help' in sys.argv:
    if os.environ.get('FIXTURE_NO_RESUME_SUPPORT'):
        sys.exit(2)
    print('Usage: codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]')
    print('Resume a previous session by id; --json --model --output-last-message')
    sys.exit(0)
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
calls = root / 'calls.jsonl'
number = len(calls.read_text().splitlines()) + 1 if calls.exists() else 1
steps = json.loads(os.environ['FIXTURE_STEPS'])
step = steps[min(number - 1, len(steps) - 1)]
prompt = sys.stdin.read()
record = {'pid': os.getpid(), 'attempt': number, 'argv': sys.argv[1:], 'cwd': os.getcwd(), 'prompt': prompt,
          'caller': os.environ.get('OMNILANE_AA_CALLER_CONTEXT'),
          'authorizer': os.environ.get('OMNILANE_AA_AUTHORIZER_CONTEXT'),
          'registry_sha': os.environ.get('OMNILANE_AA_REGISTRY_SHA256'),
          'depth': os.environ.get('OMNILANE_DEPTH')}
record['locks'] = [(p.parent.name, p.read_text(), p.parent.stat().st_ino)
                   for p in (pathlib.Path(os.environ['OMNILANE_HOME']) / 'locks').glob('*/pid')]
with calls.open('a') as out:
    out.write(json.dumps(record) + '\n')
session_id = step.get('session_id', '11111111-2222-4333-8444-555555555555')
if session_id:
    print(json.dumps({'type': 'thread.started', 'thread_id': session_id}), flush=True)
if step.get('effect') and 'resume' not in sys.argv:
    with (pathlib.Path.cwd() / 'append-only-ledger.txt').open('a') as ledger:
        ledger.write('non-idempotent effect\n')
if step.get('joined_child'):
    child = os.fork()
    if child == 0:
        os._exit(0)
    waited, status = os.waitpid(child, 0)
    (root / 'joined-child.json').write_text(json.dumps({'pid': waited, 'status': status}))
if number == 1:
    (pathlib.Path.cwd() / 'partial-edit.txt').write_text('synthetic partial edit\n')
else:
    assert (pathlib.Path.cwd() / 'partial-edit.txt').read_text() == 'synthetic partial edit\n'
time.sleep(step.get('delay', 0))
if step.get('mutate_policy'):
    path = pathlib.Path(os.environ['OMNILANE_AA_POLICY_FILE'])
    path.write_text(path.read_text() + '\n')
for event in step.get('events', []):
    raw = isinstance(event, str) and event.startswith('RAW:')
    print(event[4:] if raw else json.dumps(event), flush=True)
time.sleep(step.get('delay_after', 0))
if step.get('output'):
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(step['output'])
sys.exit(step.get('exit', 0))
'''


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eof-classifier-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "progress.jsonl"

    def classify(self, events, rc=1):
        self.path.write_text("".join(json.dumps(event) + "\n" for event in events))
        return codex_resume.classify_progress(self.path, rc)

    def test_both_error_envelopes_and_both_proven_causes(self):
        for reason in ("upstream_eof", "upstream_error"):
            for events in (error_events(reason), error_events(reason)[:1], error_events(reason)[1:]):
                with self.subTest(reason=reason, events=events):
                    self.assertEqual(self.classify(events), reason)

    def test_later_error_and_success_override_transport(self):
        for last in ({"type": "error", "message": "permission denied"},
                     {"type": "turn.failed", "error": {"message": "quota exceeded"}},
                     {"type": "turn.failed", "error": {}}, {"type": "turn.completed"}):
            self.assertIsNone(self.classify(error_events() + [last]))

    def test_does_not_classify_task_text_or_unproven_shapes(self):
        for events in ([{"type": "item.completed", "item": {"text": error_events()[0]['message']}}],
                       error_events("max_output_tokens"), error_events("unknown"),
                       [{"type": "error", "message": "stream disconnected before completion: permission denied"}],
                       [{"type": "error", "message": error_events()[0]['message'] + " token=private"}]):
            self.assertIsNone(self.classify(events))

    def test_success_refusal_watchdogs_and_signals_are_never_transport(self):
        for rc in (0, 2, 124, 125, 126, 127, 129, 130, 137, 142, 143):
            self.assertIsNone(self.classify(error_events(), rc), rc)

    def test_malformed_or_missing_input_fails_closed(self):
        self.assertIsNone(codex_resume.classify_progress(self.path, 1))
        for content in ('not json\n', '[]\n', '{}\n', '{"type":"error","message":[]}',
                        json.dumps(error_events()[0]) + '\n{"type":"turn.failed"'):
            self.path.write_text(content)
            self.assertIsNone(codex_resume.classify_progress(self.path, 1))

    def test_large_earlier_item_does_not_hide_the_final_error(self):
        events = [{'type': 'item.completed', 'item': {'text': 'x' * (1024 * 1024)}}]
        self.assertEqual(self.classify(events + error_events()), 'upstream_eof')
        self.assertIsNone(self.classify(error_events() + events))

    def test_agent_message_is_provisional_across_tool_and_new_turn_activity(self):
        message = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Checking'}}
        activities = [[], [{'type': 'turn.started'}]]
        activities += [[{'type': kind, 'item': {'type': tool}}]
                       for kind in ('item.started', 'item.updated', 'item.completed')
                       for tool in ('command_execution', 'file_change', 'mcp_tool_call')]
        for activity in activities:
            with self.subTest(activity=activity):
                self.assertEqual(self.classify([message] + activity + error_events()), 'upstream_eof')
                observed = codex_resume.progress_evidence(self.path, 1)
                self.assertFalse(observed['completion_observed'])
                self.assertTrue(observed['continuation_eligible'])

    def test_notice_uses_only_validated_fields_and_preserves_retry_id(self):
        job = "20261111-120000-12345-12345"
        line = codex_resume.human_line(job, "upstream_eof", 12)
        self.assertEqual(line, f"omnilane: job {job} was cut in transit "
                         f"(upstream_eof) after 12 s; interrupted, needs decision; the task itself did not fail; nothing was restarted automatically because automatic continuation is disabled. Running resume is the operator's statement that the earlier writer has stopped and continuing after its partial effects is safe. Next: omnilane jobs resume {job}")
        self.assertEqual(len(line.splitlines()), 1)
        for value in ("person@example.test", "/private/project", "token=private", "\x1b[31mBAD"):
            self.assertNotIn(value, codex_resume.human_line(value, value, value))

    def test_completed_turn_evidence_expires_on_later_activity(self):
        done = {'type': 'turn.completed'}
        activities = [{'type': 'turn.started'}]
        activities += [{'type': kind, 'item': {'type': item_type}}
                       for kind in ('item.started', 'item.updated', 'item.completed')
                       for item_type in ('command_execution', 'file_change', 'mcp_tool_call',
                                         'reasoning', 'agent_message')]
        # Missing item metadata or unfamiliar item events cannot be assigned to
        # the already completed turn, so completion must remain unproven.
        activities += [{'type': 'item.started'}, {'type': 'item.unknown'}]
        for activity in activities:
            with self.subTest(activity=activity):
                self.assertEqual(self.classify([done, activity] + error_events()), 'upstream_eof')
                observed = codex_resume.progress_evidence(self.path, 1)
                self.assertFalse(observed['completion_observed'])
                self.assertTrue(observed['continuation_eligible'])

    def assert_interrupted(self, events, eligible=True):
        self.assertEqual(self.classify(events), 'upstream_eof')
        observed = codex_resume.progress_evidence(self.path, 1)
        self.assertFalse(observed['completion_observed'])
        self.assertEqual(observed['continuation_eligible'], eligible)

    def assert_completed(self, events, rc=1):
        self.classify(events, rc)
        observed = codex_resume.progress_evidence(self.path, rc)
        self.assertTrue(observed['completion_observed'])
        self.assertFalse(observed['continuation_eligible'])

    def test_unbalanced_turns_do_not_prove_completion(self):
        # P3-1: a late turn.completed after a newer turn.started, or a turn
        # that never ended, leaves a turn open; that is not completion.
        started, done = {'type': 'turn.started'}, {'type': 'turn.completed'}
        tool = {'type': 'item.started', 'item': {'type': 'command_execution'}}
        for events in ([started, tool, started, done],
                       [started, started, done],
                       [done, started, started, done],
                       [started, tool, done, started, started, done]):
            with self.subTest(events=events):
                self.assert_interrupted(events + error_events())

    def test_balanced_and_failed_turns_still_complete(self):
        started, done = {'type': 'turn.started'}, {'type': 'turn.completed'}
        failed = {'type': 'turn.failed', 'error': {'message': 'quota exceeded'}}
        tool = {'type': 'item.started', 'item': {'type': 'command_execution'}}
        for events in ([started, tool, done],
                       [started, done, started, tool, done],
                       [started, failed, started, done],
                       [done], [started, tool, done, done],
                       [done, started, tool, done, done],
                       [started, tool, done, {'type': 'thread.started', 'thread_id': 'x'}],
                       [started, tool, done, {'type': 'unknown.event'}]):
            with self.subTest(events=events):
                self.assert_completed(events + error_events())

    def test_unreadable_line_after_completion_revokes_it(self):
        # P3-2: an unparseable or oversized line may be the next turn's work.
        done = {'type': 'turn.completed'}
        self.path.write_text(json.dumps(done) + '\n{"type":"item.started","item"\n'
                             + ''.join(json.dumps(e) + '\n' for e in error_events()))
        self.assertEqual(codex_resume.classify_progress(self.path, 1), 'upstream_eof')
        observed = codex_resume.progress_evidence(self.path, 1)
        self.assertFalse(observed['completion_observed'])
        self.assertFalse(observed['continuation_eligible'])
        large = {'type': 'item.completed', 'item': {'text': 'x' * (1024 * 1024)}}
        self.assert_interrupted([done, large] + error_events(), eligible=False)

    def test_final_reply_still_completes_after_unreadable_line(self):
        done = {'type': 'turn.completed'}
        output = self.path.with_name('final.txt')
        output.write_text('answer')
        for middle in ('{"type":"item.started","item"\n',
                       json.dumps({'type': 'item.completed', 'item': {'text': 'x' * (1024 * 1024)}}) + '\n'):
            with self.subTest(middle=middle[:30]):
                self.path.write_text(json.dumps(done) + '\n' + middle
                                     + ''.join(json.dumps(e) + '\n' for e in error_events()))
                observed = codex_resume.progress_evidence(self.path, 1, output)
                self.assertTrue(observed['completion_observed'])
                self.assertFalse(observed['continuation_eligible'])

    def test_reverse_cases_from_review5_section3(self):
        done, started = {'type': 'turn.completed'}, {'type': 'turn.started'}
        # Clean exit 0 without turn.completed is never transport.
        self.assertIsNone(self.classify([started] + error_events(), rc=0))
        # Completed, then a non-transport error keeps the provider exit.
        self.assertIsNone(self.classify([started, done, {'type': 'error', 'message': 'permission denied'}]))
        # Completed, then a non-turn, non-item event: still completed.
        self.assert_completed([started, done, {'type': 'thread.started', 'thread_id': 'x'}] + error_events())

    def test_latest_turn_can_complete_before_transport_error(self):
        events = [{'type': 'turn.completed'}, {'type': 'turn.started'},
                  {'type': 'item.started', 'item': {'type': 'command_execution'}},
                  {'type': 'turn.completed'}] + error_events()
        self.assertEqual(self.classify(events), 'upstream_eof')
        observed = codex_resume.progress_evidence(self.path, 1)
        self.assertTrue(observed['completion_observed'])
        self.assertFalse(observed['continuation_eligible'])


class ResumeCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eof-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "state"
        self.home.mkdir()
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.bins = self.root / "bin"
        self.bins.mkdir()
        fake = self.bins / "codex"
        fake.write_text(FAKE)
        fake.chmod(0o700)
        # All authorization evidence below is fixture-only, never host policy.
        registry = json.loads((ROOT / "config/aa-model-policy.json").read_text())
        row = next(row for row in registry['scored_configs'] if row['id'] == 'codex/gpt-6-1-sol-high')
        policy = self.root / "policy.json"
        policy.write_bytes((ROOT / "config/aa-model-policy.json").read_bytes())
        selector, model, name = build_overlay.PROVEN[row['id']]
        files = write_probe(self.root, 'codex', dict(name=name, model=model, effort=row['effort']))
        manifest = self.root / 'probe-manifest.json'
        manifest.write_text(json.dumps({'probe_runs': {row['id']: files}}))
        overlay = self.root / 'overlay.json'
        overlay.write_text(json.dumps(dict(schema_version=1, snapshot_id=registry['snapshot']['id'],
            host=socket.gethostname(), evidence=[
                dict(path=str(fake), sha256=hashlib.sha256(fake.read_bytes()).hexdigest(), vendor='codex'),
                dict(path=str(manifest), sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())],
            mappings=[dict(config_id=row['id'], identity=aa_policy._row_identity(row),
                           runtime_model=model, runtime_effort=row['effort'], selector_type=selector,
                           verification='request-selector-contract')])))
        self.caller = self.root / "caller.json"
        self.caller.write_text(json.dumps({'schema_version': 1, 'snapshot_id': registry['snapshot']['id'],
            'kind': 'model', 'caller': {key: row[key] for key in ('vendor', 'model', 'effort', 'reasoning', 'fallback')},
            'inherited_ceiling': row['score']}))
        (self.home / "routing.local.yaml").write_text(
            f"fixture: codex {row['model']} {row['effort']}\n")
        self.env.update(OMNILANE_HOME=str(self.home), CODEX_BIN=str(fake), FIXTURE_ROOT=str(self.root),
                        PATH=str(self.bins) + os.pathsep + self.env['PATH'],
                        OMNILANE_AA_POLICY_FILE=str(policy), OMNILANE_AA_CALLER_FROM_PROCESS="0",
                        OMNILANE_AA_TRANSPORT_OVERLAY=str(overlay),
                        OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="0")
        self.model, self.effort = row['model'], row['effort']

    def cli(self, *args, timeout=30):
        return subprocess.run(['bash', str(ROOT / 'bin/omnilane'), *args],
                              env=self.env, text=True, capture_output=True, timeout=timeout)

    def dispatch(self, steps, *flags, mode='advise'):
        self.require_process_inspection()
        self.env['FIXTURE_STEPS'] = json.dumps(steps)
        result = self.cli('dispatch', '--mode', mode, '--workdir', str(self.workdir),
                          '--executor', 'cli', '--caller-context', str(self.caller),
                          '--timeout', '15', *flags, 'fixture', 'SYNTHETIC task canary, do not display')
        if 'ps cannot read process start times here, so no job was started' in result.stderr:
            self.skipTest('public dispatch refused: ps cannot read process start times in this sandbox')
        jobs = list((self.home / 'jobs').glob('*')) if (self.home / 'jobs').exists() else []
        self.assertEqual(len(jobs), 1, result.stdout + result.stderr)
        self.job = jobs[0]
        self.calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        self.meta = json.loads((self.job / 'meta.json').read_text())
        self.receipt = json.loads((self.home / 'inbox' / (self.job.name + '.json')).read_text())
        status = self.cli('jobs', 'status', self.job.name, '--json')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.status = json.loads(status.stdout)['job']
        self.assertEqual(int((self.job / 'exit').read_text()), result.returncode)
        self.assertEqual(self.receipt['exit'], result.returncode)
        return result


    def require_process_inspection(self):
        try:
            result = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'pid='],
                                    capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest('requires process inspection; ps unavailable on this host')
        if result.returncode or result.stdout.strip() != str(os.getpid()):
            self.skipTest('requires process inspection; ps denied on this host')



    def test_work_cut_after_entire_first_tree_exits_never_restarts(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1, 'joined_child': True},
                                {'output': 'must not run\n'}], mode='work')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(len(self.calls), 1)
        joined = json.loads((self.root / 'joined-child.json').read_text())
        self.assertEqual(joined['status'], 0)
        for pid in (self.calls[0]['pid'], joined['pid']):
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertFalse(list(self.job.glob('attempt-*-process.json')))
        for document in (self.meta, self.receipt, self.status):
            self.assertEqual(document['attempt'], 1)
            self.assertEqual(document['reason_class'], 'transport-interrupted')
        self.assertEqual(self.status['attempts'][0]['exit_code'], 1)
        self.assertIn('nothing was restarted automatically because this mode can change files.', self.receipt['tail'])
        self.assertIn('Next: omnilane jobs resume ' + self.job.name, self.receipt['tail'])

    def test_sysops_cut_after_first_tree_exits_never_restarts(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1, 'joined_child': True}], mode='sysops')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.status['transport_reason'], 'upstream_eof')
        for pid in (self.calls[0]['pid'], json.loads((self.root / 'joined-child.json').read_text())['pid']):
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertIn('because this mode can change files.', self.receipt['tail'])
        self.assertIn('Next: omnilane jobs resume ' + self.job.name, self.receipt['tail'])

    def test_transport_cut_notifies_once_without_private_diagnostics(self):
        result = self.dispatch([{'events': error_events('upstream_error'), 'exit': 1}], mode='work')
        self.assertEqual(result.returncode, 75)
        line = self.receipt['tail']
        self.assertIn(line, self.cli('jobs', 'status', self.job.name).stdout)
        self.env['CLAUDE_PROJECT_DIR'] = str(self.workdir)
        command = ['bash', str(ROOT / 'hooks/report-completions.sh')]
        first = subprocess.run(command, input='{}', env=self.env, capture_output=True, text=True, timeout=10)
        second = subprocess.run(command, input='{}', env=self.env, capture_output=True, text=True, timeout=10)
        self.assertIn(line, first.stdout)
        self.assertIn('TRANSPORT_INTERRUPTED', first.stdout)
        self.assertEqual(first.stdout.count('Omnilane completion:'), 1)
        self.assertEqual(second.stdout, '')
        self.assertNotIn('SYNTHETIC task canary', first.stdout)

    def test_no_resume_flag(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1}], '--no-resume')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.status['reason_class'], 'transport-interrupted')
        self.assertEqual(self.status['outcome'], 'interrupted-needs-decision')
        self.assertIn('nothing was restarted automatically', self.receipt['tail'])

    def test_no_resume_environment(self):
        self.env['OMNILANE_NO_RESUME'] = '1'
        result = self.dispatch([{'events': error_events(), 'exit': 1}])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.status['outcome'], 'interrupted-needs-decision')
        self.assertIn('nothing was restarted automatically', self.receipt['tail'])

    def test_environment_optout_survives_local_defaults(self):
        (self.home / 'local.sh').write_text('OMNILANE_NO_RESUME=0\n')
        self.env['OMNILANE_NO_RESUME'] = '1'
        self.dispatch([{'events': error_events(), 'exit': 1}])
        self.assertEqual(len(self.calls), 1)

    def test_sysops_never_resumes(self):
        self.dispatch([{'events': error_events(), 'exit': 1}], mode='sysops')
        self.assertEqual(len(self.calls), 1)
        self.assertIn('Next: omnilane jobs resume ' + self.job.name, self.receipt['tail'])

    def test_normal_failure_never_resumes(self):
        result = self.dispatch([{'events': [{'type': 'error', 'message': 'permission denied'}], 'exit': 7}])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.status['reason_class'])

    def test_signal_exit_never_resumes_despite_transport_event(self):
        result = self.dispatch([{'events': error_events(), 'exit': 143}])
        self.assertEqual(result.returncode, 143)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.receipt['reason_class'])

    def test_refusal_exit_never_resumes_despite_transport_event(self):
        result = self.dispatch([{'events': error_events(), 'exit': 2}])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.receipt['reason_class'])





class WorkerIntegrationTests(ResumeCliTests):
    """Exercise the worker, runner, status and notice without a process inspector.

    This is NOT a substitute for the dispatch/supervisor acceptance tests above.
    It constructs only synthetic metadata and still runs the actual policy gate.
    """
    def dispatch(self, steps, *flags, mode='advise'):
        if flags:
            self.skipTest('flags and total supervisor budget require the public dispatch tests')
        self.env.update(FIXTURE_STEPS=json.dumps(steps), OMNILANE_TIMEOUT=str(getattr(self, 'call_timeout', 15)),
                        OMNILANE_SESSION_MODE='single-shot',
                        OMNILANE_AA_CALLER_CONTEXT=str(self.caller),
                        OMNILANE_AA_AUTHORIZER_CONTEXT=str(self.caller),
                        OMNILANE_AA_REGISTRY_SHA256=hashlib.sha256((self.root / 'policy.json').read_bytes()).hexdigest(),
                        OMNILANE_AA_OVERLAY_SHA256=hashlib.sha256((self.root / 'overlay.json').read_bytes()).hexdigest())
        self.job = self.home / 'jobs' / '20261111-120000-12345-12345'
        self.job.mkdir(parents=True)
        (self.job / 'task.txt').write_text('SYNTHETIC task canary, do not display\n')
        metadata = dict(lane='fixture', vendor='codex', session_mode='single-shot', idle_timeout=900,
                        model=self.model, effort=self.effort, timeout=15, job_timeout=None,
                        mode=mode, workdir=str(self.workdir), foreman_session='', candidate='1/1',
                        started='2026-11-11T12:00:00Z')
        (self.job / 'meta.json').write_text(json.dumps(metadata, separators=(',', ':')) + '\n')
        result = subprocess.run(['bash', str(ROOT / 'scripts/lib/job-worker.sh'), 'codex', mode,
            str(self.workdir), self.model, self.effort, str(self.job / 'task.txt'), str(self.job / 'out.txt')],
            env=self.env, text=True, capture_output=True, timeout=30)
        self.assertTrue((self.root / 'calls.jsonl').exists(), result.stdout + result.stderr)
        self.calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(codex_resume.main(['finalize', str(self.job), str(result.returncode)]), 0)
        (self.job / 'exit').write_text(str(result.returncode) + '\n')
        inbox = self.home / 'inbox'
        inbox.mkdir()
        receipt = inbox / (self.job.name + '.json')
        receipt.write_text(json.dumps(dict(job_id=self.job.name, workdir=str(self.workdir),
                                          lane='fixture', vendor='codex', exit=result.returncode, tail='')))
        self.assertEqual(codex_resume.main(['receipt', str(self.job), str(receipt)]), 0)
        self.meta = json.loads((self.job / 'meta.json').read_text())
        self.receipt = json.loads(receipt.read_text())
        status = self.cli('jobs', 'status', self.job.name, '--json')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.status = json.loads(status.stdout)['job']
        return result


    def test_no_process_inspection_is_needed_to_decline_work_auto_resume(self):
        inspector = self.bins / 'ps'
        inspector.write_text('#!/bin/sh\nprintf called > "' + str(self.root / 'inspection-called') + '"\nexit 1\n')
        inspector.chmod(0o700)
        result = self.dispatch([{'events': error_events(), 'exit': 1}], mode='work')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root / 'inspection-called').exists())

    def test_persisted_advise_also_never_resumes(self):
        self.env.update(OMNILANE_THREAD_MODE='resume', OMNILANE_THREAD_ID='11111111-2222-4333-8444-555555555555')
        result = self.dispatch([{'events': error_events(), 'exit': 1}], mode='advise')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('--ephemeral', self.calls[0]['argv'])
        self.assertFalse(self.status['auto_resume_eligible'])
        self.assertEqual(self.status['continuation_blocked_reason'], 'automatic-resume-disabled')

    def test_write_succeeded_reply_lost_effect_happens_once(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1, 'effect': True},
                                {'output': 'must not run\n', 'effect': True}], mode='work')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.workdir / 'append-only-ledger.txt').read_text().splitlines(), ['non-idempotent effect'])

    def test_real_per_call_watchdog_never_resumes(self):
        self.call_timeout = 1
        result = self.dispatch([{'events': error_events(), 'delay_after': 20}])
        self.assertIn(result.returncode, (124, 142))
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.receipt['reason_class'])

    def test_public_no_resume_flag_parses_in_dry_run_without_launch(self):
        result = self.cli('dispatch', '--dry-run', '--no-resume', '--executor', 'cli',
                          '--caller-context', str(self.caller), '--workdir', str(self.workdir),
                          'fixture', 'synthetic dry run')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'calls.jsonl').exists())
        self.assertFalse((self.home / 'jobs').exists())



    def test_completed_turn_then_transport_never_resumes(self):
        result = self.dispatch([{'events': [{'type': 'turn.completed'}] + error_events(),
                                 'exit': 1}], mode='work')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.status['outcome'], 'completed-with-transport-error')
        self.assertFalse(self.status['auto_resume_eligible'])

    def assert_later_turn_is_interrupted(self, events):
        result = self.dispatch([{'events': events + error_events(), 'exit': 1}], mode='work')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.job / 'out.txt').stat().st_size, 0)
        self.assertFalse(self.status['completion_observed'])
        self.assertEqual(self.status['outcome'], 'interrupted-needs-decision')
        self.assertEqual(self.status['transport_reason'], 'upstream_eof')
        self.assertEqual(self.status['attempts'][0]['exit_code'], 1)
        self.assertFalse(self.status['auto_resume_eligible'])
        self.assertIn('Next: omnilane jobs resume ' + self.job.name, self.receipt['tail'])
        self.assertIn("Running resume is the operator's statement that the earlier writer has stopped "
                      "and continuing after its partial effects is safe.", self.receipt['tail'])

    def test_completed_turn_then_new_turn_tool_is_interrupted(self):
        self.assert_later_turn_is_interrupted([
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'First turn done'}},
            {'type': 'turn.completed'}, {'type': 'turn.started'},
            {'type': 'item.started', 'item': {'type': 'command_execution',
                                           'command': 'synthetic operation', 'status': 'in_progress'}},
        ])

    def test_late_completion_of_an_older_turn_is_interrupted(self):
        # REVIEW5 4a: TS(N) I TS(N+1) TC(N, late) E.
        self.assert_later_turn_is_interrupted([
            {'type': 'turn.started'},
            {'type': 'item.started', 'item': {'type': 'command_execution'}},
            {'type': 'turn.started'}, {'type': 'turn.completed'},
        ])

    def test_unreadable_line_after_completion_is_interrupted(self):
        # REVIEW5 8c: TC, an unparseable line, then EOF; turn.started missing.
        result = self.dispatch([{'events': [{'type': 'turn.completed'}, 'RAW:{"type":"item.started"']
                                 + error_events(), 'exit': 1}], mode='work')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.status['completion_observed'])
        self.assertEqual(self.status['outcome'], 'interrupted-needs-decision')

    def test_completed_turn_then_new_turn_reasoning_is_interrupted(self):
        self.assert_later_turn_is_interrupted([
            {'type': 'turn.completed'}, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'Still working'}},
        ])

    def test_final_reply_then_transport_never_resumes(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1,
                                 'output': 'Task completed\n'}], mode='work')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.job / 'out.txt').read_text(), 'Task completed\n')
        self.assertEqual(self.status['outcome'], 'completed-with-transport-error')

    def test_missing_session_is_interrupted(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1, 'session_id': None}], mode='work')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.status['continuation_blocked_reason'], 'mode-can-change-files')

    def test_ephemeral_advise_is_interrupted(self):
        result = self.dispatch([{'events': error_events(), 'exit': 1}], mode='advise')
        self.assertEqual(len(self.calls), 1)
        self.assertIn('--ephemeral', self.calls[0]['argv'])
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.status['continuation_blocked_reason'], 'automatic-resume-disabled')


    def test_agent_message_event_alone_is_ambiguous(self):
        result = self.dispatch([{'events': [{'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'Task completed'}}] + error_events(), 'exit': 1}], mode='work')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.status['completion_observed'])
        self.assertEqual(self.status['outcome'], 'interrupted-needs-decision')
        self.assertIn('omnilane jobs resume ' + self.job.name, self.receipt['tail'])
        self.assertFalse(self.status['auto_resume_eligible'])

    def test_intermediate_agent_message_then_tool_activity_is_interrupted(self):
        events = [
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                'text': 'I am checking the file; the task is not finished.'}},
            {'type': 'item.started', 'item': {'type': 'command_execution',
                'command': 'synthetic operation', 'status': 'in_progress'}},
        ] + error_events()
        result = self.dispatch([{'events': events, 'exit': 1}], mode='work')
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.job / 'out.txt').stat().st_size, 0)
        self.assertFalse(self.status['completion_observed'])
        self.assertEqual(self.status['outcome'], 'interrupted-needs-decision')
        self.assertIn('omnilane jobs resume ' + self.job.name, self.receipt['tail'])
        self.assertFalse(self.status['auto_resume_eligible'])



    def test_runner_accepts_explicit_original_session_id(self):
        # A separately authorised single synthetic runner call, not a retry of
        # an unproven process tree. Exercises the real runner argv unchanged.
        sid = '11111111-2222-4333-8444-555555555555'
        prompt = self.root / 'continuation.txt'
        prompt.write_text(codex_resume.CONTINUE_PROMPT)
        output = self.root / 'runner-out.txt'
        self.env.update(FIXTURE_STEPS=json.dumps([{'output': 'continued\n'}]),
                        OMNILANE_THREAD_MODE='resume', OMNILANE_THREAD_ID=sid)
        result = subprocess.run(['bash', str(ROOT / 'scripts/runners/run-codex.sh'), 'work',
                                 str(self.workdir), self.model, self.effort, str(prompt), str(output)],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads((self.root / 'calls.jsonl').read_text())
        self.assertEqual(call['argv'][:2], ['exec', 'resume'])
        self.assertEqual(call['argv'][-2:], [sid, '-'])
        self.assertNotIn('--ephemeral', call['argv'])
        self.assertEqual(output.read_text(), 'continued\n')

    def test_completed_notice_once_and_private_diagnostics_not_leaked(self):
        self.dispatch([{'events': error_events(), 'exit': 1,
                       'output': 'person@example.test /private/project token=SYNTHETIC_SECRET\n'}], mode='work')
        self.env['CLAUDE_PROJECT_DIR'] = str(self.workdir)
        command = ['bash', str(ROOT / 'hooks/report-completions.sh')]
        first = subprocess.run(command, env=self.env, input='{}', capture_output=True, text=True, timeout=10)
        second = subprocess.run(command, env=self.env, input='{}', capture_output=True, text=True, timeout=10)
        self.assertIn('COMPLETED_WITH_TRANSPORT_ERROR', first.stdout)
        self.assertEqual(first.stdout.count('Omnilane completion:'), 1)
        self.assertEqual(second.stdout, '')
        for private in ('person@example.test', '/private/project', 'SYNTHETIC_SECRET'):
            self.assertNotIn(private, first.stdout)


class RecoveryMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eof-metadata-')
        self.addCleanup(temporary.cleanup)
        self.job = Path(temporary.name)
        (self.job / 'meta.json').write_text('{"vendor":"codex"}')

    def interrupt(self):
        codex_resume.start(self.job, 1)
        (self.job / 'out.txt.progress.log').write_text(''.join(json.dumps(x) + '\n' for x in error_events()))
        codex_resume.end(self.job, 1)


    def test_cancellation_between_attempts_does_not_rewrite_first_attempt(self):
        self.interrupt()
        fields = codex_resume.end(self.job, 143, final=True)
        self.assertEqual(fields['attempts'][0]['exit_code'], 1)
        self.assertIsNone(fields['reason_class'])

    def test_published_cancel_exit_overrides_stale_transport_record(self):
        self.interrupt()
        fields = codex_resume.public_fields(codex_resume.read_json(self.job / codex_resume.STATE_NAME))
        self.assertEqual(fields['reason_class'], 'transport-interrupted')
        self.assertIsNone(codex_resume.fields_for_exit(fields, 143)['reason_class'])
        self.assertIsNone(codex_resume.fields_for_exit(fields, 124)['reason_class'])



    def test_pending_attempt_timeout_is_finalized(self):
        codex_resume.start(self.job, 1)
        fields = codex_resume.end(self.job, 124, final=True)
        self.assertEqual(fields['attempts'][0]['exit_code'], 124)
        self.assertIsNone(fields['reason_class'])

    def test_second_start_is_always_refused(self):
        self.interrupt()
        with self.assertRaises(ValueError):
            codex_resume.start(self.job, 2)

    def test_progress_symlink_never_qualifies(self):
        target = self.job / 'target'
        target.write_text(json.dumps(error_events()[0]) + '\n')
        (self.job / 'out.txt.progress.log').symlink_to(target)
        self.assertIsNone(codex_resume.classify_progress(self.job / 'out.txt.progress.log', 1))






class PublicCancellationTests(unittest.TestCase):
    def test_cancel_after_error_event_does_not_resume(self):
        fixture = ResumeCliTests('test_normal_failure_never_resumes')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env['FIXTURE_STEPS'] = json.dumps([{'events': error_events(), 'delay_after': 20}])
        result = fixture.cli('dispatch', '--background', '--single-shot', '--executor', 'cli',
                             '--caller-context', str(fixture.caller), '--workdir', str(fixture.workdir),
                             '--timeout', '30', '--job-timeout', '30', 'fixture', 'synthetic cancel fixture')
        if 'ps cannot read process start times here, so no job was started' in result.stderr:
            self.skipTest('public cancellation refused: ps cannot read process start times in this sandbox')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        job_id = result.stdout.strip()
        job = fixture.home / 'jobs' / job_id
        try:
            deadline = time.monotonic() + 10
            progress = job / 'out.txt.progress.log'
            while not progress.exists() or not progress.stat().st_size:
                self.assertLess(time.monotonic(), deadline, 'fake vendor never emitted its error')
                time.sleep(0.02)
            cancelled = fixture.cli('jobs', 'cancel', job_id)
            self.assertEqual(cancelled.returncode, 0, cancelled.stdout + cancelled.stderr)
            wait_for_job_controller(self, job)
            calls = (fixture.root / 'calls.jsonl').read_text().splitlines()
            self.assertEqual(len(calls), 1)
            self.assertEqual((job / 'exit').read_text().strip(), '143')
            status = json.loads(fixture.cli('jobs', 'status', job_id, '--json').stdout)['job']
            self.assertIsNone(status.get('reason_class'))
        finally:
            if not (job / 'exit').exists():
                fixture.cli('jobs', 'cancel', job_id)
            wait_for_job_controller(self, job)


if __name__ == '__main__':
    unittest.main()
