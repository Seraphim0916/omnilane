#!/usr/bin/env python3
"""Fixed, body-free routing quality/cost benchmark."""

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKLOADS = ROOT / "benchmarks" / "workloads.tsv"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class BenchmarkError(Exception):
    pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="omnilane benchmark",
        description=(
            "Compare fixed-workload routing quality and user-supplied per-call "
            "costs. Provider calls require --run."
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--run",
        action="store_true",
        help="invoke providers in advise mode; default is routing-only dry-run",
    )
    parser.add_argument(
        "--vendor",
        action="append",
        default=[],
        metavar="VENDOR",
        help="vendor to compare; repeatable (default: configured vendors)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="per-workload timeout, 1..600 (default: 60)",
    )
    parser.add_argument(
        "--workloads",
        type=Path,
        default=DEFAULT_WORKLOADS,
        metavar="FILE",
        help="fixed TSV workload file",
    )
    parser.add_argument(
        "--cost-per-call",
        action="append",
        default=[],
        metavar="VENDOR=USD",
        help="optional user-supplied estimated USD per call; repeatable",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    return args


def unique(values):
    return list(dict.fromkeys(values))


def decode_plan_value(value):
    try:
        parsed = shlex.split(value, posix=True)
    except ValueError:
        return value
    return parsed[0] if len(parsed) == 1 else value


def parse_plan(text):
    plan = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[a-z_]+", key):
            plan[key] = decode_plan_value(value)
    return plan


def load_workloads(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BenchmarkError(f"cannot read workloads: {path}: {exc}") from exc

    workloads = []
    seen = set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t", 4)
        if len(fields) != 5:
            raise BenchmarkError(
                f"{path}:{line_number}: expected 5 tab-separated fields"
            )
        workload_id, lane, weight_text, pattern, prompt = fields
        if not ID_RE.fullmatch(workload_id) or workload_id in seen:
            raise BenchmarkError(f"{path}:{line_number}: invalid or duplicate id")
        if not NAME_RE.fullmatch(lane):
            raise BenchmarkError(f"{path}:{line_number}: invalid lane")
        try:
            weight = int(weight_text)
        except ValueError as exc:
            raise BenchmarkError(f"{path}:{line_number}: weight must be an integer") from exc
        if not 1 <= weight <= 100:
            raise BenchmarkError(f"{path}:{line_number}: weight must be 1..100")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise BenchmarkError(f"{path}:{line_number}: invalid regex: {exc}") from exc
        if not prompt.strip():
            raise BenchmarkError(f"{path}:{line_number}: prompt must not be empty")
        seen.add(workload_id)
        workloads.append(
            {
                "id": workload_id,
                "lane": lane,
                "weight": weight,
                "pattern": pattern,
                "prompt": prompt,
            }
        )
    if not workloads:
        raise BenchmarkError(f"no workloads found: {path}")
    return workloads


def parse_costs(items):
    costs = {}
    for item in items:
        if "=" not in item:
            raise BenchmarkError("--cost-per-call must use VENDOR=USD")
        vendor, raw = item.split("=", 1)
        if not NAME_RE.fullmatch(vendor):
            raise BenchmarkError(f"invalid cost vendor: {vendor}")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise BenchmarkError(f"invalid cost for {vendor}: {raw}") from exc
        if not value.is_finite() or value < 0:
            raise BenchmarkError(f"cost for {vendor} must be non-negative")
        costs[vendor] = value
    return costs


def list_routes(dispatch, env):
    result = subprocess.run(
        [str(dispatch), "--list"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise BenchmarkError(f"cannot list routing: {detail[0]}")
    lanes = []
    vendors = []
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        lane, raw = line.split(":", 1)
        lane = lane.strip()
        if not NAME_RE.fullmatch(lane):
            continue
        lanes.append(lane)
        try:
            fields = shlex.split(raw.strip())
        except ValueError:
            fields = raw.split()
        if fields and NAME_RE.fullmatch(fields[0]) and fields[0] not in {
            "off",
            "exec",
            "vote",
        }:
            vendors.append(fields[0])
    return unique(lanes), unique(vendors)


def resolve_route(dispatch, vendor, preferred_lane, lanes, prompt, env):
    last_error = "vendor is not configured in an available lane"
    for lane in unique([preferred_lane] + list(lanes)):
        result = subprocess.run(
            [
                str(dispatch),
                "--dry-run",
                "--mode",
                "advise",
                "--vendor",
                vendor,
                lane,
                prompt,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            plan = parse_plan(result.stdout)
            if plan.get("provider_invoked") != "no":
                return None, "dry-run contract did not confirm provider_invoked=no"
            if plan.get("vendor") != vendor:
                return None, "dry-run resolved a different vendor"
            plan.setdefault("lane", lane)
            return plan, ""
        detail = result.stderr.strip().splitlines()
        if detail:
            last_error = detail[-1]
    return None, last_error


def money(value):
    return format(value.quantize(Decimal("0.01")), "f")


def cost_report(vendor, calls, costs):
    if vendor not in costs:
        return None
    per_call = costs[vendor]
    return {
        "basis": "user-supplied-per-call",
        "currency": "USD",
        "per_call_usd": money(per_call),
        "estimated_total_usd": money(per_call * calls),
    }


def run_one(repo, vendor, plan, workload, timeout, env):
    runner = repo / "scripts" / "runners" / f"run-{vendor}.sh"
    base = {
        "id": workload["id"],
        "requested_lane": workload["lane"],
        "resolved_lane": plan.get("lane", workload["lane"]),
        "model": plan.get("model", "-"),
        "effort": plan.get("effort", "-"),
        "weight": workload["weight"],
    }
    if not runner.is_file() or not os.access(runner, os.X_OK):
        return dict(base, status="runner_error", duration_seconds=0.0, response_bytes=0), True

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="omnilane-benchmark-") as temporary:
        temporary_path = Path(temporary)
        prompt_file = temporary_path / "prompt.txt"
        output_file = temporary_path / "response.txt"
        prompt_file.write_text(str(workload["prompt"]) + "\n", encoding="utf-8")
        run_env = env.copy()
        run_env["OMNILANE_TIMEOUT"] = str(timeout)
        try:
            result = subprocess.run(
                [
                    str(runner),
                    "advise",
                    str(repo),
                    plan.get("model", "-"),
                    plan.get("effort", "-"),
                    str(prompt_file),
                    str(output_file),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=run_env,
                timeout=timeout + 5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            elapsed = round(time.monotonic() - started, 3)
            return dict(base, status="runner_error", duration_seconds=elapsed, response_bytes=0), True
        elapsed = round(time.monotonic() - started, 3)
        try:
            body = output_file.read_text(encoding="utf-8")
            response_bytes = output_file.stat().st_size
        except OSError:
            body = ""
            response_bytes = 0
        if result.returncode != 0:
            status = "runner_error"
            runner_error = True
        else:
            status = "passed" if re.search(str(workload["pattern"]), body) else "failed"
            runner_error = False
        return dict(
            base,
            status=status,
            duration_seconds=elapsed,
            response_bytes=response_bytes,
        ), runner_error


def execute(args):
    repo = Path(os.environ.get("OMNILANE_BENCHMARK_REPO", str(ROOT))).resolve()
    dispatch = repo / "scripts" / "dispatch.sh"
    if not dispatch.is_file() or not os.access(dispatch, os.X_OK):
        raise BenchmarkError(f"dispatch is not executable: {dispatch}")
    env = os.environ.copy()
    workloads = load_workloads(args.workloads)
    costs = parse_costs(args.cost_per_call)
    lanes, configured_vendors = list_routes(dispatch, env)
    vendors = unique(args.vendor or configured_vendors)
    if not vendors:
        raise BenchmarkError("no configured benchmark vendors found")
    for vendor in vendors:
        if not NAME_RE.fullmatch(vendor) or vendor in {"off", "exec", "vote"}:
            raise BenchmarkError(f"unsupported benchmark vendor: {vendor}")

    report = {
        "schema_version": 1,
        "command": "benchmark",
        "mode": "run" if args.run else "dry-run",
        "provider_invoked": False,
        "workload_count": len(workloads),
        "workloads_file": str(args.workloads.resolve()),
        "vendors": [],
    }
    had_error = False
    provider_invoked = False
    for vendor in vendors:
        vendor_started = time.monotonic()
        items = []
        passed = 0
        score_possible = 0
        score_earned = 0
        for workload in workloads:
            score_possible += int(workload["weight"])
            plan, error = resolve_route(
                dispatch,
                vendor,
                workload["lane"],
                lanes,
                workload["prompt"],
                env,
            )
            if plan is None:
                items.append(
                    {
                        "id": workload["id"],
                        "requested_lane": workload["lane"],
                        "weight": workload["weight"],
                        "status": "unavailable",
                        "duration_seconds": 0.0,
                        "response_bytes": 0,
                        "error": error,
                    }
                )
                had_error = True
                continue
            if not args.run:
                items.append(
                    {
                        "id": workload["id"],
                        "requested_lane": workload["lane"],
                        "resolved_lane": plan.get("lane", workload["lane"]),
                        "model": plan.get("model", "-"),
                        "effort": plan.get("effort", "-"),
                        "weight": workload["weight"],
                        "status": "planned",
                        "duration_seconds": 0.0,
                        "response_bytes": 0,
                    }
                )
                continue
            provider_invoked = True
            item, runner_error = run_one(
                repo, vendor, plan, workload, args.timeout, env
            )
            items.append(item)
            had_error = had_error or runner_error
            if item["status"] == "passed":
                passed += 1
                score_earned += int(workload["weight"])

        quality = None
        if args.run and score_possible:
            quality = round((score_earned / score_possible) * 100, 2)
            if quality.is_integer():
                quality = int(quality)
        report["vendors"].append(
            {
                "vendor": vendor,
                "planned_calls": len(workloads),
                "passed": passed,
                "score_earned": score_earned,
                "score_possible": score_possible,
                "quality_percent": quality,
                "duration_seconds": round(time.monotonic() - vendor_started, 3),
                "cost": cost_report(vendor, len(workloads), costs),
                "workloads": items,
            }
        )
    report["provider_invoked"] = provider_invoked
    return report, 1 if had_error else 0


def print_human(report):
    invoked = "yes" if report["provider_invoked"] else "no"
    print(f"mode={report['mode']} provider_invoked={invoked}")
    for vendor in report["vendors"]:
        quality = vendor["quality_percent"]
        quality_text = "planned" if quality is None else f"{quality}%"
        cost = vendor["cost"]
        cost_text = "not supplied" if cost is None else f"USD {cost['estimated_total_usd']}"
        print(
            f"{vendor['vendor']}: calls={vendor['planned_calls']} "
            f"quality={quality_text} estimated_cost={cost_text}"
        )


def main(argv=None):
    args = parse_args(argv)
    try:
        report, return_code = execute(args)
    except BenchmarkError as exc:
        print(f"omnilane benchmark: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print_human(report)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
