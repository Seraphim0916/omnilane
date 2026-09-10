#!/usr/bin/env python3
"""Request-selector probe harness for the omnilane AA transport overlay.

Runs one CLI invocation, captures raw stdout/stderr to files, and writes a
descriptor with the original command/stream fields plus a vendor-specific
verdict, its reason, the observed model, and the tier of evidence that model
rests on.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SWEEP_ID = os.environ.get("OMNILANE_TRANSPORT_SWEEP_ID", "overlay-reprobe-20260909")
DEFAULT_ROOT = Path.home() / ".omnilane" / "transport-evidence" / SWEEP_ID

# Ordered strongest first. The tier is read off the evidence a run produced, not
# off the vendor: a CLI that starts reporting a billed model earns the higher
# tier with no change here.
TIER_BILLED = "billed-model"       # the provider named the model it charged for
TIER_ECHO = "client-echo"          # the CLI recorded the model it asked for
TIER_SELECTOR = "selector-only"    # the CLI accepted the selector and said no more
EVIDENCE_TIERS = (TIER_BILLED, TIER_ECHO, TIER_SELECTOR)


def codex_failure(stdout_text: str) -> str:
    """The last thing codex's event stream said went wrong, if anything.

    Later events supersede earlier ones: a retry notice is progress, the message
    on `turn.failed` is the outcome.
    """
    latest = ""
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.failed":
            message = (event.get("error") or {}).get("message")
        elif event.get("type") == "error":
            message = event.get("message")
        else:
            continue
        if isinstance(message, str) and message:
            latest = message
    return f"; codex-event: {latest[:200]}" if latest else ""


def _requested_model(command: list) -> str | None:
    requested = None
    for index, argument in enumerate(command):
        if not isinstance(argument, str):
            continue
        if argument in ("--model", "-m") and index + 1 < len(command):
            requested = command[index + 1]
        elif argument.startswith("--model="):
            requested = argument.split("=", 1)[1]
    return requested


def record_values(record_text: str) -> list[str]:
    """Comparable values from a client record digest: `key<TAB>value` lines.

    `#` lines carry provenance and display labels that must never be compared —
    agy's backend label is "Gemini 3.8 Flash (Low)", not a model identifier.
    """
    values = []
    for line in (record_text or "").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        value = line.split("\t", 1)[1].strip()
        if value:
            values.append(value)
    return values


def _client_record(
    evidence_json: dict,
    record_text: str,
    passed_reason: str,
) -> tuple[str, str, str | None, str]:
    """Judge a CLI's own on-disk record of the request it sent."""
    values = record_values(record_text)
    if not values:
        return "pass", f"{passed_reason}; no-client-record", None, TIER_SELECTOR
    observed_model = ", ".join(sorted(set(values)))
    requested_model = _requested_model(evidence_json.get("command", []))
    if not requested_model:
        return "fail", "missing-requested-model", observed_model, TIER_SELECTOR
    if requested_model not in values:
        return "fail", "client-record-mismatch", observed_model, TIER_SELECTOR
    return "pass", f"{passed_reason}-and-client-record-matched", observed_model, TIER_ECHO


def verdict(
    evidence_json: dict,
    stdout_text: str,
    stderr_text: str,
    vendor: str,
    expected_token: str | None,
    extra: dict | None = None,
) -> tuple[str, str, str | None, str]:
    """Judge raw evidence without reading files, running commands or mutating it.

    `extra` carries text the caller already gathered from disk, so this stays a
    pure function that can re-judge an old sweep offline.
    """
    extra = extra or {}
    if evidence_json.get("timed_out"):
        return "fail", "timeout", None, TIER_SELECTOR
    if not expected_token:
        return "fail", "missing-expected-token", None, TIER_SELECTOR

    exit_code = evidence_json.get("exit_code")
    if vendor == "claude":
        try:
            response = json.loads(stdout_text)
        except (json.JSONDecodeError, TypeError):
            return "fail", "invalid-json", None, TIER_SELECTOR
        if not isinstance(response, dict):
            return "fail", "invalid-json-result", None, TIER_SELECTOR
        usage = response.get("modelUsage")
        models = sorted(usage) if isinstance(usage, dict) else []
        observed_model = ", ".join(models) or None
        result = response.get("result", "")
        if not isinstance(result, str):
            result = str(result)
        if response.get("is_error"):
            lower_result = result.lower()
            if "limit" in lower_result or "quota" in lower_result:
                reason = "quota-exhausted"
            elif "api error" in lower_result:
                reason = "api-error"
            else:
                reason = "result-error"
            return "fail", f"{reason}: {result[:120]}", observed_model, TIER_SELECTOR
        if not models:
            return "fail", "missing-model-usage", None, TIER_SELECTOR
        requested_model = _requested_model(evidence_json.get("command", []))
        if not requested_model:
            return "fail", "missing-requested-model", observed_model, TIER_SELECTOR
        if models != [requested_model]:
            return "fail", "model-mismatch", observed_model, TIER_SELECTOR
        if "unknown --effort" in stderr_text.lower():
            return "fail", "effort-silently-defaulted", observed_model, TIER_SELECTOR
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}", observed_model, TIER_SELECTOR
        if expected_token not in result:
            return "fail", "missing-expected-token", observed_model, TIER_SELECTOR
        return "pass", "expected-token-and-model-matched", observed_model, TIER_BILLED

    if vendor == "grok":
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}: {stderr_text[:120]}", None, TIER_SELECTOR
        if stderr_text:
            return "fail", f"unexpected-stderr: {stderr_text[:120]}", None, TIER_SELECTOR
        if expected_token not in stdout_text:
            return "fail", "missing-expected-token", None, TIER_SELECTOR
        try:
            response = json.loads(stdout_text)
        except (json.JSONDecodeError, TypeError):
            response = None
        usage = response.get("modelUsage") if isinstance(response, dict) else None
        models = sorted(usage) if isinstance(usage, dict) else []
        if not models:
            return "pass", "expected-token-and-clean-stderr; no-billed-model", None, TIER_SELECTOR
        observed_model = ", ".join(models)
        requested_model = _requested_model(evidence_json.get("command", []))
        if not requested_model:
            return "fail", "missing-requested-model", observed_model, TIER_SELECTOR
        # Grok bills `grok-4.6` as `grok-4.6-build`. Accept that one suffix and
        # nothing else: a prefix test would let `grok-4.6-anything` pass.
        if models not in ([requested_model], [f"{requested_model}-build"]):
            return "fail", "model-mismatch", observed_model, TIER_SELECTOR
        return "pass", "expected-token-and-billed-model-matched", observed_model, TIER_BILLED

    if vendor == "agy":
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}: {stderr_text[:120]}", None, TIER_SELECTOR
        if stderr_text:
            return "fail", f"unexpected-stderr: {stderr_text[:120]}", None, TIER_SELECTOR
        if expected_token not in stdout_text:
            return "fail", "missing-expected-token", None, TIER_SELECTOR
        return _client_record(evidence_json, extra.get("cli_log", ""),
                              "expected-token-and-clean-stderr")

    if vendor == "codex":
        diagnostics = [line[:120] for line in stderr_text.splitlines()
                       if "error" in line.lower() or "warning" in line.lower()]
        review = "; stderr-review: " + " | ".join(diagnostics) if diagnostics else ""
        # Under `--json` the refusal that ended the run is an stdout event, not
        # a stderr line, so a failure would otherwise be recorded as a bare exit
        # code and leave unproven[] saying nothing a reader can act on.
        why = codex_failure(stdout_text)
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}{why}{review}", None, TIER_SELECTOR
        if expected_token not in stdout_text:
            return "fail", f"missing-expected-token{why}{review}", None, TIER_SELECTOR
        result, reason, observed, tier = _client_record(
            evidence_json, extra.get("rollout", ""), "expected-token-matched")
        return result, f"{reason}{review}", observed, tier

    return "fail", f"unsupported-vendor: {vendor}", None, TIER_SELECTOR


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk_models(node, prefix: str, found: dict) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else key
            if key == "model" and isinstance(value, str):
                found.setdefault(here, value)
            _walk_models(value, here, found)
    elif isinstance(node, list):
        for item in node:
            _walk_models(item, f"{prefix}[]", found)


def codex_record(stdout_text: str, sessions_dir: Path | None = None) -> str:
    """Digest the rollout codex persisted for the thread this run started.

    Needs `codex exec --json` for the thread id and no `--ephemeral`, which
    would suppress the rollout this reads.
    """
    thread_id = None
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
            break
    if not thread_id:
        return ""
    root = sessions_dir or Path.home() / ".codex" / "sessions"
    matches = sorted(glob.glob(str(root / "**" / f"rollout-*{thread_id}.jsonl"), recursive=True))
    if not matches:
        return ""
    path = Path(matches[0])
    found: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        _walk_models(event, "", found)
    lines = [f"# thread_id\t{thread_id}", f"# source\t{path}", f"# sha256\t{_sha256(path)}"]
    lines += [f"{key}\t{value}" for key, value in sorted(found.items())]
    return "\n".join(lines) + "\n"


AGY_MODEL = re.compile(r"Resolving model (\S+)")
AGY_LABEL = re.compile(r'selected model override to backend: label="([^"]+)"')


def agy_record(app_root: Path, since: float) -> str:
    """Digest the model agy resolved in the logs this run wrote under app_root.

    `since` rejects logs from an earlier run, so a reused app root cannot lend
    its model string to a later probe.
    """
    app_root = Path(app_root).expanduser()
    if not app_root.is_dir():
        return ""
    lines: list[str] = []
    values: dict[str, str] = {}
    for path in sorted(app_root.rglob("cli*.log")):
        if not path.is_file() or path.stat().st_mtime < since - 2:
            continue
        text = path.read_text(errors="replace")
        models = AGY_MODEL.findall(text)
        if not models:
            continue
        lines += [f"# source\t{path}", f"# sha256\t{_sha256(path)}"]
        lines += [f"# label\t{label}" for label in dict.fromkeys(AGY_LABEL.findall(text))]
        for model in models:
            values.setdefault(f"{path.name}:resolved-model:{model}", model)
    if not values:
        return ""
    lines += [f"{key}\t{value}" for key, value in sorted(values.items())]
    return "\n".join(lines) + "\n"


def probe(
    name: str,
    argv: list[str],
    timeout: int = 180,
    cwd: Path | None = None,
    root: Path | None = None,
    *,
    vendor: str | None = None,
    expected_token: str | None = None,
    app_root: Path | None = None,
) -> dict:
    if not expected_token:
        raise ValueError("expected_token is required before running a probe")
    if not argv:
        raise ValueError("command is required")
    vendor = vendor or Path(argv[0]).name
    if vendor not in ("claude", "grok", "agy", "codex"):
        raise ValueError(f"unsupported vendor: {vendor}; pass vendor explicitly")
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
        "probed_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "stdout": str(out_path),
        "stderr": str(err_path),
    }
    stdout_text = out_path.read_text(errors="replace")
    extra = {}
    if vendor == "codex":
        extra["rollout"] = codex_record(stdout_text)
    elif vendor == "agy" and app_root is not None:
        extra["cli_log"] = agy_record(app_root, started)
    for key, text in extra.items():
        if not text:
            continue
        # Stored so the sweep hashes it and verdict() can re-judge it offline.
        record_path = evidence / f"{name}.{key}"
        record_path.write_text(text)
        record[key] = str(record_path)
    record["verdict"], record["verdict_reason"], record["observed_model"], \
        record["evidence_tier"] = verdict(
            record, stdout_text, err_path.read_text(errors="replace"),
            vendor, expected_token, extra,
        )
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
    parser.add_argument("--expect", required=True, help="expected response token")
    parser.add_argument("--vendor", choices=("claude", "grok", "agy", "codex"),
                        help="defaults to the command executable's basename")
    parser.add_argument("--app-root", type=Path,
                        help="agy app data root for this run, to read back its cli log")
    parser.add_argument("name")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error("command is required")
    rec = probe(args.name, args.command, root=args.root, app_root=args.app_root,
                vendor=args.vendor, expected_token=args.expect)
    print(json.dumps(rec, ensure_ascii=False))
