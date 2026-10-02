#!/usr/bin/env python3
"""Bounded, owned-group supervision; escaped processes are reported, not guessed.

A dedicated session keeper stays unreaped until the last group signal. Portable
systems cannot safely signal an arbitrary detached PID after an identity check:
those escapes are reported as incomplete, never guessed. Snapshot polling cannot
observe a double-fork that escapes between samples; this is not a security sandbox.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import time

POLL = 0.05
TERM_GRACE = 1.0
KILL_GRACE = 1.0
FORWARD_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError("unsafe supervision metadata path")
    temporary = path.with_name('.' + path.name + '.%s' % os.getpid())
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise OSError("missing or unsafe supervision metadata")
    with path.open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('supervision metadata must be an object')
    return value


def start_identity(pid, fallback=None):
    if sys.platform.startswith('linux'):
        try:
            data = Path('/proc/%s/stat' % pid).read_text()
            return 'linux:' + data.rsplit(')', 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    return 'ps:' + fallback if fallback else None


def snapshot():
    # Linux reads identity and relationship fields from the same kernel record.
    # Never combine a stale ps relationship with a later /proc birth identity.
    rows = {}
    scan_deadline = time.monotonic() + 0.75
    if sys.platform.startswith('linux'):
        for path in Path('/proc').iterdir():
            if time.monotonic() >= scan_deadline:
                raise OSError('process enumeration exceeded its deadline')
            if not path.name.isdigit():
                continue
            try:
                data = (path / 'stat').read_text()
                tail = data.rsplit(')', 1)[1].split()
                pid = int(path.name)
                command = data[data.index('(') + 1:data.rindex(')')]
                rows[pid] = dict(pid=pid, ppid=int(tail[1]), pgid=int(tail[2]),
                                 state=tail[0], started='linux:' + tail[19],
                                 command=clean_command(command))
            except OSError as error:
                if error.errno in (errno.ENOENT, errno.ESRCH):
                    continue
                raise OSError('process identity unreadable for pid ' + path.name) from error
            except (ValueError, IndexError) as error:
                raise OSError('invalid process identity for pid ' + path.name) from error
    else:
        # One ps row contains birth identity, parent/group and command name.
        # `comm`, not `args`, keeps secrets in command arguments out of reports.
        result = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid=,stat=,lstart=,comm='],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=0.75, check=False)
        if result.returncode:
            raise OSError('process enumeration failed')
        for line in result.stdout.splitlines():
            fields = line.split(None, 9)
            if len(fields) != 10:
                raise OSError('invalid process enumeration row')
            pid, ppid, pgid = map(int, fields[:3])
            rows[pid] = dict(pid=pid, ppid=ppid, pgid=pgid, state=fields[3],
                             started='ps:' + ' '.join(fields[4:9]),
                             command=clean_command(fields[9]))
    if os.getpid() not in rows:
        raise OSError('process enumeration omitted supervisor')
    return rows


def clean_command(command):
    return ''.join(ch if ch.isprintable() else '?' for ch in os.path.basename(command))[:128]


def valid_survivor(row):
    return (isinstance(row, dict) and type(row.get('pid')) is int and row['pid'] > 0
            and isinstance(row.get('started'), str) and bool(row['started'])
            and isinstance(row.get('command'), str))


def is_live(row):
    return row is not None and not row['state'].startswith(('Z', 'X'))


def status_code(status):
    return 128 + os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)


def write_message(fd, value):
    os.write(fd, (json.dumps(value) + '\n').encode())


def keeper(command, status_fd, control_fd, old_mask):
    """Reserve the session/group ID until the outer sole reaper releases us."""
    os.setsid()
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    for sig in FORWARD_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
    write_message(status_fd, {'ready': True})
    if os.read(control_fd, 1) != b'G':
        os._exit(125)
    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, FORWARD_SIGNALS)
    child = os.fork()
    if child == 0:
        os.close(status_fd)
        os.close(control_fd)
        for sig in FORWARD_SIGNALS:
            signal.signal(sig, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        try:
            os.execvp(command[0], command)
        except OSError as error:
            print('omnilane: watchdog could not start command: %s' % error, file=sys.stderr)
            os._exit(127)
    signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
    write_message(status_fd, {'command_pid': child})
    reported = False
    while True:
        if not reported:
            try:
                waited, status = os.waitpid(child, os.WNOHANG)
            except InterruptedError:
                continue
            if waited == child:
                write_message(status_fd, {'status': status_code(status), 'completed_at_ns': time.time_ns()})
                reported = True
        readable, _, _ = select.select([control_fd], [], [], POLL)
        if readable:
            message = os.read(control_fd, 1)
            if message == b'R' and reported:
                os._exit(0)
            # Parent death or forced release: own group is still pinned by us.
            if not message or message == b'X':
                os.killpg(os.getpgrp(), signal.SIGTERM)
                time.sleep(TERM_GRACE)
                os.killpg(os.getpgrp(), signal.SIGKILL)
                os._exit(125)


class Supervisor:
    def __init__(self, mode, seconds, command, job_dir=None):
        self.mode, self.command = mode, command
        self.deadline = None if seconds is None else time.monotonic() + seconds
        self.job_dir = Path(job_dir) if job_dir else None
        self.report_dir = Path(os.environ["OMNILANE_PROCESS_JOB_DIR"]) if os.environ.get("OMNILANE_PROCESS_JOB_DIR") else self.job_dir
        self.forwarded = None
        self.known = {}
        self.signals = []
        self.errors = []
        self.residual_unknown = False
        self.keeper_pid = None
        self.reaped = False
        self.command_status = None
        self.command_completed_ns = None
        self.ready = False
        self.buffer = b''
        self.last_published = None
        self.status_eof = False
        self.instance = "%s:%s" % (os.getpid(), time.time_ns())
        self.job_instance = self.instance if mode == "job" else os.environ.get("OMNILANE_PROCESS_JOB_INSTANCE")

    def forward(self, signum, _frame):
        if self.forwarded is None:
            self.forwarded = 128 + signum

    def observe(self):
        rows = snapshot()
        roots = {self.keeper_pid}
        roots.update(pid for pid, row in self.known.items()
                     if pid in rows and rows[pid]['started'] == row['started'])
        # The pinned group remains ours even when an unobserved grandchild
        # was reparented before this sample. Include group members directly.
        owned = set(roots)
        owned.update(pid for pid, row in rows.items() if row["pgid"] == self.keeper_pid)
        changed = True
        while changed:
            changed = False
            for pid, row in rows.items():
                if row['ppid'] in owned and pid not in owned:
                    owned.add(pid)
                    changed = True
        for pid in owned:
            row = rows.get(pid)
            if row is None:
                continue
            old = self.known.get(pid)
            if old and old['started'] != row['started']:
                if row['pgid'] != self.keeper_pid:
                    continue  # New detached lifetime is not the observed child.
                # The pinned owned group authorizes this new lifetime; discard
                # any handle for the older process rather than losing the row.
            self.known[pid] = row
        live = [row for pid, row in self.known.items()
                if pid != self.keeper_pid and pid in rows
                and rows[pid]['started'] == row['started'] and is_live(rows[pid])]
        if self.job_dir:
            state = {'schema_version': 1, 'instance': self.instance, 'supervisor': rows[os.getpid()],
                     'keeper_pid': self.keeper_pid, 'dispatcher': rows.get(os.getppid()),
                     'processes': list(self.known.values())}
            encoded = json.dumps(state, sort_keys=True)
            if encoded != self.last_published:
                atomic_json(self.job_dir / 'process-state.json', state)
                self.last_published = encoded
        return live

    def read_status(self):
        while True:
            try:
                part = os.read(self.status_fd, 65536)
            except BlockingIOError:
                break
            if not part:
                self.status_eof = True
                break
            self.buffer += part
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            message = json.loads(line)
            self.ready = self.ready or message.get('ready', False)
            if 'status' in message:
                self.command_status = message['status']
                self.command_completed_ns = message.get('completed_at_ns')

    def requested(self):
        if self.job_dir is None or not (self.job_dir / 'cancel-request.json').exists():
            return None
        request = load_json(self.job_dir / 'cancel-request.json')
        code = request.get('exit_code', 143)
        return code if code in (129, 130, 143) else 143

    def signal_tree(self, signum, live):
        label = signal.Signals(signum).name
        if not self.reaped:
            try:
                # Sole wait ownership pins this PGID even if keeper is a zombie.
                os.killpg(self.keeper_pid, signum)
                self.signals.append(dict(signal=label, pgid=self.keeper_pid, sent=True))
            except ProcessLookupError:
                self.signals.append(dict(signal=label, pgid=self.keeper_pid, sent=False,
                                         reason='group absent'))
            except OSError as error:
                self.errors.append(str(error))
                self.signals.append(dict(signal=label, pgid=self.keeper_pid, sent=False,
                                         reason=str(error)))
        # Escaped processes are reporting-only on every platform. The owned
        # pinned group is the only group this supervisor signals.

    def unconfirmed_survivors(self):
        self.residual_unknown = True
        return [dict(row, liveness_unconfirmed=True) for pid, row in self.known.items()
                if pid != self.keeper_pid and is_live(row)]

    def cleanup(self, reason):
        live = []
        try:
            live = self.observe()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.errors.append(str(error))
            live = self.unconfirmed_survivors()
        if live or reason != 'completed' or self.errors:
            self.signal_tree(signal.SIGTERM, live)
            deadline = time.monotonic() + (2.0 if self.mode == "job" else TERM_GRACE)
            while time.monotonic() < deadline:
                self.read_status()
                try:
                    live = self.observe()
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    self.errors.append(str(error))
                    live = self.unconfirmed_survivors()
                    break
                if not live:
                    break
                time.sleep(POLL)
            if live or self.errors:
                self.signal_tree(signal.SIGKILL, live)
                deadline = time.monotonic() + KILL_GRACE
                while time.monotonic() < deadline:
                    try:
                        live = self.observe()
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        self.errors.append(str(error))
                        live = self.unconfirmed_survivors()
                        break
                    if not live:
                        break
                    time.sleep(POLL)
        # Capture a provider's graceful completion even if its runner shell
        # was terminated and cannot publish a completion marker itself.
        self.read_status()
        # A final child can be a zombie before the keeper has sampled waitpid.
        # Give that sole reaper one bounded reporting window; otherwise a
        # genuinely graceful completion could lose its evidence nondeterministically.
        completion_deadline = time.monotonic() + 0.25
        while self.command_status is None and not self.status_eof and time.monotonic() < completion_deadline:
            time.sleep(POLL)
            self.read_status()
        if self.mode == 'call' and self.report_dir and self.command_status == 0:
            try:
                from job_cancel import record_completion
                record_completion(self.report_dir, 0, completed_at_ns=self.command_completed_ns)
            except (OSError, ValueError) as error:
                self.errors.append('could not record provider completion: ' + str(error))
        # Last group signal is above. Release and reap keeper only now.
        try:
            os.write(self.control_fd, b'R' if self.command_status is not None else b'X')
        except (BrokenPipeError, OSError):
            pass
        end = time.monotonic() + KILL_GRACE
        while time.monotonic() < end:
            try:
                waited, _ = os.waitpid(self.keeper_pid, os.WNOHANG)
            except InterruptedError:
                continue
            if waited:
                self.reaped = True
                break
            time.sleep(POLL)
        if not self.reaped:
            # Child still belongs to this sole reaper; safe final direct signal.
            try:
                os.kill(self.keeper_pid, signal.SIGKILL)
                self.signals.append(dict(signal='SIGKILL', pid=self.keeper_pid,
                                         sent=True, via='owned-child'))
            except OSError as error:
                self.errors.append(str(error))
                self.signals.append(dict(signal='SIGKILL', pid=self.keeper_pid,
                                         sent=False, via='owned-child', reason=str(error)))
            end = time.monotonic() + KILL_GRACE
            while time.monotonic() < end:
                try:
                    waited, _ = os.waitpid(self.keeper_pid, os.WNOHANG)
                except InterruptedError:
                    continue
                if waited:
                    self.reaped = True
                    break
                time.sleep(POLL)
        if not self.reaped:
            self.errors.append('keeper could not be reaped within deadline')
        return dict(complete=not live and not self.errors and self.reaped,
                    reason=reason, signals=self.signals, survivors=live,
                    errors=list(dict.fromkeys(self.errors)),
                    command_exit=self.command_status,
                    residual_unknown=self.residual_unknown)

    def run(self):
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for sig in FORWARD_SIGNALS:
            signal.signal(sig, self.forward)
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, FORWARD_SIGNALS)
        setup_fds = []
        try:
            read_status, write_status = os.pipe()
            setup_fds.extend((read_status, write_status))
            read_control, write_control = os.pipe()
            setup_fds.extend((read_control, write_control))
            self.keeper_pid = os.fork()
        except OSError as error:
            for fd in setup_fds:
                os.close(fd)
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            print('omnilane: supervisor could not start: ' + str(error), file=sys.stderr)
            return 125
        if self.keeper_pid == 0:
            if self.mode == 'job':
                os.environ['OMNILANE_PROCESS_JOB_INSTANCE'] = self.instance
            os.environ['OMNILANE_PROCESS_OWNED'] = '1'
            os.close(read_status)
            os.close(write_control)
            try:
                keeper(self.command, write_status, read_control, old_mask)
            except BaseException as error:
                print('omnilane: group keeper failed: %s' % error, file=sys.stderr)
                os._exit(125)
        self.status_fd, self.control_fd = read_status, write_control
        try:
            os.close(write_status)
            os.close(read_control)
            os.set_blocking(read_status, False)
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        except OSError as error:
            # No G has been sent: the child cannot have started vendor code.
            os.kill(self.keeper_pid, signal.SIGKILL)
            end = time.monotonic() + KILL_GRACE
            while time.monotonic() < end:
                try:
                    waited, _ = os.waitpid(self.keeper_pid, os.WNOHANG)
                except InterruptedError:
                    continue
                if waited:
                    break
                time.sleep(POLL)
            for fd in (read_status, write_status, read_control, write_control):
                try:
                    os.close(fd)
                except OSError:
                    pass
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            print('omnilane: supervisor setup failed: ' + str(error), file=sys.stderr)
            return 125
        launched = False
        reason, rc = 'completed', 125
        try:
            while True:
                self.read_status()
                self.observe()
                if self.status_eof and self.command_status is None:
                    raise OSError("keeper exited without command completion")
                requested = self.requested()
                if self.forwarded is not None or requested:
                    reason, rc = 'cancelled', self.forwarded or requested
                    break
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    reason, rc = 'timeout', 142 if self.mode == 'call' else 124
                    break
                if self.command_status is not None:
                    rc = self.command_status
                    break
                if self.ready and not launched:
                    os.write(write_control, b'G')
                    launched = True
                time.sleep(POLL)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.errors.append(str(error))
            reason, rc = 'supervision-error', 125
        result = self.cleanup(reason)
        if not result['complete']:
            print('omnilane: cleanup incomplete: ' + json.dumps(result, sort_keys=True), file=sys.stderr)
            rc = 125
        # An inner call may observe an escape that was too short-lived in the
        # outer ancestry snapshot. Preserve its explicit incomplete evidence;
        # imported records are reporting data, never signal authorization.
        if self.job_dir:
            for path in self.job_dir.glob('call-cleanup.*.json'):
                try:
                    inner = validate_cleanup(load_json(path))
                    if inner.get('job_instance') != self.instance:
                        raise ValueError('per-call evidence belongs to another job instance')
                    rows = inner.get('survivors')
                    if not isinstance(rows, list) or any(not valid_survivor(row) for row in rows):
                        raise ValueError('invalid per-call survivor evidence')
                except (OSError, ValueError):
                    result['errors'].append('invalid per-call cleanup evidence')
                    result['complete'] = False
                    continue
                if inner.get('complete') is False:
                    result['complete'] = False
                    result['errors'].append('per-call cleanup was incomplete')
                    if inner.get('residual_unknown') is True:
                        result['residual_unknown'] = True
                    identities = {(row['pid'], row['started']) for row in result['survivors']}
                    for row in inner.get('survivors', []):
                        key = (row.get('pid'), row.get('started'))
                        if key not in identities:
                            result['survivors'].append(row)
                            identities.add(key)
            if not result['complete']:
                rc = 125
        result['schema_version'] = 1
        result['job_instance'] = self.job_instance
        result['exit_code'] = rc
        result['instance'] = self.instance
        if self.mode == 'call' and self.report_dir and not result['complete']:
            atomic_json(self.report_dir / ('call-cleanup.%s.json' % self.instance.replace(':', '-')), result)
        if self.job_dir:
            atomic_json(self.job_dir / 'process-cleanup.json', result)
        os.close(read_status)
        os.close(write_control)
        return rc


def read_code_file(path):
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4:
                return None
            raw = stream.read(5)
        value = raw[:-1] if raw.endswith(b'\n') else raw
        if value.isdigit() and str(int(value)).encode() == value and int(value) <= 255:
            return int(value)
    except (OSError, ValueError):
        pass
    return None


def read_exit(directory):
    return read_code_file(Path(directory) / 'exit')


def finalized(directory):
    recorded = read_exit(directory)
    return recorded is not None and read_code_file(Path(directory) / 'finalized') == recorded


def validate_cleanup(value, instance=None):
    # A single schema predicate is shared with public status/result readers.
    # Normal script execution resolves the sibling directly; runpy/importlib
    # test launchers may not have this directory on sys.path.
    try:
        from job_cancel import valid_cleanup_record
    except ModuleNotFoundError:
        import importlib.util
        spec = importlib.util.spec_from_file_location('_omnilane_cleanup_schema', Path(__file__).with_name('job_cancel.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        valid_cleanup_record = module.valid_cleanup_record
    if not valid_cleanup_record(value):
        raise ValueError('invalid cleanup evidence schema')
    if instance is not None and value['instance'] != instance:
        raise ValueError('cleanup evidence belongs to another job instance')
    return value


def cancel(directory):
    """Request cancellation and wait for cleanup plus dispatch finalization."""
    directory = Path(directory)
    state = None
    try:
        state = load_json(directory / 'process-state.json')
    except (OSError, ValueError):
        pass
    instance = state.get('instance') if isinstance(state, dict) else None
    if state is None and not (directory / 'process-cleanup.json').exists() and not (directory / 'process-supervision-version').exists():
        # Historical jobs predate tracked supervision. Completed/dead records
        # retain their outcome; metadata absence is not a new cleanup failure.
        if (directory / 'exit').exists() or (directory / 'exit').is_symlink():
            recorded = read_exit(directory)
            return dict(complete=True, already_finished=True, legacy=True,
                        invalid_exit=recorded is None, exit_code=recorded,
                        signals=[], survivors=[])
        try:
            raw = (directory / 'pid').read_text().strip()
            pid = int(raw) if raw.isdigit() else 0
            live = pid > 0 and is_live(snapshot().get(pid))
        except (OSError, ValueError, subprocess.SubprocessError):
            live = False
        if not live:
            return dict(complete=True, not_running=True, legacy=True,
                        exit_code=None, signals=[], survivors=[])
        return dict(complete=False, reason='legacy live worker has no trusted ownership record',
                    exit_code=125, signals=[], survivors=[])
    existing = None
    try:
        existing = validate_cleanup(load_json(directory / 'process-cleanup.json'), instance)
    except (OSError, ValueError):
        pass
    if existing and existing.get('instance') == instance and read_exit(directory) is not None and finalized(directory):
        return dict(existing, already_finished=existing.get('complete') is True)
    atomic_json(directory / 'cancel-request.json',
                {'requested_at': time.time(), 'instance': instance})
    end = time.monotonic() + 8.0
    last = None
    while time.monotonic() < end:
        try:
            candidate = validate_cleanup(load_json(directory / 'process-cleanup.json'), instance)
            if candidate.get('instance') == instance or (instance is None and load_json(directory / 'process-state.json').get('instance') == candidate['instance']):
                last = candidate
                # Exit/inbox are published by dispatch after supervisor cleanup.
                # Waiting for the worker to disappear also allows inbox finalization.
                recorded = read_exit(directory)
                if recorded is not None and finalized(directory):
                    result = dict(last)
                    result['exit_code'] = recorded
                    return result
        except (OSError, ValueError):
            pass
        time.sleep(POLL)
    # Lost/stopped supervisor ownership is never reconstructed from a bare PID.
    # Keep the failure explicit, including every still-matching observed process.
    survivors = []
    errors = []
    try:
        state = load_json(directory / 'process-state.json')
        rows = snapshot()
        survivors = [row for row in state.get('processes', [])
                     if row.get('pid') in rows and rows[row['pid']]['started'] == row.get('started')
                     and is_live(rows[row['pid']])]
        dispatcher = state.get('dispatcher')
        if isinstance(dispatcher, dict):
            pid = dispatcher.get('pid')
            if pid in rows and rows[pid]['started'] == dispatcher.get('started') and is_live(rows[pid]):
                reporting = {pid}
                changed = True
                while changed:
                    changed = False
                    for child, row in rows.items():
                        if child not in reporting and row['ppid'] in reporting and is_live(row):
                            reporting.add(child)
                            changed = True
                identities = {(row['pid'], row['started']) for row in survivors}
                survivors += [rows[pid] for pid in reporting
                              if (pid, rows[pid]['started']) not in identities]

    except (OSError, ValueError, subprocess.SubprocessError) as error:
        errors.append(str(error))
        if isinstance(state, dict):
            survivors = [dict(row, liveness_unconfirmed=True) for row in state.get('processes', [])
                         if valid_survivor(row)]
    return dict(complete=False, reason='supervisor or finalization did not confirm cleanup',
                residual_unknown=bool(errors),
                signals=(last or {}).get('signals', []), survivors=survivors,
                errors=errors, exit_code=125)


def main(argv):
    if len(argv) == 3 and argv[0] == 'request':
        if argv[2] not in ('129', '130', '143'):
            return 2
        atomic_json(Path(argv[1]) / 'cancel-request.json',
                    {'requested_at': time.time(), 'exit_code': int(argv[2])})
        return 0
    if len(argv) < 3 or argv[0] not in ('call', 'job'):
        print('usage: process_tree.py call|job SECONDS|--no-deadline COMMAND [ARG...]', file=sys.stderr)
        return 2
    mode, value, *command = argv
    if value == '--no-deadline' and mode == 'job':
        seconds = None
    elif value.isascii() and value.isdigit() and value[0] != '0' and (mode != 'job' or len(value) <= 9):
        seconds = int(value)
    else:
        return 2
    directory = os.environ.get('OMNILANE_PROCESS_JOB_DIR') if mode == 'job' else None
    return Supervisor(mode, seconds, command, directory).run()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
