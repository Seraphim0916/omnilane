#!/usr/bin/env python3
"""Keep a dispatch refusal as evidence a commander can cite.

Reads the refusal JSON that dispatch just printed (one object per line; the last
one with "allowed": false wins), stores it under OMNILANE_HOME/refusals/, prints
one summary line to stderr that pastes into a report as is, and prints the stored
path on stdout. `omnilane self-exec record --refusal PATH` takes that path.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import sys
from pathlib import Path

QUOTE_LIMIT = 160


def refusal_from(text: str) -> dict | None:
    found = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("allowed") is False:
            found = value
    return found


def one_line(value, limit=None) -> str:
    text = " ".join(str(value or "").split())
    return text if limit is None or len(text) <= limit else text[:limit - 1] + "…"


def summary(refusal: dict, path: Path | None) -> str:
    quote = one_line(refusal.get("message"), QUOTE_LIMIT).replace('"', "'")
    reason = one_line(refusal.get("reason") or refusal.get("message"), QUOTE_LIMIT).replace('"', "'")
    parts = [f"failed_gate={refusal.get('failed_gate') or 'unknown'}",
             f"code={refusal.get('code') or 'unknown'}",
             f'reason="{reason}"', f'quote="{quote}"']
    if path is not None:
        parts.append(f"evidence={path}")
    return "omnilane refusal: " + " ".join(parts)


def store(home: Path, refusal: dict, lane: str, headline: str) -> Path:
    directory = home / "refusals"
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {"schema_version": 1, "recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "lane": lane, "headline": headline, "refusal": refusal}
    path = directory / f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True)
    parser.add_argument("--lane", default="")
    parser.add_argument("--headline", default="", help="the plain-text line dispatch printed first")
    args = parser.parse_args(argv)
    refusal = refusal_from(sys.stdin.read())
    if refusal is None:
        return 1
    path = None
    if os.environ.get("OMNILANE_REFUSAL_EVIDENCE", "1") != "0":
        try:
            path = store(Path(args.home), refusal, args.lane, one_line(args.headline))
        except OSError as error:
            print(f"omnilane: refusal evidence not stored ({error.__class__.__name__})", file=sys.stderr)
    print(summary(refusal, path), file=sys.stderr)
    if path is not None:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
