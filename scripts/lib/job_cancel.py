#!/usr/bin/env python3
"""Cancellation audit and result provenance; process ownership lives in process_tree."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, value):
    # Replacement never follows an existing destination symlink.
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, separators=(',', ':'), ensure_ascii=True)
            handle.write('\n')
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def fingerprint(path):
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024 * 1024:
                return None
            # Cleanup must not chase a growing writer to EOF or hash arbitrary
            # amounts of output. Unknown provenance never prevents retrieval.
            deadline = time.monotonic() + 0.25
            remaining = before.st_size
            digest = hashlib.sha256()
            while remaining:
                if time.monotonic() >= deadline:
                    return None
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    return None
                digest.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return {'device': after.st_dev, 'inode': after.st_ino, 'size': after.st_size,
                'mtime_ns': after.st_mtime_ns, 'sha256': digest.hexdigest()}
    except OSError:
        return None


def regular_size(path):
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(fd)
            return metadata.st_size if stat.S_ISREG(metadata.st_mode) else None
        finally:
            os.close(fd)
    except OSError:
        return None


def record_completion(directory, rc, completed_at_ns=None):
    # Preserve the first successful observation, including against a later
    # successful wrapper. Cleanup/metadata publication time is not completion
    # time. The keeper passes its wall-clock wait observation when available.
    prior = read_json(directory / 'runner-completion.json')
    if completed_at_ns is None:
        completed_at_ns = time.time_ns()
    if type(completed_at_ns) is not int or completed_at_ns < 0:
        raise ValueError('invalid completion observation time')
    prior_time = prior.get('completed_at_ns')
    if prior.get('runner_exit_code') == 0 and (rc != 0 or
            (type(prior_time) is int and prior_time <= completed_at_ns)):
        return
    outputs = {}
    for name in ('out.txt', 'out.txt.tmp'):
        value = fingerprint(directory / name)
        if value is not None:
            outputs[name] = value
    atomic_json(directory / 'runner-completion.json', {
        'schema_version': 1, 'completed_at': datetime.fromtimestamp(completed_at_ns / 1_000_000_000, timezone.utc).isoformat(),
        'completed_at_ns': completed_at_ns,
        'runner_exit_code': rc, 'outputs': outputs})


def recorded_exit(directory):
    path = directory / 'exit'
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4:
            return None
        value = path.read_text()
        digits = value[:-1] if value.endswith('\n') else value
        if digits.isascii() and digits.isdigit() and str(int(digits)) == digits and int(digits) <= 255:
            return int(digits)
    except (OSError, ValueError):
        pass
    return None


def caller_identity():
    # The account is known; the upstream human/model identity usually is not.
    value = {'uid': os.getuid(), 'euid': os.geteuid(), 'origin': 'unknown',
             'identity_source': 'operating_system', 'pid': os.getpid()}
    try:
        value['username'] = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        value['username'] = None
    return value


def cancel_engine(directory):
    from process_tree import cancel
    return cancel(directory)


def cancel_job(directory):
    fd = os.open(str(directory / 'cancel.lock'), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('unsafe cancellation lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('cancellation incomplete: another cancellation is in progress', file=sys.stderr)
            return 1
        return _cancel_job(directory)
    finally:
        os.close(fd)


def _cancel_job(directory):
    prior = read_json(directory / 'cancel.json')
    history = prior.pop('attempts', [])
    if not isinstance(history, list):
        history = []
    if prior:
        history.append(prior)
    audit = {'schema_version': 1, 'requested_at': timestamp(), 'requested_at_ns': time.time_ns(),
             'caller': caller_identity(), 'status': 'in_progress', 'signals': [],
             'final_exit_code': recorded_exit(directory), 'survivors': [], 'attempts': history}
    atomic_json(directory / 'cancel.json', audit)
    try:
        result = cancel_engine(directory)
    except Exception as error:
        result = {'complete': False, 'signals': [], 'survivors': [],
                  'reason': 'cleanup failed: ' + str(error), 'residual_unknown': True}
    audit.update(status='complete' if result.get('complete') is True else 'incomplete',
                 completed_at=timestamp(), signals=result.get('signals', []),
                 survivors=safe_survivors(result.get('survivors', [])), reason=result.get('reason', ''),
                 final_exit_code=recorded_exit(directory),
                 residual_unknown=result.get('residual_unknown') is True or type(result.get('complete')) is not bool)
    if result.get('complete') is True and result.get('already_finished'):
        audit['status'] = 'already_finished'
    if result.get('complete') is True and result.get('not_running'):
        audit['status'] = 'not_running'
    atomic_json(directory / 'cancel.json', audit)
    if result.get('complete') is not True:
        print('cancellation incomplete; ' + residual_notice(dict(audit, residual_count=len(audit['survivors']))) +
              ' (manual cleanup may be required)', file=sys.stderr)
        for row in audit['survivors']:
            print('  pid={pid} started={started} command={command}'.format(**row), file=sys.stderr)
        return 125
    if result.get('not_running'):
        print('not running (no live worker to cancel)')
        return 0
    if result.get('invalid_exit'):
        print('already finished (invalid exit metadata)')
        return 0
    rc = audit['final_exit_code']
    label = 'already finished' if audit['status'] == 'already_finished' else 'cancelled'
    print(label + ' (exit ' + (str(rc) if rc is not None else 'unknown') + ')')
    return 0


def safe_survivors(rows):
    result = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict) or type(row.get('pid')) is not int or row['pid'] <= 0:
            continue
        # Only command names, never argv. The engine already records comm;
        # defense in depth also strips parameters from malformed local metadata.
        command = str(row.get('command', 'unknown')).split()
        command = os.path.basename(command[0]) if command else 'unknown'
        command = ''.join(ch if ch.isprintable() else '?' for ch in command)[:128]
        result.append({'pid': row['pid'], 'started': ''.join(ch if ch.isprintable() else '?' for ch in str(row.get('started', 'unknown')))[:256],
                       'command': command})
        if type(row.get('liveness_unconfirmed')) is bool:
            result[-1]['liveness_unconfirmed'] = row['liveness_unconfirmed']
    return result


def valid_cleanup_record(value):
    if (type(value.get('schema_version')) is not int or value['schema_version'] != 1 or
            type(value.get('complete')) is not bool or not isinstance(value.get('survivors'), list) or
            not isinstance(value.get('instance'), str) or not value['instance'] or
            not isinstance(value.get('signals'), list) or
            type(value.get('residual_unknown', False)) is not bool):
        return False
    for entry in value['signals']:
        if (not isinstance(entry, dict) or not set(entry).issubset({'signal', 'pid', 'pgid', 'sent', 'reason', 'via'})
                or entry.get('signal') not in ('SIGTERM', 'SIGKILL', 'SIGHUP', 'SIGINT')
                or type(entry.get('sent')) is not bool):
            return False
    for row in value['survivors']:
        if (not isinstance(row, dict) or type(row.get('pid')) is not int or row['pid'] <= 0 or
                not isinstance(row.get('started'), str) or not row['started'] or
                not isinstance(row.get('command'), str) or not row['command'] or
                type(row.get('liveness_unconfirmed', False)) is not bool):
            return False
    return not (value['complete'] and (value['survivors'] or value.get('residual_unknown')))


def incomplete_cleanup(directory):
    path = directory / 'process-cleanup.json'
    cleanup = read_json(path)
    malformed = (path.exists() or path.is_symlink()) and not valid_cleanup_record(cleanup)
    audit = read_json(directory / 'cancel.json')
    failure = directory / 'finalization-error'
    publication_failed = failure.is_file() and not failure.is_symlink()
    if not malformed and cleanup.get('complete') is not False and audit.get('status') != 'incomplete' and not publication_failed:
        return None
    source = cleanup if malformed or cleanup.get('complete') is False else audit
    survivors = safe_survivors(source.get('survivors', []))
    unknown = malformed or source.get('residual_unknown') is True
    command_exit = source.get('command_exit', recorded_exit(directory))
    if type(command_exit) is not int or not 0 <= command_exit <= 255:
        command_exit = None
    result = {'complete': False, 'residual_count': len(survivors), 'survivors': survivors,
              'reason': 'invalid cleanup metadata' if malformed else
                        'completion publication failed' if publication_failed else 'cleanup not confirmed',
              'command_exit': command_exit}
    if unknown:
        result['residual_unknown'] = True
        result['reason'] += '; residual unknown; last observed ' + str(len(survivors))
        for row in survivors:
            row['liveness_unconfirmed'] = True
    return result


def residual_notice(cleanup):
    if cleanup.get('residual_unknown'):
        return 'residual unknown; last observed ' + str(cleanup['residual_count'])
    return 'residual ' + str(cleanup['residual_count'])


def cleanup_notice(cleanup):
    return ('cleanup incomplete; ' + residual_notice(cleanup) +
            ' (' + cleanup['reason'] + '; manual cleanup may be required)')


def emit_incomplete_status(directory, job_id, json_mode):
    cleanup = incomplete_cleanup(directory)
    if not cleanup:
        return 1
    if json_mode:
        print(json.dumps({'schema_version': 1, 'command': 'status', 'ok': True,
                          'job': {'id': job_id, 'state': 'incomplete', 'exit_code': 125, 'cleanup': cleanup}}))
    else:
        print('incomplete exit=125; ' + cleanup_notice(cleanup))
        for row in cleanup['survivors']:
            print('  pid={pid} started={started} command={command}'.format(**row))
    return 0


def result_provenance(directory, rc):
    cancellation = read_json(directory / 'cancel.json')
    cancelled = (bool(cancellation) and cancellation.get('status') not in ('already_finished', 'not_running')) or rc in (137, 143)
    interrupted = cancelled or incomplete_cleanup(directory) is not None
    source = 'out.txt'
    temporary = regular_size(directory / 'out.txt.tmp') if interrupted else None
    if temporary is not None and temporary > 0:
        source = 'out.txt.tmp'
    if not interrupted:
        return source, None
    current = fingerprint(directory / source)
    status = 'partial_or_unknown'
    marker = read_json(directory / 'runner-completion.json')
    requested = cancellation.get('requested_at_ns')
    attempts = cancellation.get('attempts', [])
    if isinstance(attempts, list):
        times = [entry.get('requested_at_ns') for entry in attempts if isinstance(entry, dict)]
        times += [requested]
        known = [value for value in times if isinstance(value, int)]
        requested = min(known) if known else None
    if requested is None:
        request = read_json(directory / 'cancel-request.json')
        seconds = request.get('requested_at')
        if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
            requested = int(seconds * 1_000_000_000)
    if requested is None:
        try:
            requested = (directory / 'exit').stat().st_mtime_ns
        except OSError:
            requested = None
    completed = marker.get('completed_at_ns')
    if (current is not None and marker.get('runner_exit_code') == 0 and
            isinstance(requested, int) and isinstance(completed, int) and completed > requested and
            current['mtime_ns'] > requested and
            isinstance(marker.get('outputs'), dict) and
            any(marker['outputs'].get(name) == current for name in ('out.txt', 'out.txt.tmp'))):
        status = 'completed_after_cancel'
    return source, {'status': status, 'source': source, 'cancelled': cancelled,
                    'completion_confirmed': status == 'completed_after_cancel'}


def emit_result(directory, job_id, rc, json_mode):
    cleanup = incomplete_cleanup(directory)
    if cleanup:
        rc = 125
    source, provenance = result_provenance(directory, rc)
    output = directory / source
    error = directory / 'out.txt.stderr.log'
    available = output.is_file() and not output.is_symlink()
    error_available = error.is_file() and not error.is_symlink() and error.stat().st_size > 0
    if json_mode:
        job = {'id': job_id, 'state': 'done', 'exit_code': rc,
               'output_available': available, 'stderr_available': error_available}
        if cleanup:
            job.update(state='incomplete', cleanup=cleanup)
        if provenance:
            job['result_provenance'] = provenance
        print(json.dumps({'schema_version': 1, 'command': 'result', 'ok': True, 'job': job}))
    else:
        if cleanup:
            print('omnilane: ' + cleanup_notice(cleanup), file=sys.stderr)
            for row in cleanup['survivors']:
                print('  pid={pid} started={started} command={command}'.format(**row), file=sys.stderr)
        if provenance and available:
            if provenance['completion_confirmed']:
                print('omnilane: result completed after cancellation; cancellation exit is preserved (' + source + ')', file=sys.stderr)
            else:
                print('omnilane: interrupted-job output is partial or unknown; completion is not confirmed (' + source + ')', file=sys.stderr)
        for path, stream, present in ((output, sys.stdout.buffer, available), (error, sys.stderr.buffer, error_available)):
            if not present:
                continue
            fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    continue
                if path == error:
                    sys.stderr.write('--- stderr ---\n')
                    sys.stderr.flush()
                for chunk in iter(lambda: handle.read(65536), b''):
                    stream.write(chunk)
                stream.flush()
    return rc


def main():
    command, directory = sys.argv[1:3]
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('unsafe job directory')
    if command == 'state':
        cleanup = incomplete_cleanup(directory)
        if cleanup:
            print(residual_notice(cleanup).removeprefix('residual ') + '\t' + json.dumps(cleanup, separators=(',', ':')))
        return 0
    if command == 'status':
        return emit_incomplete_status(directory, sys.argv[3], sys.argv[4] == '1')
    if command == 'cancel':
        return cancel_job(directory)
    if command == 'record-completion':
        record_completion(directory, int(sys.argv[3]))
        return 0
    if command == 'result':
        return emit_result(directory, sys.argv[3], int(sys.argv[4]), sys.argv[5] == '1')
    raise ValueError('unknown command')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('omnilane: ' + str(error), file=sys.stderr)
        sys.exit(1)
