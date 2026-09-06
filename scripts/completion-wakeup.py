#!/usr/bin/env python3
"""Caller-owned Codex heartbeat receipts; never invokes a model or scheduler.

Only CLI job metadata and exit markers are observed. Private outputs, prompts,
native state, inbox tails and Codex session files are deliberately not read.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time

LIMIT = 128 * 1024
IDENTITY = ('lane', 'vendor', 'model', 'mode', 'workdir', 'foreman_session', 'started')


class Invalid(ValueError):
    pass


def check(value, message):
    if not value:
        raise Invalid(message)


def identifier(value):
    check(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}', value),
          'invalid identifier')
    return value


def no_links(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        check(not part.is_symlink(), 'symlink path rejected')
    return path


def read_bytes(path, limit=LIMIT):
    path = no_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        check(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), 'invalid record owner/type')
        data = stream.read(limit + 1)
        check(len(data) <= limit, 'record too large')
        return data


def read_json(path):
    value = json.loads(read_bytes(path))
    check(isinstance(value, dict), 'expected JSON object')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode()
    check(len(raw) <= LIMIT, 'registry too large')
    no_links(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def registry_lock(args):
    home = no_links(args.home)
    if args.action == 'prepare':
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
    check(home.is_dir(), 'home missing')
    root = no_links(home / 'wakeups')
    if args.action == 'prepare':
        root.mkdir(mode=0o700, exist_ok=True)
    check(root.is_dir(), 'registry not prepared')
    info = root.stat()
    check(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'wakeup store must be owner-only')
    key = digest([args.host_id, args.thread_id])[:32]
    path = root / (key + '.json')
    fd = os.open(root / (key + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        check(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), 'invalid lock')
        # Advisory kernel locks automatically release on crash; no stale PID cleanup.
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path


def identity(args, job, workdir, claims=None, permit_adoption=False):
    identifier(job)
    path = no_links(Path(args.home) / 'jobs' / job)
    check(path.is_dir(), 'job missing')
    check(not os.path.lexists(path / 'native.json'), 'unsupported-native: use native completion channel')
    meta = read_json(path / 'meta.json')
    check(all(isinstance(meta.get(k), str) for k in IDENTITY), 'invalid job metadata')
    actual = no_links(meta['workdir'])
    check(actual.is_dir() and str(actual.resolve()) == workdir, 'job workdir mismatch')
    public = {k: meta[k] for k in IDENTITY}
    public['workdir'] = workdir
    # This is a Claude process/session binding, not a Codex app thread ID.
    frozen = digest(public)
    if meta['foreman_session']:
        claims = {} if claims is None else claims
        claim = claims.get(job)
        if claim is None and permit_adoption and args.adoption_evidence:
            receipt = read_json(args.adoption_evidence)
            check(receipt.get('source') == 'operator_adoption'
                  and receipt.get('operator_confirmed') is True
                  and receipt.get('controller_thread_id') == args.thread_id
                  and receipt.get('controller_host_id') == args.host_id
                  and receipt.get('run_id') == args.run_id
                  and isinstance(receipt.get('job_identity_sha256'), dict)
                  and receipt['job_identity_sha256'].get(job) == frozen,
                  'adoption receipt binding/identity mismatch')
            claim = {'identity_sha256': frozen, 'receipt_path': str(no_links(args.adoption_evidence)),
                     'receipt_sha256': digest(receipt), 'trust': 'operator adoption attested by caller'}
            claims[job] = claim
        # Reveal only public job id + fingerprint, not the original session value.
        check(isinstance(claim, dict) and claim.get('identity_sha256') == frozen,
              'adoption_required:' + job + ':' + frozen)
    # Freeze immutable identity, not mutable completion/provenance fields.
    return frozen


def assert_binding(state, args):
    check(state.get('schema_version') == 1, 'unsupported registry schema')
    check(state.get('controller_thread_id') == args.thread_id
          and state.get('controller_host_id') == args.host_id
          and state.get('run_id') == args.run_id, 'controller binding mismatch')
    check(state.get('home') == str(no_links(args.home)), 'home binding mismatch')
    check(state.get('status') in ('pending', 'registered', 'closed'), 'invalid registry status')


def summary(state, path):
    return {'schema_version': 1, 'registry': str(path), 'status': state['status'],
            'automation_id': state.get('automation_id'), 'tracked_jobs': sorted(state['jobs'])}


def command_prefix(args):
    import shlex
    return ' '.join(shlex.quote(x) for x in [sys.executable, str(Path(__file__).resolve()), 'poll',
        '--home', str(no_links(args.home)), '--thread-id', args.thread_id,
        '--host-id', args.host_id, '--run-id', args.run_id])


def assert_unused_historical_run(args, path):
    """A stopped heartbeat must never become valid again through name reuse."""
    history = no_links(path.parent / 'history')
    if not history.exists():
        return
    check(history.is_dir(), 'invalid history store')
    for archived in history.glob(path.stem + '-*.json'):
        old = read_json(archived)
        check(old.get('controller_thread_id') == args.thread_id
              and old.get('controller_host_id') == args.host_id, 'history binding mismatch')
        check(old.get('run_id') != args.run_id, 'historical run id cannot be reused')


def prepare(args, state, path, now):
    check(args.workdir and args.job, 'prepare requires --workdir and --job')
    work = no_links(args.workdir)
    check(work.is_dir(), 'workdir missing')
    work = str(work.resolve())
    if state is None:
        state = {'schema_version': 1, 'controller_thread_id': args.thread_id,
                 'controller_host_id': args.host_id, 'run_id': args.run_id,
                 'home': str(no_links(args.home)), 'workdir': work, 'status': 'pending',
                 'created_at': now, 'expires_at': now + args.ttl_seconds,
                 'requested_interval_seconds': args.interval_seconds, 'jobs': {}, 'events': {}}
    else:
        assert_binding(state, args)
        check(state['status'] != 'closed' and now < state['expires_at'], 'registry closed/expired')
        check(state['workdir'] == work, 'workdir binding mismatch')
    for job in args.job:
        frozen = identity(args, job, work, state.setdefault('adoptions', {}), permit_adoption=True)
        check(job not in state['jobs'] or state['jobs'][job] == frozen, 'job identity changed')
        state['jobs'][job] = frozen
    check(len(state['jobs']) <= 100, 'job limit exceeded')
    prompt = (f'檢查此任務已登錄的 omnilane 背景工作：執行 {command_prefix(args)}。'
              '沒有新事件或需處理問題時保持安靜。只處理登錄的工作，確認主控綁定後以 claim token '
              'ack-delivered，再接續原任務驗收並以證據 ack-accepted；退出碼零不是驗收通過。'
              '不要新建監看器或自動派工。needs_disable 時停用本 heartbeat，再以工具結果回填 closed。')
    tool_args = {'mode': 'update' if state.get('automation_id') else 'create', 'kind': 'heartbeat',
                 'name': 'omnilane 完成續驗', 'prompt': prompt, 'status': 'ACTIVE',
                 'targetThreadId': args.thread_id}
    if state.get('automation_id'):
        tool_args['id'] = state['automation_id']
    result = summary(state, path)
    result.update(tool='mcp__codex_app__automation_update', tool_args=tool_args,
                  schedule_request={'interval_seconds': state['requested_interval_seconds'],
                                    'instruction': '主控使用工具支援的排程格式補入 rrule；保留既有通知偏好。'},
                  registration_required=state['status'] == 'pending')
    return state, result


def automation_receipt(args, expected):
    check(args.evidence and args.automation_id, 'automation id and evidence required')
    receipt = read_json(args.evidence)
    check(receipt.get('source') == 'automation_update'
          and receipt.get('automation_id') == args.automation_id
          and receipt.get('controller_thread_id') == args.thread_id
          and receipt.get('controller_host_id') == args.host_id
          and receipt.get('status') == expected, 'automation receipt binding/status mismatch')
    return {'path': str(no_links(args.evidence)), 'sha256': digest(receipt),
            'effective_interval_seconds': receipt.get('effective_interval_seconds'),
            'trust': 'caller-attested tool result; not independent scheduler verification'}


def poll(args, state, path, now):
    result = summary(state, path)
    result.update(events=[], errors=[], needs_disable=False)
    if state['status'] == 'closed':
        return result
    if now >= state['expires_at']:
        result.update(status='expired', needs_disable=bool(state.get('automation_id')))
        return result
    if state['status'] != 'registered':
        return result
    accepted = 0
    for job, frozen in sorted(state['jobs'].items()):
        try:
            check(identity(args, job, state['workdir'], state.get('adoptions', {})) == frozen, 'binding_changed')
            marker = Path(args.home) / 'jobs' / job / 'exit'
            if not os.path.lexists(marker):
                continue
            raw = read_bytes(marker, 32).decode().strip()
            check(re.fullmatch(r'-?\d{1,5}', raw), 'invalid_exit')
            rc = int(raw)
            check(-32768 <= rc <= 32767, 'invalid_exit')
            key = digest([args.run_id, job, frozen, rc])
            # Changing an already observed terminal exit is not a fresh generation.
            prior = [k for k, e in state['events'].items() if e['job_id'] == job]
            check(not prior or prior == [key], 'terminal_changed')
            event = state['events'].setdefault(key, {'job_id': job, 'exit_code': rc,
                                                     'state': 'observed', 'observed_at': now})
            if event['state'] == 'accepted':
                accepted += 1
                continue
            if event.get('lease_until', 0) > now:
                continue
            event.update(claim_token=secrets.token_hex(16), lease_until=now + args.lease_seconds,
                         state='claimed')
            result['events'].append({'event_key': key, 'job_id': job, 'exit_code': rc,
                                     'claim_token': event['claim_token'], 'lease_until': event['lease_until']})
        except (Invalid, OSError, ValueError) as error:
            # No arbitrary metadata, logs, paths or file contents in public errors.
            code = str(error) if isinstance(error, Invalid) else 'invalid_job_record'
            result['errors'].append({'job_id': job, 'code': code})
    result['needs_disable'] = accepted == len(state['jobs']) and not result['errors']
    state['last_observed_at'] = now
    return result


def act(args):
    for value in (args.thread_id, args.host_id, args.run_id):
        identifier(value)
    with registry_lock(args) as path:
        state = read_json(path) if os.path.lexists(path) else None
        now = time.time()
        previous_run = None
        if args.action == 'prepare':
            assert_unused_historical_run(args, path)
            if state and state.get('status') == 'closed' and state.get('run_id') != args.run_id:
                # Preserve the closed run before publishing a new active binding.
                # Validate every dimension except the intentionally new run ID.
                old_run_id = state.get('run_id')
                identifier(old_run_id)
                state_for_binding = dict(state, run_id=args.run_id)
                assert_binding(state_for_binding, args)
                previous_run, state = state, None
            state, result = prepare(args, state, path, now)
        else:
            check(state is not None, 'registry not prepared')
            assert_binding(state, args)
            result = summary(state, path)
            if args.action == 'poll':
                result = poll(args, state, path, now)
            elif args.action == 'record-registration':
                check(state['status'] != 'closed' and now < state['expires_at'], 'registry closed/expired')
                identifier(args.automation_id)
                check(not state.get('automation_id') or state['automation_id'] == args.automation_id,
                      'automation id mismatch')
                receipt = automation_receipt(args, 'ACTIVE')
                state.update(status='registered', automation_id=args.automation_id,
                             registration_receipt=receipt, registered_at=now)
                result = summary(state, path)
            elif args.action == 'closed':
                check(state.get('automation_id') == args.automation_id, 'automation id mismatch')
                receipt = automation_receipt(args, 'PAUSED')
                if args.close_reason == 'completed':
                    check(len(state['events']) == len(state['jobs'])
                          and all(e['state'] == 'accepted' for e in state['events'].values()),
                          'unaccepted jobs remain; use explicit stopped/expired reason')
                if args.close_reason == 'expired':
                    check(now >= state['expires_at'], 'registry not expired')
                state.update(status='closed', closed_at=now, closure_receipt=receipt,
                             close_reason=args.close_reason)
                result = summary(state, path)
            else:
                check(state['status'] == 'registered' and now < state['expires_at'], 'inactive registry')
                event = state['events'].get(args.event_key)
                check(event and event.get('claim_token') == args.claim_token
                      and event.get('lease_until', 0) > now, 'invalid/expired claim')
                check(identity(args, event['job_id'], state['workdir'], state.get('adoptions', {}))
                      == state['jobs'][event['job_id']],
                      'binding_changed')
                current_exit = read_bytes(Path(args.home) / 'jobs' / event['job_id'] / 'exit', 32)
                check(current_exit.decode().strip() == str(event['exit_code']), 'terminal_changed')
                if args.action == 'ack-delivered':
                    check(event['state'] in ('claimed', 'delivered', 'accepted'), 'invalid delivery state')
                    if event['state'] == 'claimed':
                        event.update(state='delivered', delivered_at=now)
                else:
                    check(event['state'] in ('delivered', 'accepted'), 'delivery required before acceptance')
                    check(args.result and args.evidence, 'acceptance result/evidence required')
                    data = read_bytes(args.evidence)
                    check(data.strip(), 'empty acceptance evidence')
                    evidence = {'path': str(no_links(args.evidence)),
                                'sha256': hashlib.sha256(data).hexdigest()}
                    if event['state'] == 'accepted':
                        check(event['result'] == args.result and event['evidence'] == evidence,
                              'acceptance receipt conflict')
                    else:
                        event.update(state='accepted', accepted_at=now, result=args.result, evidence=evidence)
                result.update(event_key=args.event_key, event_state=event['state'])
        if previous_run is not None:
            history = no_links(path.parent / 'history')
            history.mkdir(mode=0o700, exist_ok=True)
            info = history.stat()
            check(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'history must be owner-only')
            archived = history / (path.stem + '-' + digest(previous_run) + '.json')
            if os.path.lexists(archived):
                check(read_json(archived) == previous_run, 'history conflict')
            else:
                write_json(archived, previous_run)
            state['previous_run_history'] = str(archived)
            result['previous_run_history'] = str(archived)
        write_json(path, state)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'record-registration', 'poll',
                                         'ack-delivered', 'ack-accepted', 'closed'])
    parser.add_argument('--home', default=os.environ.get('OMNILANE_HOME', str(Path.home() / '.omnilane')))
    for name in ('thread-id', 'host-id', 'run-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--workdir')
    parser.add_argument('--job', action='append')
    parser.add_argument('--automation-id')
    parser.add_argument('--evidence')
    parser.add_argument('--adoption-evidence')
    parser.add_argument('--event-key')
    parser.add_argument('--claim-token')
    parser.add_argument('--close-reason', choices=['completed', 'stopped', 'expired'], default='completed')
    parser.add_argument('--result', choices=['PASS', 'FAIL', 'PARTIAL', 'BLOCKED'])
    parser.add_argument('--ttl-seconds', type=int, default=86400)
    parser.add_argument('--interval-seconds', type=int, default=60)
    parser.add_argument('--lease-seconds', type=int, default=900)
    args = parser.parse_args()
    try:
        check(1 <= args.ttl_seconds <= 604800 and 30 <= args.interval_seconds <= 86400
              and 1 <= args.lease_seconds <= 86400, 'invalid time limit')
        print(json.dumps(act(args), ensure_ascii=False, sort_keys=True))
        return 0
    except (Invalid, OSError, ValueError, TypeError, KeyError) as error:
        message = str(error) if isinstance(error, Invalid) else type(error).__name__
        print(json.dumps({'error': message}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
