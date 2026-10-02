"""Offline cancellation regression matrix with cooperatively owned fixtures.

OMNILANE_CANCEL_TEST_ROOT permits running identical tests against an untouched
baseline. Fixture cleanup uses a private release file, never PID/group scans or
numeric PID signals; each process has an independent hard lifetime bound.
"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from offline_env import isolated_environment

ROOT = Path(os.environ.get('OMNILANE_CANCEL_TEST_ROOT', Path(__file__).resolve().parents[1]))

FIXTURE = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['TREE_FIXTURE_ROOT'])
mode = sys.argv[1] if len(sys.argv) > 1 else 'tree'
def record(name):
    path = root / (name + '.json')
    data = {'pid': os.getpid(), 'pgid': os.getpgrp()}
    if Path('/proc/self/stat').exists():
        data['started'] = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data)); tmp.replace(path)
def wait():
    deadline = time.monotonic() + 25
    while not (root / 'release').exists() and time.monotonic() < deadline:
        time.sleep(.02)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode in ('leaf', 'detached', 'sentinel'):
    record(mode + '-before')
    if mode == 'detached':
        time.sleep(.4)  # Allow the supervisor to observe lineage before escape.
        os.setsid()
    record(mode)
    wait()
else:
    leaf = 'detached' if mode == 'escape' else 'leaf'
    subprocess.Popen([sys.executable, __file__, leaf],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 4
    while not (root / (leaf + '.json')).exists():
        if time.monotonic() > deadline: raise SystemExit(98)
        time.sleep(.01)
    record('parent')
    if mode == 'exit': raise SystemExit(7)
    wait()
'''


class DescendantCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='omnilane-descendants-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env, violations = isolated_environment(self.root / 'isolated', os.environ['PATH'])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / 'omnilane'
        self.home.mkdir()
        self.env.update(TREE_FIXTURE_ROOT=str(self.root), OMNILANE_HOME=str(self.home),
                        OMNILANE_PROCESS_CONSERVATIVE='1')
        self.fixture = self.root / 'fixture.py'
        self.fixture.write_text(FIXTURE)
        self.processes = []
        self.handles = []
        self.addCleanup(self.cleanup_fixtures)

    def cleanup_fixtures(self):
        (self.root / 'release').touch()
        # Only our unreaped Popen children may be signalled. Descendants exit
        # cooperatively, even in the deliberately broken baseline tests.
        for process in self.processes:
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(self.live(p) for p in self.identities()):
            time.sleep(.02)
        alive = [p for p in self.identities() if self.live(p)]
        for handle in self.handles:
            handle.close()
        self.assertFalse(alive, 'fixture cooperative cleanup failed: ' + repr(alive))

    def identities(self):
        return [json.loads(p.read_text()) for p in self.root.glob('*.json')]

    def live(self, identity):
        pid = identity['pid']
        proc = Path('/proc') / str(pid) / 'stat'
        if Path('/proc/self/stat').exists():
            try:
                data = proc.read_text().rsplit(')', 1)[1].split()
            except FileNotFoundError:
                return False
            return data[0] not in ('Z', 'X') and data[19] == identity.get('started', data[19])
        got = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], env=self.env,
                             capture_output=True, text=True, timeout=2)
        return bool(got.stdout.strip()) and not got.stdout.lstrip().startswith(('Z', 'X'))

    def wait_file(self, path, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if path.exists(): return path
            time.sleep(.02)
        self.fail('fixture did not publish ' + str(path))

    def launch(self, command, env=None):
        out = open(self.root / ('stdout-%s' % len(self.processes)), 'w+')
        err = open(self.root / ('stderr-%s' % len(self.processes)), 'w+')
        self.handles.extend((out, err))
        proc = subprocess.Popen(command, env=env or self.env, stdout=out, stderr=err)
        self.processes.append(proc)
        proc.fixture_err = err
        return proc

    def finish(self, proc, expected):
        rc = proc.wait(timeout=12)
        proc.fixture_err.seek(0)
        self.assertEqual(expected, rc, proc.fixture_err.read())

    def wrapper(self, kind, seconds='1', mode='tree'):
        return self.launch(['perl', str(ROOT / 'scripts/lib' / (kind + '-timeout.pl')),
                            seconds, sys.executable, str(self.fixture), mode])

    def stopped(self, name='leaf'):
        identity = json.loads(self.wait_file(self.root / (name + '.json')).read_text())
        self.assertFalse(self.live(identity), 'owned %s survived cleanup: %s' % (name, identity))

    def test_per_call_timeout_142_kills_term_ignoring_grandchild(self):
        self.finish(self.wrapper('call'), 142)
        self.stopped(); self.stopped('parent')

    def test_immediate_child_exit_cleans_same_group_grandchild(self):
        self.finish(self.wrapper('call', '5', 'exit'), 7)
        self.stopped()

    def test_whole_job_timeout_124_cleans_tree_and_reports_complete(self):
        self.env['OMNILANE_PROCESS_JOB_DIR'] = str(self.root / 'report')
        Path(self.env['OMNILANE_PROCESS_JOB_DIR']).mkdir()
        self.finish(self.wrapper('job'), 124)
        self.stopped(); self.stopped('parent')
        report = json.loads((Path(self.env['OMNILANE_PROCESS_JOB_DIR']) / 'process-cleanup.json').read_text())
        self.assertTrue(report['complete'])
        self.assertEqual(report['survivors'], [])

    def test_term_forwarding_cleans_tree(self):
        proc = self.wrapper('call', '20')
        self.wait_file(self.root / 'parent.json')
        proc.terminate()
        self.finish(proc, 143)
        self.stopped(); self.stopped('parent')

    def test_unrelated_sentinel_survives_owned_tree_timeout(self):
        sentinel = self.launch([sys.executable, str(self.fixture), 'sentinel'])
        identity = json.loads(self.wait_file(self.root / 'sentinel.json').read_text())
        self.finish(self.wrapper('call'), 142)
        self.stopped()
        self.assertIsNone(sentinel.poll())
        self.assertTrue(self.live(identity))

    def test_conservative_detached_escape_reports_incomplete_without_killing_it(self):
        reportdir = self.root / 'report'; reportdir.mkdir()
        self.env['OMNILANE_PROCESS_JOB_DIR'] = str(reportdir)
        self.finish(self.wrapper('job', '2', 'escape'), 125)
        identity = json.loads(self.wait_file(self.root / 'detached.json').read_text())
        self.assertTrue(self.live(identity))
        report = json.loads((reportdir / 'process-cleanup.json').read_text())
        self.assertFalse(report['complete'])
        survivor = next(p for p in report['survivors'] if p['pid'] == identity['pid'])
        self.assertTrue(survivor['started'])
        self.assertTrue(survivor['command'])
        self.assertNotIn(str(self.fixture), survivor['command'])
        self.assertNotIn('args', survivor)

    def test_process_enumeration_failure_is_unconfirmed_125(self):
        reportdir = self.root / 'report'; reportdir.mkdir()
        self.env['OMNILANE_PROCESS_JOB_DIR'] = str(reportdir)
        launcher = self.root / 'enumeration_failure.py'
        launcher.write_text('import importlib.util,sys\n'
            'spec=importlib.util.spec_from_file_location("engine",sys.argv[1])\n'
            'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
            'def failed_snapshot(): raise OSError("fixture process enumeration failed")\n'
            'm.snapshot=failed_snapshot\n'
            'raise SystemExit(m.main(["job","2"]+sys.argv[2:]))\n')
        self.finish(self.launch([sys.executable, str(launcher),
                     str(ROOT / 'scripts/lib/process_tree.py'), sys.executable, str(self.fixture), 'tree']), 125)
        report = json.loads((reportdir / 'process-cleanup.json').read_text())
        self.assertFalse(report['complete'])
        self.assertTrue(report['errors'])

    def test_inner_incomplete_propagates_when_outer_never_observed_escape(self):
        reportdir = self.root / 'report'; reportdir.mkdir()
        self.env['OMNILANE_PROCESS_JOB_DIR'] = str(reportdir)
        launcher = self.root / 'outer.py'
        launcher.write_text('import importlib.util,os,sys\n'
            'spec=importlib.util.spec_from_file_location("engine",sys.argv[1])\n'
            'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
            'original=m.snapshot\n'
            'supervisor=m.Supervisor("job",None,sys.argv[2:],os.environ["OMNILANE_PROCESS_JOB_DIR"])\n'
            'def outer_snapshot():\n'
            '    rows=original()\n'
            '    return {pid: row for pid,row in rows.items() if pid in (os.getpid(),supervisor.keeper_pid) or row["ppid"] == supervisor.keeper_pid}\n'
            'm.snapshot=outer_snapshot\n'
            'raise SystemExit(supervisor.run())\n')
        proc = self.launch([sys.executable, str(launcher), str(ROOT / 'scripts/lib/process_tree.py'),
                            'perl', str(ROOT / 'scripts/lib/call-timeout.pl'), '2',
                            sys.executable, str(self.fixture), 'escape'])
        self.finish(proc, 125)
        escaped = json.loads((self.root / 'detached.json').read_text())
        self.assertTrue(self.live(escaped))
        inner_paths = list(reportdir.glob('call-cleanup.*.json'))
        self.assertEqual(len(inner_paths), 1)
        inner = json.loads(inner_paths[0].read_text())
        outer = json.loads((reportdir / 'process-cleanup.json').read_text())
        self.assertFalse(inner['complete'])
        self.assertFalse(outer['complete'])
        self.assertEqual(outer['exit_code'], 125)
        self.assertIn('per-call cleanup was incomplete', outer['errors'])
        self.assertIn(escaped['pid'], [row['pid'] for row in outer['survivors']])
        self.assertNotIn(escaped['pid'], [row['pid'] for row in
            json.loads((reportdir / 'process-state.json').read_text())['processes']])
        # Reproduce dispatch's persisted exit after this real supervisor run;
        # jobs status/result must retain the same incomplete outcome.
        jobs = self.home / 'jobs'; jobs.mkdir()
        job = jobs / '20261002-000000-123-1'
        reportdir.rename(job)
        (job / 'exit').write_text(str(proc.returncode) + '\n')
        status = subprocess.run(['bash', str(ROOT / 'scripts/jobs.sh'), '--json', 'status', job.name],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['job']['exit_code'], 125)
        result = subprocess.run(['bash', str(ROOT / 'scripts/jobs.sh'), 'result', job.name],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn('incomplete', result.stderr)

    def test_portable_ps_failure_cannot_be_an_empty_successful_snapshot(self):
        spec = importlib.util.spec_from_file_location('cancel_tree_test', ROOT / 'scripts/lib/process_tree.py')
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        failure = subprocess.CompletedProcess(['ps'], 73, '', 'fixture failure')
        with patch.object(engine.sys, 'platform', 'darwin'), patch.object(engine.subprocess, 'run', return_value=failure):
            with self.assertRaises(OSError):
                engine.snapshot()

    def test_term_in_fork_to_session_startup_window(self):
        launcher = self.root / 'startup.py'
        launcher.write_text('import importlib.util,os,signal,sys\n'
            'spec=importlib.util.spec_from_file_location("engine",sys.argv[1])\n'
            'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
            'fork=os.fork\n'
            'def interrupted_fork():\n'
            '    pid=fork()\n'
            '    if pid: os.kill(os.getpid(),signal.SIGTERM)\n'
            '    return pid\n'
            'm.os.fork=interrupted_fork\n'
            'raise SystemExit(m.main(["job","--no-deadline"]+sys.argv[2:]))\n')
        proc = self.launch([sys.executable, str(launcher), str(ROOT / 'scripts/lib/process_tree.py'),
                            sys.executable, str(self.fixture), 'tree'])
        self.finish(proc, 143)
        for identity in self.identities(): self.assertFalse(self.live(identity))

    def dispatch(self, background, timeout):
        gate = self.root / 'gate'
        gate.write_text('#!/bin/sh\nexec ' + sys.executable + ' ' + str(self.fixture) + ' tree\n')
        gate.chmod(0o755)
        (self.home / 'routing.local.yaml').write_text('fixture: exec "' + str(gate) + '" -\n')
        cmd = ['bash', str(ROOT / 'scripts/dispatch.sh'), '--timeout', '20']
        if background: cmd += ['--background', '--single-shot']
        if timeout: cmd += ['--job-timeout', '20']
        proc = self.launch(cmd + ['fixture', 'offline owned descendant fixture'])
        self.wait_file(self.root / 'parent.json')
        jobs = list((self.home / 'jobs').iterdir())
        self.assertEqual(len(jobs), 1)
        return proc, jobs[0]

    def cancel_dispatch(self, background, timeout, direct_term=False):
        proc, job = self.dispatch(background, timeout)
        if background: self.finish(proc, 0)
        if direct_term:
            proc.terminate()
        else:
            result = subprocess.run(['bash', str(ROOT / 'scripts/jobs.sh'), 'cancel', job.name],
                                    env=self.env, text=True, capture_output=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr + '\n' + '\n'.join(
                p.name + ': ' + p.read_text() for p in job.glob('*.json')))
        if not background: self.finish(proc, 143)
        self.wait_file(job / 'exit')
        self.assertEqual('143', (job / 'exit').read_text().strip())
        self.stopped(); self.stopped('parent')
        record = json.loads(self.wait_file(self.home / 'inbox' / (job.name + '.json')).read_text())
        self.assertEqual(record['exit'], 143)
        cleanup = json.loads((job / 'process-cleanup.json').read_text())
        self.assertTrue(cleanup['complete'])
        if not direct_term:
            self.assertEqual('complete', json.loads((job / 'cancel.json').read_text())['status'])

    def test_jobs_cancel_foreground_without_job_timeout(self): self.cancel_dispatch(False, False)
    def test_jobs_cancel_foreground_with_job_timeout(self): self.cancel_dispatch(False, True)
    def test_jobs_cancel_background_without_job_timeout(self): self.cancel_dispatch(True, False)
    def test_jobs_cancel_background_with_job_timeout(self): self.cancel_dispatch(True, True)
    def test_foreground_term_publishes_exit_and_completion(self): self.cancel_dispatch(False, False, True)
    def test_foreground_term_with_job_timeout_publishes_completion(self): self.cancel_dispatch(False, True, True)

    def test_nine_runner_entrypoints_use_shared_timeout_helper(self):
        names = ('claude', 'codex', 'exec', 'gemini', 'grok', 'kimi', 'openai-compat', 'opencode', 'qwen')
        for name in names:
            with self.subTest(runner=name):
                source = (ROOT / 'scripts/runners' / ('run-' + name + '.sh')).read_text()
                self.assertIn('run_with_timeout "$RUN_TIMEOUT"', source)
        common = (ROOT / 'scripts/lib/common.sh').read_text()
        self.assertIn('process_tree.py', common)


if __name__ == '__main__':
    unittest.main()
