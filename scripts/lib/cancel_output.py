"""Evidence for ordinary CLI jobs result with a recorded 137/143 exit.

Use existing job metadata and inbox receipts. A receipt carries at most 2000
bytes of output, so it can only confirm a byte-for-byte match to small, complete
published output. Exit-file time is a conservative observation boundary, not
the time a signal was sent. Never infer success from a filename or an mtime.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import os
import re
import shutil
import stat
import sys


SECOND = 10**9
TAIL_LIMIT = 2000  # dispatch.sh completion_tail
PREVIEW_LIMIT = 8192
IDENTITY_FIELDS = ("lane", "vendor", "model", "mode", "workdir", "foreman_session")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def regular_at(directory, name):
    if directory is None:
        yield None
        return
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError:
        yield None
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            yield None
            return
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def subdir(stack, parent, name):
    if parent is None:
        return None
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    except OSError:
        return None
    stack.callback(os.close, fd)
    return fd


def stamp(info):
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def snapshot(stream, limit):
    if stream is None:
        return b"", None
    before = os.fstat(stream.fileno())
    data = stream.read(limit + 1)
    after = os.fstat(stream.fileno())
    if stamp(before) != stamp(after) or len(data) != min(before.st_size, limit + 1):
        return data, None
    return data, after


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def read_record(directory, name, limit):
    with regular_at(directory, name) as stream:
        data, info = snapshot(stream, limit)
    if info is not None and len(data) <= limit:
        try:
            item = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
            if isinstance(item, dict):
                return item, info.st_mtime_ns
        except (ValueError, RecursionError):
            pass
    return {}, None


def utc_second(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        try:
            value = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return int(value.timestamp()) * SECOND
        except (ValueError, OverflowError):
            pass
    return None


def output_status(job, inbox, job_id, code, source, data, info):
    if info is None:
        return "unknown"
    meta, _ = read_record(job, "meta.json", 4096)
    started = utc_second(meta.get("started"))
    if started is not None and info.st_mtime_ns < started:
        return "stale"
    if source == "out.txt.tmp":
        return "partial" if data else "unknown"
    if started is None or not 0 < len(data) <= TAIL_LIMIT:
        return "unknown"

    with regular_at(job, "exit") as stream:
        exit_bytes, exit_info = snapshot(stream, 4)
    if exit_info is None or exit_bytes not in (str(code).encode(), f"{code}\n".encode()):
        return "unknown"
    cancelled_ns = exit_info.st_mtime_ns

    receipt, receipt_ns = read_record(inbox, job_id + ".json", 32768)
    done = utc_second(receipt.get("finished"))
    tail = receipt.get("tail")
    if (receipt.get("job_id") != job_id or type(receipt.get("exit")) is not int or receipt["exit"] != 0
            or done is None or not isinstance(tail, str)
            or any(not isinstance(meta.get(key), str) or receipt.get(key) != meta[key]
                   for key in IDENTITY_FIELDS)
            or any(receipt.get(key) != meta.get(key) for key in ("thread", "thread_turn"))
            or tail.startswith("[truncated: leading output omitted]\n")
            or "\ufffd" in tail or "\0" in tail):
        return "unknown"
    try:
        if tail.encode("utf-8") != data:
            return "unknown"
    except UnicodeError:
        return "unknown"

    # The writer's timestamp is only precise to one second. Require coherent
    # file order as well; an equal/reordered timestamp cannot prove lateness.
    if (done < started or done > receipt_ns
            or not info.st_mtime_ns < min(done + SECOND, receipt_ns)):
        return "unknown"
    if receipt_ns < cancelled_ns:
        return "stale"
    if cancelled_ns < info.st_mtime_ns and cancelled_ns < done + SECOND:
        return "completed"
    return "unknown"


def temporary_preview(raw):
    # The new preview is deliberately text-only; do not replay terminal control
    # sequences. Cap actual emitted bytes too (UTF-8 substitution can grow it).
    preview = "".join(ch if ch in "\n\t" or ch.isprintable() else "?"
                      for ch in raw[:PREVIEW_LIMIT].decode("utf-8", "replace"))
    encoded = preview.encode("utf-8")
    return (encoded[:PREVIEW_LIMIT].decode("utf-8", "ignore").encode("utf-8"),
            len(raw) > PREVIEW_LIMIT or len(encoded) > PREVIEW_LIMIT)


def render(home, job_id, code, json_mode):
    with ExitStack() as stack:
        # The configured home may be a symlink. Pin it and do not follow any
        # links from it to the job, its artifacts, or the inbox receipt.
        try:
            root = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            root = None
        else:
            stack.callback(os.close, root)
        jobs = subdir(stack, root, "jobs")
        job = subdir(stack, jobs, job_id)
        formal = stack.enter_context(regular_at(job, "out.txt"))
        err = stack.enter_context(regular_at(job, "out.txt.stderr.log"))
        stderr_available = err is not None and os.fstat(err.fileno()).st_size > 0
        inbox = None
        preview = b""
        truncated = False
        if formal is not None:
            source = "out.txt"
            raw, info = snapshot(formal, TAIL_LIMIT)
            inbox = subdir(stack, root, "inbox")
        else:
            source = None
            raw, info = b"", None
            with regular_at(job, "out.txt.tmp") as temp:
                if temp is not None:
                    source = "out.txt.tmp"
                    raw, info = snapshot(temp, PREVIEW_LIMIT)
                    preview, truncated = temporary_preview(raw)
        status = output_status(job, inbox, job_id, code, source, raw, info)
        if json_mode:
            print(json.dumps(dict(schema_version=1, command="result", ok=True, job=dict(
                id=job_id, state="done", exit_code=code, output_available=formal is not None,
                stderr_available=stderr_available, cancelled=True, output_provenance=dict(
                    status=status, source=source, preview_truncated=truncated)))))
            return
        label = f"output provenance: {status}"
        if source is not None:
            label += f" ({source}"
            if source == "out.txt.tmp":
                label += ", temporary preview" + (", truncated" if truncated else "")
            label += ")"
        print(f"omnilane: cancelled/interrupted (exit {code}); {label}", file=sys.stderr)
        if formal is not None:
            # Emit the exact bytes checked for completed; don't reread them.
            sys.stdout.buffer.write(raw)
            if status != "completed":
                shutil.copyfileobj(formal, sys.stdout.buffer)
        elif preview:
            sys.stdout.buffer.write(preview)
        if stderr_available:
            sys.stderr.buffer.write(b"--- stderr ---\n")
            shutil.copyfileobj(err, sys.stderr.buffer)


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "1")
