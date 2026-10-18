#!/usr/bin/env python3
"""Read operator-provided quota snapshots. Never query provider/session stores."""

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from types import SimpleNamespace


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    # Python 3.9 accepts only colon-separated offsets and 3/6 fraction digits.
    # Normalize these ISO forms before using the standard calendar validator.
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    value = re.sub(r"(\d{2}:\d{2}:\d{2})[.,](\d+)",
                   lambda m: m[1] + "." + m[2][:6].ljust(6, "0"), value)
    value = dt.datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp requires a timezone")
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("percentage must be a number")
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("percentage out of range")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate snapshot field")
        result[key] = value
    return result


def unknown_snapshot(vendor, reason):
    return dict(vendor=vendor, status="unknown", freshness="unknown",
                near_limit=False, source=None, captured_at=None,
                age_seconds=None, reason=reason)


def read_snapshot(directory):
    # Pin the parent directory, then open the leaf relative to it. Neither
    # open follows a symlink; NONBLOCK prevents a raced-in FIFO from hanging.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    parent = os.open(directory, flags | os.O_DIRECTORY)
    try:
        fd = os.open("codex.json", flags, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        # Preserve the 65,536-character contract while bounding UTF-8 bytes.
        if not stat.S_ISREG(info.st_mode) or info.st_size > 262144:
            raise ValueError("unsafe or oversized snapshot")
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError("oversized snapshot")
    raw = raw.decode("utf-8")
    if len(raw) > 65536:
        raise ValueError("oversized snapshot")
    return raw


def snapshot(vendor, home, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    result = unknown_snapshot(vendor, "unsupported-vendor")
    # v1 has a policy only for Codex's weekly window.
    if vendor != "codex":
        return result
    try:
        threshold = number(float(os.environ.get("OMNILANE_QUOTA_THRESHOLD_PERCENT") or "90"))
        max_age = float(os.environ.get("OMNILANE_QUOTA_MAX_AGE_SECONDS") or "1800")
        if not math.isfinite(max_age) or max_age < 0:
            raise ValueError("invalid maximum age")
        result.update(threshold_percent=threshold, max_age_seconds=max_age)
    except (ValueError, OverflowError):
        result["reason"] = "invalid-configuration"
        return result
    directory = Path(os.environ.get("OMNILANE_QUOTA_DIR") or str(Path(home) / "quota"))
    if not directory.is_absolute():
        result["reason"] = "invalid-configuration"
        return result
    try:
        raw = read_snapshot(directory)
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or value["vendor"] != vendor or value["window"] != "weekly":
            raise ValueError("wrong vendor/window")
        used = number(value["used_percent"])
        captured = timestamp(value["captured_at"])
        resets = timestamp(value["resets_at"])
        source = value["source"]
        if not isinstance(source, str) or not source or len(source) > 128 or not source.isprintable():
            raise ValueError("invalid source label")
        age = (now - captured).total_seconds()
        if age < 0 or resets <= captured:
            raise ValueError("invalid snapshot time")
        result.update(source=source, captured_at=value["captured_at"],
                      resets_at=value["resets_at"], age_seconds=round(age, 3),
                      used_percent=used, window="weekly")
        if age > max_age or resets <= now:
            result.update(freshness="stale", reason="stale-snapshot")
        else:
            result.update(status="fresh", freshness="fresh", reason="weekly-snapshot",
                          near_limit=used >= threshold)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        result["reason"] = "invalid-snapshot"
    return result


def candidate(values):
    if not values or not values[0]:
        return None
    return dict(zip(("vendor", "model", "effort"), values))


def decision(args):
    if args.snapshot:
        snap = json.loads(args.snapshot)
    else:
        vendor = args.original[0]
        if vendor == "vote" and "codex" in args.original[1].split(","):
            vendor = "codex"
        snap = unknown_snapshot(vendor, "snapshot-read-failed")
    return dict(schema_version=1, decided_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                job_id=None, lane=args.lane, status=snap["status"], snapshot=snap,
                action=args.action, original=candidate(args.original),
                exclusion_reason="quota_near_limit" if snap["near_limit"] else None,
                final=candidate(args.final))


def capability(args):
    # Reuse the native executor's exact row match, including isolation, tools,
    # lifecycle and inherited runtime constraints. No job or provider is started.
    import native
    request = SimpleNamespace(executor=args.executor, vendor=args.vendor,
                              model=args.model, effort=args.effort, mode=args.mode,
                              workdir=native.canonical_dir(args.workdir),
                              session=args.session, thread=args.thread,
                              background=args.background == "1",
                              job_timeout=args.job_timeout, idle_timeout=args.idle_timeout)
    context = native.capability_context(args.context) if args.context else None
    executor, _, _ = native.choose(request, context)
    if executor == "native":
        return 0
    return 1 if args.executor == "native" else 10


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("snapshot")
    p.add_argument("vendor")
    p.add_argument("home")
    p = commands.add_parser("decision")
    for name in ("snapshot", "lane", "action"):
        p.add_argument("--" + name, required=True)
    for side in ("original", "final"):
        for field in ("vendor", "model", "effort"):
            p.add_argument("--" + side + "-" + field, required=True)
    p = commands.add_parser("persist")
    p.add_argument("--path", required=True)
    p.add_argument("--decision", required=True)
    p = commands.add_parser("capability")
    for name in ("executor", "vendor", "model", "effort", "mode", "workdir", "session", "thread", "context", "background", "job-timeout", "idle-timeout"):
        p.add_argument("--" + name, default="")
    args = parser.parse_args()
    if args.command == "snapshot":
        value = snapshot(args.vendor, args.home)
        print("{}\t{}\t{}".format(int(value["near_limit"]), value["status"], json.dumps(value, separators=(",", ":"))))
    elif args.command == "decision":
        for side in ("original", "final"):
            setattr(args, side, [getattr(args, side + "_" + field)
                                 for field in ("vendor", "model", "effort")])
        print(json.dumps(decision(args), separators=(",", ":")))
    elif args.command == "persist":
        import native
        native.atomic_json(Path(args.path), json.loads(args.decision))
    else:
        try:
            return capability(args)
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
