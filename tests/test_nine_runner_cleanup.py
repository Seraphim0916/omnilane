#!/usr/bin/env python3
"""Dynamic offline execution of all nine actual runner entrypoints.

No provider tool/configuration is inherited. All descendant fixtures exit on an
owned release file or after 25 seconds, including on assertion failure; cleanup
never signals a remembered PID or a process group.
"""
import argparse
import concurrent.futures
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

RUNNERS = ('codex', 'claude', 'gemini', 'grok', 'kimi', 'qwen', 'opencode', 'exec', 'openai-compat')
TOOLS = ('codex', 'claude', 'agy', 'grok', 'kimi', 'qwen', 'opencode', 'fake-exec', 'curl')
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
if '--help' in sys.argv:
    print('--app_data_dir --sandbox --print --print-timeout --output-format --model --add-dir')
    raise SystemExit(0)
def identity():
    if pathlib.Path('/proc/self/stat').exists():
        raw = pathlib.Path('/proc/%s/stat' % os.getpid()).read_text().rsplit(')', 1)[1].split()
        started = raw[19]
        source = 'proc'
    else:
        started = subprocess.check_output(['ps', '-p', str(os.getpid()), '-o', 'lstart='], text=True).strip()
        source = 'ps'
    return dict(pid=os.getpid(), started=started, source=source, pgid=os.getpgrp(), ppid=os.getppid())
def publish(role):
    p = root / (role + '.json')
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(identity()))
    tmp.rename(p)
def wait():
    end = time.monotonic() + 25
    while time.monotonic() < end and not (root / 'release').exists():
        time.sleep(.02)
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    publish('grandchild')
    wait()
    os._exit(0)
publish('child')
print('owned fake provider started', flush=True)
wait()
'''

def live(identity):
    if identity['source'] == 'ps':
        result = subprocess.run(['ps', '-p', str(identity['pid']), '-o', 'stat=', '-o', 'lstart='],
                                text=True, capture_output=True, check=False)
        parts = result.stdout.strip().split(None, 1)
        return (len(parts) == 2 and parts[1] == identity['started']
                and parts[0][0] not in ('Z', 'X'))
    try:
        fields = Path('/proc/%s/stat' % identity['pid']).read_text().rsplit(')', 1)[1].split()
        return fields[19] == identity['started'] and fields[0] not in ('Z', 'X')
    except FileNotFoundError:
        return False

def wait_for(predicate, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.02)
    return bool(predicate())

def run_case(repo, evidence, isolation, runner, mode, thread=False):
    case = evidence / (mode + '-' + runner + ('-thread' if thread else ''))
    case.mkdir(parents=True)
    environment, violations = isolation.isolated_environment(case / 'isolated', os.environ.get('PATH', ''))
    (Path(environment['HOME']) / '.gemini').mkdir()
    fake_bin = case / 'fake-bin'
    fake_bin.mkdir()
    fake = fake_bin / 'owned-fake.py'
    fake.write_text(FAKE)
    fake.chmod(0o755)
    for name in TOOLS:
        (fake_bin / name).symlink_to(fake)
    work = case / 'work'
    work.mkdir()
    prompt = case / 'prompt.txt'
    prompt.write_text('Offline process cleanup fixture. No real provider call.\n')
    environment.update(PATH=str(fake_bin) + os.pathsep + environment['PATH'],
                       FIXTURE_ROOT=str(case), OMNILANE_TIMEOUT='1' if mode == 'call' else '20',
                       OMNILANE_GROK_MAX_ATTEMPTS='1', OMNILANE_OAI_VENDOR='deepseek',
                       DEEPSEEK_API_KEY='offline-fixture-not-a-real-key',
                       DEEPSEEK_BASE_URL='https://offline.invalid')
    if thread:
        environment.update(OMNILANE_THREAD_MODE='new', OMNILANE_THREAD_ID='offline-thread')
    model = str(fake_bin / 'fake-exec') if runner == 'exec' else 'offline-model'
    command = ['bash', str(repo / 'scripts/runners' / ('run-' + runner + '.sh')),
               'advise', str(work), model, '-', str(prompt), str(case / 'output.txt')]
    expected = {'call': 142, 'job': 124, 'term': 143}[mode]
    if mode != 'call':
        command = ['python3', str(repo / 'scripts/lib/process_tree.py'), 'job',
                   '1' if mode == 'job' else '20'] + command
    result = dict(runner=runner, mode=mode, thread=thread, expected=expected, command=command)
    stdout = (case / 'stdout.log').open('w')
    stderr = (case / 'stderr.log').open('w')
    process = subprocess.Popen(command, env=environment, stdout=stdout, stderr=stderr)
    started = time.monotonic()
    identities = []
    try:
        assert wait_for(lambda: (case / 'child.json').exists() and (case / 'grandchild.json').exists(), 4), 'fixture did not become ready'
        identities = [json.loads((case / (role + '.json')).read_text()) for role in ('child', 'grandchild')]
        result['identities'] = identities
        assert all(live(item) for item in identities), 'child and grandchild must be observed alive before cancellation'
        assert identities[0]['pgid'] == identities[1]['pgid'], 'fixture must be same-group descendant'
        result['observed_alive_before_cancellation'] = True
        if mode == 'term':
            # Popen still owns its unreaped direct child: no stale numeric PID.
            assert process.poll() is None
            process.send_signal(signal.SIGTERM)
        code = process.wait(timeout=9)
        result['actual'] = code
        result['elapsed'] = round(time.monotonic() - started, 3)
        assert code == expected, 'expected exit %s, got %s' % (expected, code)
        assert wait_for(lambda: not any(live(item) for item in identities), 2), 'owned descendants survived supervisor completion'
        assert not violations.exists(), 'unmocked network command attempted'
        result['descendants_gone'] = True
        result['passed'] = True
    except Exception as error:
        result['passed'] = False
        result['error'] = str(error)
        result['survivors_before_release'] = [item for item in identities if live(item)]
    finally:
        (case / 'release').touch()
        # Cooperative release is independent of production cleanup under test.
        try:
            process.wait(timeout=27)
        except subprocess.TimeoutExpired:
            # Only our unreaped direct Popen child, never descendant numeric PIDs.
            process.kill()
            process.wait(timeout=3)
        result['fixture_cleanup_verified'] = wait_for(lambda: not any(live(item) for item in identities), 2)
        stdout.close()
        stderr.close()
    (case / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return result


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'fork'), 'fixture requires POSIX fork and process groups')
class NineRunnerCleanupTests(unittest.TestCase):
    def run_matrix(self, mode):
        repo = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('offline_env', repo / 'tests/offline_env.py')
        isolation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolation)
        with tempfile.TemporaryDirectory(prefix='omnilane-nine-runner-') as directory:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(run_case, repo, Path(directory), isolation, runner, mode)
                           for runner in RUNNERS]
                if mode == 'call':
                    futures.append(pool.submit(run_case, repo, Path(directory), isolation, 'gemini', mode, True))
                results = [future.result() for future in futures]
            for result in results:
                with self.subTest(runner=result['runner'], mode=mode):
                    self.assertTrue(result['passed'], json.dumps(result, indent=2))
                    self.assertTrue(result['fixture_cleanup_verified'], json.dumps(result, indent=2))

    def test_all_runner_per_call_timeouts(self):
        self.run_matrix('call')

    def test_all_runner_outer_job_timeouts(self):
        self.run_matrix('job')

    def test_all_runner_forwarded_term(self):
        self.run_matrix('term')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--modes', nargs='+', choices=('call', 'job', 'term'), default=['call', 'job', 'term'])
    parser.add_argument('--runners', nargs='+', choices=RUNNERS, default=RUNNERS)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('offline_env', args.repo / 'tests/offline_env.py')
    isolation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolation)
    cases = [(r, m) for m in args.modes for r in args.runners]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, args.repo.resolve(), args.evidence.resolve(), isolation, r, m) for r, m in cases]
        if 'call' in args.modes and 'gemini' in args.runners:
            futures.append(pool.submit(run_case, args.repo.resolve(), args.evidence.resolve(), isolation, 'gemini', 'call', True))
        results = [f.result() for f in futures]
    (args.evidence / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')
    print('%s/%s dynamic runner cases passed' % (sum(r['passed'] for r in results), len(results)))
    return int(not all(r['passed'] and r['fixture_cleanup_verified'] for r in results))

if __name__ == '__main__':
    raise SystemExit(main())
