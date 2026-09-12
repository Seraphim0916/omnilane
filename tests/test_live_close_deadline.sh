#!/bin/bash
# Isolated worker + real jobs.sh close; never invokes a provider.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$ROOT" python3 - <<'PY'
import array
import fcntl
import json
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import termios
import threading
import time

root = pathlib.Path(os.environ['ROOT'])
scratch = pathlib.Path(tempfile.mkdtemp(prefix='close-deadline-', dir=root / '.test-scratch'))
fixture = scratch / 'repo'
(fixture / 'scripts/lib').mkdir(parents=True)
(fixture / 'scripts/runners').mkdir()
(fixture / 'config').mkdir()
shutil.copyfile(root / 'config/aa-model-policy.json', fixture / 'config/aa-model-policy.json')
for name in ('common.sh', 'live-protocol.sh', 'aa_policy.py'):
    shutil.copyfile(root / 'scripts/lib' / name, fixture / 'scripts/lib' / name)
with (fixture / 'scripts/lib/live-protocol.sh').open('a') as stream:
    stream.write('\ncodex_live_surface_available() { return 0; }\n')
shutil.copyfile(os.environ.get('CLOSE_WORKER_UNDER_TEST', root / 'scripts/lib/job-worker.sh'), fixture / 'scripts/lib/job-worker.sh')
# Pause only the idle-exit fixture at the exact idle-pump/loop-guard boundary.
# Production forwarding/drain code is unchanged by this scheduling hook.
worker_copy = fixture / 'scripts/lib/job-worker.sh'
worker_text = worker_copy.read_text()
loop_boundary = 'done\n\nif [[ "$forward_spool_open"'
assert worker_text.count(loop_boundary) == 1
pause = '''
  if [[ "$CASE_MODE" == "idle-exit" && "$close_requested" -eq 0 && ! -e "$CASE_DIR/at-loop-boundary" ]]; then
    : > "$CASE_DIR/at-loop-boundary"
    kill -STOP "$$"
  fi
'''
worker_copy.write_text(worker_text.replace(loop_boundary, pause + loop_boundary))
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
        if mode in ('flow', 'epipe'):
            (case / 'runner-stopped-reading').write_text('ready')
            while True: time.sleep(.05)
        for line in incoming: captured.write(line)
if mode == 'full-grace': time.sleep(7)
if mode == 'stuck':
    while True: time.sleep(.05)
''')
runner.chmod(0o755)
processes = []
opened_fds = set()

def wait_for(path, timeout=5):
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f'missing {path}'
        time.sleep(.02)

def wait_for_consumed(fd, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        available = array.array('i', [0])
        fcntl.ioctl(fd, termios.FIONREAD, available, True)
        if available[0] == 0:
            return
        time.sleep(.01)
    raise AssertionError('holder did not consume the queued input')

def fill_fifo(fd, limit=8 * 1024 * 1024, timeout=2):
    """Establish backpressure, not a platform-dependent producer race."""
    deadline = time.monotonic() + timeout
    written = 0
    block = b'x' * 4096
    while written < limit and time.monotonic() < deadline:
        try:
            written += os.write(fd, block)
        except BlockingIOError:
            if len(block) == 1:
                assert written > 0, 'runner FIFO was not empty before prefill'
                return written
            # Fill any tail smaller than PIPE_BUF before confirming EAGAIN.
            block = b'x'
    raise AssertionError(f'runner FIFO did not reach bounded backpressure: {written}')

try:
    modes = os.environ.get('CLOSE_TEST_CASES', 'empty,queued,ordered,partial,flow,epipe,idle-exit,stuck,full-grace').split(',')
    for number, mode in enumerate(modes, 1):
        case = scratch / mode
        home = case / 'home'
        job_id = f'20260905-160000-12345-{number}'
        job = home / 'jobs' / job_id
        job.mkdir(parents=True)
        (case / 'prompt').write_text('initial\n')
        (job / 'meta.json').write_text('{"lane":"hardest-coding","vendor":"codex","session_mode":"live"}')
        clean = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_AA_")}
        env = dict(clean, OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1", OMNILANE_HOME=str(home), OMNILANE_REPO=str(fixture),
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
        if mode in ('queued', 'flow', 'epipe'):
            if mode in ('flow', 'epipe'):
                wait_for(case / 'runner-stopped-reading')
            os.kill(holder, signal.SIGSTOP)
            try:
                if mode in ('flow', 'epipe'):
                    runner_fd = os.open(job / 'runner-inbox.fifo', os.O_WRONLY | os.O_NONBLOCK)
                    opened_fds.add(runner_fd)
                    try:
                        # The fake runner has stopped reading. Fill its FIFO
                        # before close so even a briefly idle producer cannot
                        # let the drain finish successfully on an empty inbox.
                        fill_fifo(runner_fd)
                    finally:
                        os.close(runner_fd)
                        opened_fds.remove(runner_fd)
                fd = os.open(job / 'inbox.fifo', os.O_WRONLY | os.O_NONBLOCK)
                opened_fds.add(fd)
                payload = b'{"queued":true}\n' if mode == 'queued' else b'{"text":"' + b'x' * 1000 + b'"}\n'
                assert os.write(fd, payload) == len(payload)
                if mode == 'flow':
                    def produce():
                        while not stop.is_set():
                            try: os.write(fd, payload)
                            except (BlockingIOError, BrokenPipeError): pass
                            time.sleep(.001)
                    producer = threading.Thread(target=produce, daemon=True)
                    producer.start()
                elif mode != 'epipe':
                    os.close(fd)
                    opened_fds.remove(fd)
                # Deliver close before resuming: the queued line must survive drain.
                if mode != 'epipe': os.kill(holder, signal.SIGUSR1)
            finally:
                # A setup failure must never leave this fixture's holder stopped.
                try: os.kill(holder, signal.SIGCONT)
                except ProcessLookupError: pass
        if mode in ('ordered', 'partial'):
            fd = os.open(job / 'inbox.fifo', os.O_WRONLY | os.O_NONBLOCK)
            opened_fds.add(fd)
            payload = (b'{"sequence":1}\n{"sequence":2}\n' if mode == 'ordered'
                       else b'{"partial":true}')
            assert os.write(fd, payload) == len(payload)
            wait_for_consumed(fd)
        if mode == 'epipe':
            wait_for_consumed(fd)
            os.kill(int((case / 'runner.pid').read_text()), signal.SIGTERM)
        if mode == 'idle-exit':
            wait_for(case / 'at-loop-boundary')
            try:
                fd = os.open(job / 'inbox.fifo', os.O_WRONLY | os.O_NONBLOCK)
                opened_fds.add(fd)
                payload = b'{"text":"accepted-before-runner-exit"}\n'
                assert os.write(fd, payload) == len(payload)
                os.close(fd)
                opened_fds.remove(fd)
                os.kill(int((case / 'runner.pid').read_text()), signal.SIGKILL)
            finally:
                try: os.kill(holder, signal.SIGCONT)
                except ProcessLookupError: pass
            # No operator close signal may mask this natural reader failure.
            wait_for(job / 'exit', timeout=5)
        second_close = None
        if mode == 'flow':
            second_close = subprocess.Popen(['/bin/bash', str(root / 'scripts/jobs.sh'), 'close', job_id],
                                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, start_new_session=True)
            processes.append(second_close)
        started = time.monotonic()
        result = subprocess.run(['/bin/bash', str(root / 'scripts/jobs.sh'), 'close', job_id],
                                env=env, capture_output=True, text=True, timeout=11)
        elapsed = time.monotonic() - started
        if second_close:
            second_close.communicate(timeout=2)
            assert second_close.returncode == result.returncode
        stop.set()
        if producer:
            producer.join(timeout=1)
            os.close(fd)
            opened_fds.remove(fd)
        if mode in ('ordered', 'partial', 'epipe'):
            os.close(fd)
            opened_fds.remove(fd)
        assert result.returncode != 124, (mode, elapsed, result.stderr)
        # jobs close tracks the worker PID; the outer fixture shell still needs
        # one scheduling turn to persist its exit code and reap that worker.
        wait_for(job / 'exit', timeout=3)
        proc.wait(timeout=1)
        assert elapsed < 9.0, (mode, elapsed, result.stdout, result.stderr)
        expected = (1, 137) if mode == 'idle-exit' else ((1,) if mode in ('empty', 'flow', 'epipe') else (0,))
        assert result.returncode == proc.returncode and result.returncode in expected, (mode, result.returncode, proc.returncode, result.stdout, result.stderr, (case / 'stderr').read_text())
        assert 'invalid timeout specification' not in (case / 'stderr').read_text()
        if mode == 'queued': assert b'{"queued":true}\n' in (case / 'received').read_bytes()
        if mode in ('ordered', 'partial'):
            received = (case / 'received').read_bytes()
            assert received.endswith(payload), (mode, 'input suffix lost or reordered')
            assert received.count(payload) == 1, (mode, 'input duplicated')
        if mode in ('flow', 'epipe'):
            assert (job / 'close-pending.jsonl').stat().st_size > 0
        if mode == 'idle-exit':
            retained = (job / 'close-pending.jsonl').read_bytes()
            assert retained == payload, 'idle-exit accepted bytes lost, reordered, or duplicated'
        assert not list(job.glob('.forward.*')), 'forward spool survived close'
        if mode in ('stuck', 'flow'): assert (case / 'term').exists(), f'{mode}: TERM not delivered before KILL'
        if mode == 'full-grace': assert not (case / 'term').exists(), '7-second normal shutdown interrupted'
        runner_pid = int((case / 'runner.pid').read_text())
        try: os.kill(runner_pid, 0)
        except ProcessLookupError: pass
        else: raise AssertionError(f'{mode}: runner survived close')
        print(f'PASS {mode}: jobs close exit={result.returncode}, elapsed={elapsed:.3f}s', flush=True)
finally:
    if 'stop' in locals(): stop.set()
    if 'producer' in locals() and producer: producer.join(timeout=1)
    for fd in opened_fds:
        try: os.close(fd)
        except OSError: pass
    for proc in processes:
        if proc.poll() is not None: continue
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait()
    shutil.rmtree(scratch)
PY
