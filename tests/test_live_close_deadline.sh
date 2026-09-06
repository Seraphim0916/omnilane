#!/bin/bash
# Isolated worker + real jobs.sh close; never invokes a provider.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$ROOT" python3 - <<'PY'
import json
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import threading
import time

root = pathlib.Path(os.environ['ROOT'])
scratch = pathlib.Path(tempfile.mkdtemp(prefix='close-deadline-', dir=root / '.test-scratch'))
fixture = scratch / 'repo'
(fixture / 'scripts/lib').mkdir(parents=True)
(fixture / 'scripts/runners').mkdir()
for name in ('common.sh', 'live-protocol.sh'):
    shutil.copyfile(root / 'scripts/lib' / name, fixture / 'scripts/lib' / name)
with (fixture / 'scripts/lib/live-protocol.sh').open('a') as stream:
    stream.write('\ncodex_live_surface_available() { return 0; }\n')
shutil.copyfile(os.environ.get('CLOSE_WORKER_UNDER_TEST', root / 'scripts/lib/job-worker.sh'), fixture / 'scripts/lib/job-worker.sh')
runner = fixture / 'scripts/runners/run-codex.sh'
runner.write_text('''#!/usr/bin/env python3
import os, pathlib, signal, sys, time
case = pathlib.Path(os.environ['CASE_DIR'])
mode = os.environ['CASE_MODE']
(case / 'runner.pid').write_text(str(os.getpid()))
def term(*args):
    (case / 'term').write_text('TERM')
    if mode not in ('stuck', 'flow'): sys.exit(143)
signal.signal(signal.SIGTERM, term)
with open(os.environ['OMNILANE_INBOX']) as incoming:
    with (case / 'received').open('w', buffering=1) as captured:
        captured.write(incoming.readline())
        if mode != 'empty':
            pathlib.Path(sys.argv[6] + '.events.jsonl').write_text(
                '{"method":"turn/completed","params":{"turn":{"status":"completed"}}}\\n')
        if mode == 'flow':
            while True: time.sleep(.05)
        for line in incoming: captured.write(line)
if mode == 'full-grace': time.sleep(7)
if mode == 'stuck':
    while True: time.sleep(.05)
''')
runner.chmod(0o755)
processes = []

def wait_for(path, timeout=5):
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f'missing {path}'
        time.sleep(.02)

try:
    modes = os.environ.get('CLOSE_TEST_CASES', 'empty,queued,flow,stuck,full-grace').split(',')
    for number, mode in enumerate(modes, 1):
        case = scratch / mode
        home = case / 'home'
        job_id = f'20260905-160000-12345-{number}'
        job = home / 'jobs' / job_id
        job.mkdir(parents=True)
        (case / 'prompt').write_text('initial\n')
        (job / 'meta.json').write_text('{"lane":"hardest-coding","vendor":"codex","session_mode":"live"}')
        env = dict(os.environ, OMNILANE_HOME=str(home), OMNILANE_REPO=str(fixture),
                   OMNILANE_SESSION_MODE='live', OMNILANE_LIVE_REQUIRED='1',
                   OMNILANE_IDLE_TIMEOUT='0', CASE_DIR=str(case), CASE_MODE=mode)
        for key in ('OMNILANE_JOB_WORKER_REPO', 'OMNILANE_JOB_WORKER_EXPECTED_SHA256'):
            env.pop(key, None)
        with (case / 'stderr').open('w') as stderr:
            proc = subprocess.Popen(['/bin/bash', '-c',
                '/bin/bash "$1" codex advise "$2" fake medium "$3" "$4"; rc=$?; printf "%s\\n" "$rc" > "$5"; exit "$rc"',
                'test-close', str(fixture / 'scripts/lib/job-worker.sh'), str(case),
                str(case / 'prompt'), str(job / 'out.txt'), str(job / 'exit')],
                env=env, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        processes.append(proc)
        wait_for(job / 'inbox.ready')
        wait_for(case / 'received')
        holder = int((job / 'inbox.holder.pid').read_text())
        stop = threading.Event()
        producer = None
        if mode in ('queued', 'flow'):
            os.kill(holder, signal.SIGSTOP)
            fd = os.open(job / 'inbox.fifo', os.O_WRONLY | os.O_NONBLOCK)
            payload = b'{"queued":true}\n' if mode == 'queued' else b'{"text":"' + b'x' * 1000 + b'"}\n'
            os.write(fd, payload)
            if mode == 'flow':
                def produce():
                    while not stop.is_set():
                        try: os.write(fd, payload)
                        except (BlockingIOError, BrokenPipeError): pass
                        time.sleep(.001)
                producer = threading.Thread(target=produce, daemon=True)
                producer.start()
            else:
                os.close(fd)
            # Deliver close before resuming: the queued line must survive drain.
            os.kill(holder, signal.SIGUSR1)
            os.kill(holder, signal.SIGCONT)
        started = time.monotonic()
        result = subprocess.run(['/bin/bash', str(root / 'scripts/jobs.sh'), 'close', job_id],
                                env=env, capture_output=True, text=True, timeout=11)
        elapsed = time.monotonic() - started
        stop.set()
        if producer:
            producer.join(timeout=1)
            os.close(fd)
        assert result.returncode != 124, (mode, elapsed, result.stderr)
        # jobs close tracks the worker PID; the outer fixture shell still needs
        # one scheduling turn to persist its exit code and reap that worker.
        wait_for(job / 'exit', timeout=3)
        proc.wait(timeout=1)
        assert elapsed < 9.0, (mode, elapsed, result.stdout, result.stderr)
        expected = 1 if mode in ('empty', 'flow') else 0
        assert result.returncode == proc.returncode == expected, (mode, result.returncode, proc.returncode, result.stdout, result.stderr, (case / 'stderr').read_text())
        assert 'invalid timeout specification' not in (case / 'stderr').read_text()
        if mode == 'queued': assert b'{"queued":true}\n' in (case / 'received').read_bytes()
        if mode == 'flow': assert (job / 'close-pending.jsonl').stat().st_size > 0
        if mode in ('stuck', 'flow'): assert (case / 'term').exists(), f'{mode}: TERM not delivered before KILL'
        if mode == 'full-grace': assert not (case / 'term').exists(), '7-second normal shutdown interrupted'
        runner_pid = int((case / 'runner.pid').read_text())
        try: os.kill(runner_pid, 0)
        except ProcessLookupError: pass
        else: raise AssertionError(f'{mode}: runner survived close')
        print(f'PASS {mode}: jobs close exit={result.returncode}, elapsed={elapsed:.3f}s', flush=True)
finally:
    if 'stop' in locals(): stop.set()
    for proc in processes:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait()
    shutil.rmtree(scratch)
PY
