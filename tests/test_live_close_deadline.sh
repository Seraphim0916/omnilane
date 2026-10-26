#!/bin/bash

# Only an explicit child invocation from an isolated parent keeps fixture state.
# A stale environment marker alone must never bypass standalone isolation.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
# Isolated worker + real jobs.sh close; never invokes a provider.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# Run directly, this script creates the scratch root and leaves nothing behind;
# under tests/run.sh the root already exists and run.sh removes it.
if [[ ! -d "$ROOT/.test-scratch" ]]; then
  mkdir "$ROOT/.test-scratch"
  trap 'rmdir "$ROOT/.test-scratch" 2>/dev/null || true' EXIT
fi
ROOT="$ROOT" python3 - <<'PY'
import array
import fcntl
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time

root = pathlib.Path(os.environ['ROOT'])
# Generous hang guard for every wait below. The checks themselves come from observable
# state (files, process groups, close's own sub-second clock), not from host scheduling.
GENEROUS = 60
processes = []
exit_recorders = []
opened_fds = set()
scratch = None

def build_fixture(scratch):
    """Copy the code under test into the scratch tree; runs inside the guarded region."""
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
    return fixture

def wait_for(path, timeout=GENEROUS):
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f'missing {path}'
        time.sleep(.02)

def wait_for_absent(path, timeout=GENEROUS):
    end = time.monotonic() + timeout
    while path.exists():
        assert time.monotonic() < end, f'still present {path}'
        time.sleep(.02)

def wait_for_receipt_waiter(closer, receipt, timeout=GENEROUS):
    """Return once jobs close, past its holder checks, polls for the receipt.

    Its receipt poller is a direct child whose arguments name the exit file.
    A close that returns first never took the missing-holder wait path."""
    end = time.monotonic() + timeout
    while True:
        if closer.poll() is not None:
            out, err = closer.communicate()
            raise AssertionError(('close returned before waiting for the exit receipt',
                                  closer.returncode, out, err))
        # -ww: unlimited width on procps and BSD ps, so the exit path is never cut.
        listing = subprocess.run(['ps', '-A', '-ww', '-o', 'ppid=,command='],
                                 capture_output=True, text=True,
                                 timeout=max(.1, end - time.monotonic())).stdout
        for line in listing.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[0] == str(closer.pid) and str(receipt) in fields[1]:
                return
        assert time.monotonic() < end, 'close never reached the receipt wait'
        time.sleep(.02)

def wait_for_stopped(pid, timeout=GENEROUS):
    """A marker or successful kill(STOP) is not a stopped-state receipt."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # WUNTRACED observes STOP without reaping the direct child; available
        # on the system Python 3.9, unlike waitid.
        observed, status = os.waitpid(pid, os.WUNTRACED | os.WNOHANG)
        if observed == pid and os.WIFSTOPPED(status):
            return
        time.sleep(.01)
    raise AssertionError(f'{pid}: holder did not stop')

def wait_for_exit(pid):
    """Close must reap the runner before returning, with no post-close grace."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError(f'{pid}: runner survived close')


def record_exit(worker, destination):
    receipt = destination.with_name('.exit.tmp')
    receipt.write_text(str(worker.wait()) + '\n')
    os.replace(receipt, destination)


class ReleasedReceipt:
    """Holds the exit receipt after the worker is reaped, as the real
    supervisor does while it finalizes: holder PID gone, no exit file yet."""
    def __init__(self, worker, release):
        self.worker, self.release = worker, release

    def wait(self):
        status = self.worker.wait()
        # No timeout: the receipt appears only once close is seen waiting for it, or when
        # the fixture's finally block releases it. A self-release under load would let
        # close find the receipt before it starts waiting and fail the run spuriously.
        self.release.wait()
        return status


def group_gone(pgid, timeout=GENEROUS):
    """True once no process is left in the group; SIGKILL needs a scheduling turn to land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(.02)
    return False


def start_close(job_id, env):
    """Every jobs close runs in its own process group, registered for group cleanup."""
    closer = subprocess.Popen(['/bin/bash', str(root / 'scripts/jobs.sh'), 'close', job_id],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True)
    processes.append(closer)
    return closer


def finish_close(closer):
    """Wait for close under the generous guard, then require its whole group to be gone.

    On a hang the whole group is killed, receipt poller included, before failing."""
    try:
        out, err = closer.communicate(timeout=GENEROUS)
    except subprocess.TimeoutExpired:
        os.killpg(closer.pid, signal.SIGKILL)
        closer.communicate()
        raise AssertionError(f'jobs close {closer.args} hung past {GENEROUS}s')
    # A finished close has already reaped its poller; 5s stays far below the 11s a left-over poller would live.
    if not group_gone(closer.pid, timeout=5):
        os.killpg(closer.pid, signal.SIGKILL)
        raise AssertionError(f'jobs close left processes in group {closer.pid}')
    return subprocess.CompletedProcess(closer.args, closer.returncode, out, err)


def cleanup_fixture(scratch, processes, opened_fds, exit_recorders, original_error):
    errors = []
    try:
        for fd in opened_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        for proc in processes:
            # Kill the whole group even when its leader already exited: a close
            # that returned can still leave its receipt poller in the group.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=GENEROUS)
            except Exception as error:
                errors.append(str(error))
            if not group_gone(proc.pid):
                errors.append(f'process group {proc.pid} survived fixture cleanup')
        for recorder in exit_recorders:
            recorder.join(timeout=GENEROUS)
            if recorder.is_alive():
                errors.append('exit recorder survived fixture cleanup')
    finally:
        try:
            if scratch is not None:
                shutil.rmtree(scratch)
        except OSError as error:
            errors.append(str(error))
    if errors:
        message = '; '.join(errors)
        if original_error is not None:
            print('cleanup: ' + message, file=sys.stderr)
        else:
            raise AssertionError(message)


def wait_for_consumed(fd, timeout=GENEROUS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        available = array.array('i', [0])
        fcntl.ioctl(fd, termios.FIONREAD, available, True)
        if available[0] == 0:
            return
        time.sleep(.01)
    raise AssertionError('holder did not consume the queued input')

def fill_fifo(fd, limit=8 * 1024 * 1024, timeout=GENEROUS):
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
    # Created inside the guarded region: an init failure still removes the whole tree.
    scratch = pathlib.Path(tempfile.mkdtemp(prefix='close-deadline-', dir=root / '.test-scratch'))
    fixture = build_fixture(scratch)
    modes = os.environ.get('CLOSE_TEST_CASES', 'empty,queued,finishing,ordered,partial,flow,epipe,idle-exit,stuck,full-grace,repeat-close').split(',')
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
            proc = subprocess.Popen(['/bin/bash', str(fixture / 'scripts/lib/job-worker.sh'),
                'codex', 'advise', str(case), 'fake', 'medium',
                str(case / 'prompt'), str(job / 'out.txt')],
                env=env, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        processes.append(proc)
        # The holder is this direct child, so waitpid can observe STOP without ps.
        # Preserve the old supervisor's exit-file receipt for the real jobs CLI.
        release = threading.Event()
        reaped = ReleasedReceipt(proc, release) if mode == 'finishing' else proc
        recorder = threading.Thread(target=record_exit, args=(reaped, job / 'exit'))
        recorder.start()
        exit_recorders.append(recorder)
        wait_for(job / 'inbox.ready')
        wait_for(case / 'received')
        holder = int((job / 'inbox.holder.pid').read_text())
        assert holder == proc.pid, 'STOP observer must own the holder child'
        stop = threading.Event()
        producer = None
        hammer = None
        if mode == 'repeat-close':
            # A second close (two jobs close calls, or a retry) can land while the
            # worker is already shutting down; it must never kill the worker.
            def hammer_usr1():
                while True:
                    try: os.kill(holder, signal.SIGUSR1)
                    except ProcessLookupError: return
                    time.sleep(.0005)
            hammer = threading.Thread(target=hammer_usr1, daemon=True)
            hammer.start()
        if mode in ('queued', 'finishing', 'flow', 'epipe'):
            if mode in ('flow', 'epipe'):
                wait_for(case / 'runner-stopped-reading')
            os.kill(holder, signal.SIGSTOP)
            try:
                wait_for_stopped(holder)
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
                payload = b'{"queued":true}\n' if mode in ('queued', 'finishing') else b'{"text":"' + b'x' * 1000 + b'"}\n'
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
            if mode == 'finishing':
                # The worker clears its holder PID on exit, before the supervisor
                # records the exit status. A close landing in that window must
                # wait for the receipt instead of reporting a missing holder.
                wait_for_absent(job / 'inbox.holder.pid')
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
                wait_for_stopped(holder)
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
            wait_for(job / 'exit')
        second_close = None
        if mode == 'flow':
            second_close = start_close(job_id, env)
        started = time.monotonic()
        closer = start_close(job_id, env)
        if mode == 'finishing':
            # Release the receipt only once close is provably waiting for it.
            wait_for_receipt_waiter(closer, job / 'exit')
            release.set()
        result = finish_close(closer)
        elapsed = time.monotonic() - started
        runner_pid = int((case / 'runner.pid').read_text())
        wait_for_exit(runner_pid)
        if hammer: hammer.join(timeout=GENEROUS)
        if second_close:
            assert finish_close(second_close).returncode == result.returncode
        stop.set()
        if producer:
            producer.join(timeout=1)
            os.close(fd)
            opened_fds.remove(fd)
        if mode in ('ordered', 'partial', 'epipe'):
            os.close(fd)
            opened_fds.remove(fd)
        # job-worker.sh budgets a stuck runner at up to 9.91s; close must beat its own
        # 11s deadline. jobs close measures that deadline itself on a sub-second clock
        # and exits 124 with this message when it is missed, so the check reads close's
        # own verdict instead of an outer wall-clock window that host load can stretch.
        assert result.returncode != 124, (mode, elapsed, result.stderr)
        assert 'did not terminate within' not in result.stderr, (mode, elapsed, result.stderr)
        # jobs close tracks the worker PID; the outer fixture shell still needs
        # one scheduling turn to persist its exit code and reap that worker.
        wait_for(job / 'exit')
        proc.wait(timeout=GENEROUS)
        expected = (1, 137) if mode == 'idle-exit' else ((1,) if mode in ('empty', 'flow', 'epipe') else (0,))
        assert result.returncode == proc.returncode and result.returncode in expected, (mode, result.returncode, proc.returncode, result.stdout, result.stderr, (case / 'stderr').read_text())
        assert 'invalid timeout specification' not in (case / 'stderr').read_text()
        if mode in ('queued', 'finishing'): assert b'{"queued":true}\n' in (case / 'received').read_bytes()
        if mode == 'finishing': assert result.stdout == 'already closed (exit 0)\n', (mode, result.stdout, result.stderr)
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
        print(f'PASS {mode}: jobs close exit={result.returncode}, elapsed={elapsed:.3f}s', flush=True)
finally:
    if 'release' in locals(): release.set()
    if 'stop' in locals(): stop.set()
    if 'producer' in locals() and producer: producer.join(timeout=1)
    cleanup_fixture(scratch, processes, opened_fds, exit_recorders, sys.exc_info()[1])
PY
