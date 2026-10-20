#!/usr/bin/env python3
"""Record why the commander did a task itself, and count how often it does.

    omnilane self-exec record --refusal PATH --reason "TEXT" [--lane L] [--task "TEXT"] [--json]
    omnilane stats self-exec [--since 7d] [--json]

A record must cite the refusal dispatch stored (the `evidence=` path of its
summary line), so "the dispatch was refused" is a file anyone can open. Records
go to OMNILANE_HOME/self-exec.jsonl. The share counts records against jobs whose
meta.json says they started in the same window.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import sys
from pathlib import Path

LOG_NAME = "self-exec.jsonl"
TEXT_LIMIT = 500


def home() -> Path:
    return Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def since_cutoff(text: str, moment: datetime.datetime) -> datetime.datetime:
    match = re.fullmatch(r"([1-9][0-9]*)([dh])", text)
    if match:
        amount = int(match.group(1))
        return moment - (datetime.timedelta(days=amount) if match.group(2) == "d"
                         else datetime.timedelta(hours=amount))
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        raise SystemExit("omnilane: --since takes Nd, Nh or YYYY-MM-DD") from None


def clean(text: str, name: str) -> str:
    value = " ".join(str(text).split())
    if not value:
        raise SystemExit(f"omnilane: --{name} must not be empty")
    return value[:TEXT_LIMIT]


def load_refusal(path: Path) -> tuple[dict, str, str]:
    """Accept the file dispatch stored ({"lane", "refusal": {...}}) or a bare refusal."""
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as error:
        raise SystemExit(f"omnilane: --refusal {path}: not a readable JSON file ({error.__class__.__name__})")
    refusal = value.get("refusal", value) if isinstance(value, dict) else None
    if not isinstance(refusal, dict) or refusal.get("allowed") is not False:
        raise SystemExit(f"omnilane: --refusal {path}: not a dispatch refusal (no \"allowed\": false)")
    return refusal, hashlib.sha256(raw).hexdigest(), str(value.get("lane") or "")


def record(args) -> int:
    path = Path(args.refusal).expanduser().resolve()
    refusal, digest, refused_lane = load_refusal(path)
    entry = {"schema_version": 1, "id": f"se-{now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}",
             "recorded_at": stamp(now()), "host": socket.gethostname().split(".")[0],
             "lane": args.lane or refused_lane,
             "reason": clean(args.reason, "reason"),
             "task": clean(args.task, "task") if args.task else "",
             "refusal": {"path": str(path), "sha256": digest,
                         "failed_gate": refusal.get("failed_gate"), "code": refusal.get("code")}}
    log = home() / LOG_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(entry, ensure_ascii=False))
    else:
        print(f"omnilane: recorded {entry['id']} in {log} "
              f"(refusal {entry['refusal']['code']} / {entry['refusal']['failed_gate']})")
    return 0


def records(cutoff: datetime.datetime) -> list[dict]:
    found = []
    try:
        lines = (home() / LOG_NAME).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return found
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        moment = parse_stamp(entry.get("recorded_at")) if isinstance(entry, dict) else None
        if moment is not None and moment >= cutoff:
            found.append(entry)
    return found


def dispatched(cutoff: datetime.datetime) -> collections.Counter:
    count = collections.Counter()
    for meta in (home() / "jobs").glob("*/meta.json"):
        try:
            value = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        moment = parse_stamp(value.get("started")) if isinstance(value, dict) else None
        if moment is not None and moment >= cutoff:
            count[value.get("executor") or "unknown"] += 1
    return count


def stats(args) -> int:
    moment = now()
    cutoff = since_cutoff(args.since, moment)
    own = records(cutoff)
    jobs = dispatched(cutoff)
    total = len(own) + sum(jobs.values())
    report = {"since": stamp(cutoff), "until": stamp(moment), "self_exec": len(own),
              "dispatched": sum(jobs.values()), "dispatched_by_executor": dict(sorted(jobs.items())),
              "self_exec_share": round(len(own) / total, 4) if total else None,
              "by_failed_gate": dict(collections.Counter(
                  (e.get("refusal") or {}).get("failed_gate") or "unknown" for e in own).most_common()),
              "by_code": dict(collections.Counter(
                  (e.get("refusal") or {}).get("code") or "unknown" for e in own).most_common()),
              "records": [{k: e.get(k) for k in ("id", "recorded_at", "lane", "reason")} for e in own]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    share = "n/a" if report["self_exec_share"] is None else f"{report['self_exec_share']:.1%}"
    print(f"self-exec since {report['since']}: {report['self_exec']} of {total} tasks ({share}); "
          f"dispatched {report['dispatched']} {report['dispatched_by_executor']}")
    for key in ("by_failed_gate", "by_code"):
        if report[key]:
            print(f"  {key.replace('_', ' ')}: " + ", ".join(f"{k} {v}" for k, v in report[key].items()))
    for entry in report["records"]:
        print(f"  {entry['recorded_at']} {entry['id']} [{entry['lane'] or '-'}] {entry['reason']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omnilane", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record one task the commander did itself")
    rec.add_argument("--refusal", required=True, help="refusal evidence file dispatch printed")
    rec.add_argument("--reason", required=True, help="why the commander did it itself")
    rec.add_argument("--lane", default="")
    rec.add_argument("--task", default="")
    rec.add_argument("--json", action="store_true")
    st = sub.add_parser("stats", help="self-exec share of all tasks in a window")
    st.add_argument("--since", default="7d", help="Nd, Nh or YYYY-MM-DD (default 7d)")
    st.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    return record(args) if args.command == "record" else stats(args)


if __name__ == "__main__":
    sys.exit(main())
