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
import json_depth
import os
import re
import secrets
import sys
from pathlib import Path

QUOTE_LIMIT = 160


def refusal_from(text: str) -> dict | None:
    found = None
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json_depth.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict) and value.get("allowed") is False:
            found = value
    return found


def one_line(value, limit=None) -> str:
    text = " ".join(str(value or "").split())
    return text if limit is None or len(text) <= limit else text[:limit - 1] + "…"


def sanitise_human_line(value, limit=600) -> str:
    """Redact display text only; machine-readable evidence keeps its exact bytes.

    Use the pending-recheck public_reason redaction families, with a larger
    display budget and intact CLI flags so the next step remains usable.
    """
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value or ""))
    text = one_line(re.sub(r"[\x00-\x1f\x7f]", " ", text))
    text = re.sub(r'''--workdir(?:=|\s+)(?:'[^']*'|"[^"]*"|<[^>]*>|\S+)''',
                  "--workdir <your project dir>", text)
    text = re.sub(r"[\w.+%-]+@[\w.-]+", "[redacted]", text)
    text = re.sub(r'''(?i)\b(?:account|organisation|organization|org|tenant|user)[ _-]*(?:id)?
                     ["']?\s*[=:]\s*["']?[^\s,;"']+''', "[redacted]", text, flags=re.X)
    text = re.sub(r'''(?i)\b(?:account|organisation|organization|org|tenant|user)[ _-]*id\s+
                     ["']?[^\s,;"']+''', "[redacted]", text, flags=re.X)
    text = re.sub(r'''(?i)\b(?:bearer\s+|(?:access[ _-]*token|refresh[ _-]*token|token|api[ _-]*key)
                     ["']?\s*[=:]\s*["']?)[^\s,;"']+''', "[redacted]", text, flags=re.X)
    text = re.sub(r"(?i)\b(?:sk[-_]|org[-_]|acct[-_]|account[-_]|tenant[-_])[A-Za-z0-9_-]+\b",
                  "[redacted]", text)
    text = re.sub(r'''(?<![\w])(?:~?/|\.\.?/)[^\s,;"'<>]+''', "[redacted]", text)
    text = re.sub(r"(?<![\w-])[A-Za-z0-9_./+=-]{24,}",
                  lambda match: match.group(0) if re.fullmatch(r"--[a-z]+(?:-[a-z]+)*", match.group(0))
                  else "[redacted]", text)
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return one_line(text, limit)


def caller_guidance(diagnostic: str) -> dict:
    """Explain an identity failure; these hints never supply caller authority."""
    if "Codex sandbox" in diagnostic and "blocked process inspection" in diagnostic:
        return dict(reason="the Codex sandbox blocked process inspection",
                    next_command="run omnilane whoami in a normal terminal or a session authorized for process inspection")
    if "no scored configuration for" in diagnostic:
        return dict(reason="this model has no row in the approved score table yet; omnilane refuses "
                           "unscored callers by design until the table is updated in a release",
                    next_command='omnilane route --inherit --native-context <file> <lane> "<task>" '
                                 "(your own sub-agent; create its runtime capability file with omnilane native-context)")
    if "only command" in diagnostic and "codex" in diagnostic.lower():
        return dict(reason="the Codex compound command hid the caller identity",
                    next_command="run the omnilane command alone as the only command of the tool call, "
                                 "without ;, &&, | or a subshell")
    if "no vendor CLI among this process's ancestors" in diagnostic:
        return dict(reason="no vendor CLI was found among this process's ancestors",
                    next_command="a human operator adds --operator-asserted-human to the command "
                                 "(the human operator's statement, not for models)")
    if "was launched without model_reasoning_effort" in diagnostic:
        return dict(reason="the Codex launcher was found, but its effort is unknown because "
                           "model_reasoning_effort was not set",
                    next_command="restart Codex with -m MODEL -c 'model_reasoning_effort = \"EFFORT\"', "
                                 "then retry the omnilane command")
    if "was launched without --effort" in diagnostic:
        return dict(reason="the launcher was found, but its effort is unknown because --effort was not set",
                    next_command="restart the CLI with explicit --model MODEL and --effort EFFORT flags, "
                                 "then retry the omnilane command")
    if "was launched without a model flag" in diagnostic:
        return dict(reason="the launcher was found, but its model is unknown because --model was not set",
                    next_command="restart the CLI with an explicit --model flag and an explicit effort flag, "
                                 "then retry the omnilane command")
    return dict(reason=diagnostic or "the launcher did not provide a readable model and effort",
                next_command="restart the vendor CLI with explicit model and effort flags, "
                             "then retry the omnilane command")


def human_line(refusal: dict) -> str:
    gate = one_line(refusal.get("failed_gate") or refusal.get("code") or "unknown")
    reason = one_line(refusal.get("reason") or refusal.get("message") or "the request was refused").rstrip(".")
    command = one_line(refusal.get("next_command") or "omnilane doctor")
    return sanitise_human_line(f"omnilane: refused ({gate}): {reason}. Next: {command}")


def print_human(refusal: dict) -> None:
    if os.environ.get("OMNILANE_REFUSAL_JSON_ONLY") != "1":
        print(human_line(refusal), file=sys.stderr)


def summary(refusal: dict, path: Path | None) -> str:
    quote = one_line(refusal.get("message"), QUOTE_LIMIT).replace('"', "'")
    reason = one_line(refusal.get("reason") or refusal.get("message"), QUOTE_LIMIT).replace('"', "'")
    parts = [f"failed_gate={one_line(refusal.get('failed_gate') or 'unknown')}",
             f"code={one_line(refusal.get('code') or 'unknown')}",
             f'reason="{reason}"', f'quote="{quote}"']
    if refusal.get("next_command") and os.environ.get("OMNILANE_REFUSAL_JSON_ONLY") != "1":
        command = sanitise_human_line(refusal["next_command"]).replace('"', "'")
        parts.append(f'next_command="{command}"')
    if path is not None:
        parts.append(f"evidence={one_line(path)}")
    return ("omnilane refusal: " + " ".join(parts)).encode(
        "utf-8", "backslashreplace").decode("utf-8")


def store(home: Path, refusal: dict, lane: str, headline: str) -> Path:
    directory = home / "refusals"
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {"schema_version": 1, "recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "lane": lane, "headline": headline, "refusal": refusal}
    path = directory / f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        # Retain raw-fd ownership even if stream construction partly succeeds.
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as stream:
            # Escape lone surrogate code units without changing parsed field values.
            stream.write(json.dumps(record, ensure_ascii=False, indent=2).encode(
                "utf-8", "backslashreplace").decode("utf-8") + "\n")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass  # Preserve the original construction, serialization, or write error.
        raise
    else:
        os.close(fd)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default="")
    parser.add_argument("--human-only", action="store_true", help="print the readable line without storing evidence")
    parser.add_argument("--lane", default="")
    parser.add_argument("--headline", default="", help="the plain-text line dispatch printed first")
    args = parser.parse_args(argv)
    if not args.home and not args.human_only:
        parser.error("--home is required unless --human-only is used")
    refusal = refusal_from(sys.stdin.read())
    if args.human_only:
        if refusal is None and not args.headline:
            return 1
        display = dict(refusal) if refusal is not None else dict(
            failed_gate="target-availability", next_command="omnilane list")
        if args.headline:
            # Availability is a separate failure from the last policy decision.
            # Keep its explanation in the human line without changing the JSON.
            headline = args.headline.removeprefix("omnilane: ")
            display["reason"] = headline + " " + str(display.get("reason") or display.get("message") or "")
        print_human(display)
        return 0
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
