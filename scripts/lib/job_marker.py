#!/usr/bin/env python3
"""List processes that belong to one supervised job by an exact environment entry.

    job_marker.py scan --token TOKEN --supervisor PID --after MICROSECONDS

Prints "PID START" per match, START being the process start time in microseconds
(macOS: since the epoch; Linux: since boot, at clock-tick resolution). Exit 0 with no lines when nothing matches; exit 3 when processes
cannot be listed.

A process matches only when its environment, read as separate NUL-terminated
entries (never from command text), contains exactly OMNILANE_JOB_TOKEN=TOKEN, it
started strictly after the job began (macOS: after --after, the supervisor's clock
reading taken before it forked the worker, because an exec through a launcher such
as /usr/bin/perl can report a later start time for the supervisor itself; Linux:
after the supervisor's own start time, in clock ticks), and it
is neither the supervisor, one of
the supervisor's ancestors, nor this scan. On macOS the environment is the one
the process received at exec (KERN_PROCARGS2); a process that clears or replaces
its environment before exec carries no token and is not found.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import struct
import sys

EXIT_UNAVAILABLE = 3


class Unavailable(Exception):
    pass


def parse_procargs2(raw):
    """Environment entries from a KERN_PROCARGS2 buffer, or None when it is unusable.

    Layout: int32 argc, the executable path, NUL padding, argc NUL-terminated
    arguments, then NUL-terminated environment entries up to an empty one.
    """
    if raw is None or len(raw) < 4:
        return None
    argc = struct.unpack_from("<i", raw)[0]
    rest = raw[4:]
    end = rest.find(b"\0")
    if argc < 0 or end < 0:
        return None
    pos = end
    while pos < len(rest) and rest[pos] == 0:
        pos += 1
    parts = rest[pos:].split(b"\0")
    if len(parts) <= argc:
        return None
    env = []
    for entry in parts[argc:]:
        if not entry:
            break
        env.append(entry)
    return env


def _darwin():
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    bsdinfo = struct.Struct("<12I16s32s5Ii2Q")  # struct proc_bsdinfo, 136 bytes
    ctl_kern, kern_argmax, kern_procargs2, proc_pidtbsdinfo = 1, 8, 49, 3

    def sysctl(mib, size):
        buf = ctypes.create_string_buffer(size)
        length = ctypes.c_size_t(size)
        arr = (ctypes.c_int * len(mib))(*mib)
        if libc.sysctl(arr, len(mib), buf, ctypes.byref(length), None, ctypes.c_size_t(0)) != 0:
            return None
        return buf.raw[:length.value]

    raw = sysctl([ctl_kern, kern_argmax], 4)
    if raw is None:
        raise Unavailable("KERN_ARGMAX")
    argmax = struct.unpack("<i", raw)[0]

    def pids():
        count = libproc.proc_listallpids(None, 0)
        if count <= 0:
            raise Unavailable("proc_listallpids")
        buf = (ctypes.c_int * (count + 64))()
        count = libproc.proc_listallpids(buf, ctypes.sizeof(buf))
        if count <= 0:
            raise Unavailable("proc_listallpids")
        return [p for p in buf[:count] if p > 0]

    def info(pid):
        buf = ctypes.create_string_buffer(bsdinfo.size)
        if libproc.proc_pidinfo(pid, proc_pidtbsdinfo, ctypes.c_uint64(0), buf, bsdinfo.size) != bsdinfo.size:
            return None
        fields = bsdinfo.unpack(buf.raw)
        return fields[4], fields[20] * 1_000_000 + fields[21]  # ppid, start in microseconds

    def environment(pid):
        return parse_procargs2(sysctl([ctl_kern, kern_procargs2, pid], argmax))

    return pids, info, environment


def _linux(proc="/proc"):
    ticks = os.sysconf("SC_CLK_TCK")

    def pids():
        try:
            return [int(name) for name in os.listdir(proc) if name.isdigit()]
        except OSError as error:
            raise Unavailable(str(error))

    def info(pid):
        try:
            with open(f"{proc}/{pid}/stat", "rb") as handle:
                stat = handle.read()
        except OSError:
            return None
        fields = stat[stat.rfind(b")") + 2:].split()
        if len(fields) < 20:
            return None
        # Start time since boot, kept in clock ticks scaled to microseconds; it is only
        # compared with other processes' start times, including the supervisor's.
        return int(fields[1]), int(fields[19]) * (1_000_000 // ticks)

    def environment(pid):
        try:
            with open(f"{proc}/{pid}/environ", "rb") as handle:
                return [entry for entry in handle.read().split(b"\0") if entry]
        except OSError:
            return None

    return pids, info, environment


def scan(token: str, supervisor: int, after_us: int, source=None):
    darwin = sys.platform == "darwin"
    if source is not None:
        pids, info, environment = source
    elif darwin:
        pids, info, environment = _darwin()
    elif sys.platform.startswith("linux"):
        pids, info, environment = _linux()
    else:
        raise Unavailable(sys.platform)
    wanted = b"OMNILANE_JOB_TOKEN=" + token.encode()
    own = os.getpid()
    sup = info(supervisor)
    if sup is None:
        raise Unavailable("supervisor not listed")
    # Linux keeps the fork time across exec, so the supervisor's own start time is the
    # baseline there; macOS can report a later one, so its clock reading is used instead.
    baseline = after_us if darwin else sup[1]
    excluded = {own, supervisor}
    parent, steps = sup[0], 0
    while parent > 1 and steps < 4096:  # the supervisor's whole ancestor chain
        excluded.add(parent)
        found = info(parent)
        if found is None:
            break
        parent, steps = found[0], steps + 1
    matches = []
    for pid in pids():
        if pid in excluded:
            continue
        try:
            details = info(pid)
            if details is None or details[1] <= baseline:
                continue
            env = environment(pid)
        except (OSError, ValueError, IndexError, struct.error):
            continue  # gone, unreadable or malformed: skip this process, keep scanning
        if env is None:
            continue  # another user's process, or gone; it cannot carry this job's token
        if wanted in env:
            matches.append((pid, details[1]))
    return matches


def live_matches(token, source=None):
    """Manual continuation checks exact ownership without requiring host stability.

    No supervisor/start cutoff is needed: this unguessable marker belongs to a
    completed job. Unrelated unreadable/disappearing PIDs are not refusal votes.
    Never expose an environment entry, and recheck only matched identities.
    """
    if source is None:
        source = _darwin() if sys.platform == "darwin" else _linux() if sys.platform.startswith("linux") else None
    if source is None:
        raise Unavailable("unsupported platform")
    pids, info, environment = source
    wanted = b"OMNILANE_JOB_TOKEN=" + token.encode()
    matches = []
    for pid in pids():
        if pid == os.getpid():
            continue
        try:
            before = info(pid)
            if before is None or wanted not in (environment(pid) or []):
                continue
            after = info(pid)
            if after is not None and after[1] == before[1]:
                matches.append((pid, before[1]))
        except (OSError, ValueError, IndexError, struct.error):
            continue
    return sorted(matches)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="job_marker.py")
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--token", required=True)
    scan_parser.add_argument("--supervisor", required=True, type=int)
    scan_parser.add_argument("--after", required=True, type=int)
    args = parser.parse_args(argv)
    if not args.token.isalnum() or not 16 <= len(args.token) <= 128:
        parser.error("invalid --token")
    try:
        found = scan(args.token, args.supervisor, args.after)
    except (Unavailable, OSError, ValueError, struct.error) as error:
        print(f"job_marker: cannot list processes: {error}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    for pid, start in found:
        print(pid, start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
