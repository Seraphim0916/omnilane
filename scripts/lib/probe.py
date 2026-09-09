#!/usr/bin/env python3
"""Request-selector probe harness for the omnilane AA transport overlay.

Runs one CLI invocation, captures raw stdout/stderr to files, and writes a
{command, exit_code, timed_out, elapsed_seconds, stdout, stderr} descriptor —
the same evidence shape the 2026-09-07 Codex run used, so both sets of
evidence files hash into the same overlay.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

SWEEP_ID = os.environ.get("OMNILANE_TRANSPORT_SWEEP_ID", "overlay-reprobe-20260909")
DEFAULT_ROOT = Path.home() / ".omnilane" / "transport-evidence" / SWEEP_ID


def probe(
    name: str,
    argv: list[str],
    timeout: int = 180,
    cwd: Path | None = None,
    root: Path | None = None,
) -> dict:
    root = (root or DEFAULT_ROOT).expanduser()
    evidence = root / "evidence"
    work = root / "work"
    evidence.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    out_path = evidence / f"{name}.stdout"
    err_path = evidence / f"{name}.stderr"
    started = time.time()
    timed_out = False
    env = dict(os.environ)
    # The runners drop the API key so the subscription OAuth path is used.
    env.pop("XAI_API_KEY", None)
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        proc = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                cwd=str(cwd or work), env=env)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            rc = proc.wait()
    record = {
        "command": argv,
        "cwd": str(cwd or work),
        "exit_code": rc,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 3),
        "stdout": str(out_path),
        "stderr": str(err_path),
    }
    (evidence / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def read(
    name: str,
    stream: str = "stdout",
    limit: int = 4000,
    root: Path | None = None,
) -> str:
    path = (root or DEFAULT_ROOT).expanduser() / "evidence" / f"{name}.{stream}"
    if not path.exists():
        return ""
    return path.read_text(errors="replace")[:limit]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("name")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error("command is required")
    rec = probe(args.name, args.command, root=args.root)
    print(json.dumps(rec, ensure_ascii=False))
