#!/usr/bin/env python3
"""Request-selector probe harness for the omnilane AA transport overlay.

Runs one CLI invocation, captures raw stdout/stderr to files, and writes a
descriptor with the original command/stream fields plus a vendor-specific
verdict, its reason, and the observed model when the response proves it.
"""
import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SWEEP_ID = os.environ.get("OMNILANE_TRANSPORT_SWEEP_ID", "overlay-reprobe-20260909")
DEFAULT_ROOT = Path.home() / ".omnilane" / "transport-evidence" / SWEEP_ID


def verdict(
    evidence_json: dict,
    stdout_text: str,
    stderr_text: str,
    vendor: str,
    expected_token: str | None,
) -> tuple[str, str, str | None]:
    """Judge raw evidence without reading files, running commands or mutating it.

    Only Claude's billed modelUsage keys currently prove the responding model.
    A requested selector (including the Codex banner) is not observed identity.
    """
    if evidence_json.get("timed_out"):
        return "fail", "timeout", None
    if not expected_token:
        return "fail", "missing-expected-token", None

    exit_code = evidence_json.get("exit_code")
    if vendor == "claude":
        try:
            response = json.loads(stdout_text)
        except (json.JSONDecodeError, TypeError):
            return "fail", "invalid-json", None
        if not isinstance(response, dict):
            return "fail", "invalid-json-result", None
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
            return "fail", f"{reason}: {result[:120]}", observed_model
        if not models:
            return "fail", "missing-model-usage", None
        command = evidence_json.get("command", [])
        requested_model = None
        for index, argument in enumerate(command):
            if argument == "--model" and index + 1 < len(command):
                requested_model = command[index + 1]
            elif isinstance(argument, str) and argument.startswith("--model="):
                requested_model = argument.split("=", 1)[1]
        if not requested_model:
            return "fail", "missing-requested-model", observed_model
        if models != [requested_model]:
            return "fail", "model-mismatch", observed_model
        if "unknown --effort" in stderr_text.lower():
            return "fail", "effort-silently-defaulted", observed_model
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}", observed_model
        if expected_token not in result:
            return "fail", "missing-expected-token", observed_model
        return "pass", "expected-token-and-model-matched", observed_model

    if vendor in ("grok", "agy"):
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}: {stderr_text[:120]}", None
        if stderr_text:
            return "fail", f"unexpected-stderr: {stderr_text[:120]}", None
        if expected_token not in stdout_text:
            return "fail", "missing-expected-token", None
        return "pass", "expected-token-and-clean-stderr", None

    if vendor == "codex":
        diagnostics = [line[:120] for line in stderr_text.splitlines()
                       if "error" in line.lower() or "warning" in line.lower()]
        review = "; stderr-review: " + " | ".join(diagnostics) if diagnostics else ""
        if exit_code != 0:
            return "fail", f"exit-code: {exit_code}{review}", None
        if expected_token not in stdout_text:
            return "fail", f"missing-expected-token{review}", None
        return "pass", f"expected-token-matched{review}", None

    return "fail", f"unsupported-vendor: {vendor}", None


def probe(
    name: str,
    argv: list[str],
    timeout: int = 180,
    cwd: Path | None = None,
    root: Path | None = None,
    *,
    vendor: str | None = None,
    expected_token: str | None = None,
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
    record["verdict"], record["verdict_reason"], record["observed_model"] = verdict(
        record, out_path.read_text(errors="replace"), err_path.read_text(errors="replace"),
        vendor, expected_token,
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
    parser.add_argument("name")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error("command is required")
    rec = probe(args.name, args.command, root=args.root,
                vendor=args.vendor, expected_token=args.expect)
    print(json.dumps(rec, ensure_ascii=False))
