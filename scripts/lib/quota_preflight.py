#!/usr/bin/env python3
"""Read operator-provided quota snapshots. Never query provider/session stores."""

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def snapshot(vendor, home, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    result = dict(vendor=vendor, status="unknown", freshness="unknown",
                  near_limit=False, source=None, captured_at=None,
                  age_seconds=None, reason="unsupported-vendor")
    # v1 has a policy only for Codex's weekly window.
    if vendor != "codex":
        return result
    try:
        threshold = number(float(os.environ.get("OMNILANE_QUOTA_THRESHOLD_PERCENT", "90")))
        max_age = float(os.environ.get("OMNILANE_QUOTA_MAX_AGE_SECONDS", "1800"))
        if not math.isfinite(max_age) or max_age < 0:
            raise ValueError("invalid maximum age")
        result.update(threshold_percent=threshold, max_age_seconds=max_age)
    except (ValueError, OverflowError):
        result["reason"] = "invalid-configuration"
        return result
    path = Path(os.environ.get("OMNILANE_QUOTA_DIR", str(Path(home) / "quota"))) / "codex.json"
    try:
        if path.is_symlink() or not path.is_file():
            result["reason"] = "missing-or-unsafe-snapshot"
            return result
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized snapshot")
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
    snap = json.loads(args.snapshot)
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
    p.add_argument("--original", nargs=3, required=True)
    p.add_argument("--final", nargs=3, required=True)
    p = commands.add_parser("capability")
    for name in ("executor", "vendor", "model", "effort", "mode", "workdir", "session", "thread", "context", "background", "job-timeout", "idle-timeout"):
        p.add_argument("--" + name, default="")
    args = parser.parse_args()
    if args.command == "snapshot":
        value = snapshot(args.vendor, args.home)
        print("{}\t{}\t{}".format(int(value["near_limit"]), value["status"], json.dumps(value, separators=(",", ":"))))
    elif args.command == "decision":
        print(json.dumps(decision(args), separators=(",", ":")))
    else:
        try:
            return capability(args)
        except (ValueError, OSError, KeyError, TypeError):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
