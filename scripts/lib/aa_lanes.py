#!/usr/bin/env python3
"""Add eligible_lanes to a refused AA decision.

A refusal that only says no leaves a model caller guessing. This reads the
decision on stdin and appends the lanes whose chain still holds a target at or
below the caller's effective ceiling, by registry score alone: no CLI is probed
and nothing is dispatched. Output is one JSON line, the decision plus the list.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_policy  # noqa: E402
import caller_identity  # noqa: E402


def lanes(paths: list[Path]) -> dict[str, list[list[str]]]:
    """Lane -> chain segments; the first file naming a lane wins, as in dispatch."""
    table: dict[str, list[list[str]]] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0]
            if not line.strip() or line[0] in " \t" or ":" not in line:
                continue
            name, chain = line.split(":", 1)
            name = name.strip()
            if not name or name in table or not chain.strip():
                continue
            segments = []
            for segment in chain.split("|"):
                try:
                    fields = shlex.split(segment)
                except ValueError:
                    fields = []
                if fields:
                    segments.append(fields)
            table[name] = segments
    return table


def eligible(registry: dict, table: dict[str, list[list[str]]], ceiling: int) -> list[dict]:
    found = []
    for lane, segments in table.items():
        for fields in segments:
            vendor = fields[0]
            if vendor in ("off", "vote") or len(fields) < 2:
                continue
            effort = fields[2] if len(fields) > 2 and fields[2] != "-" else None
            row, _ = caller_identity.resolve(registry, vendor, fields[1], effort)
            if row is None or row["score"] > ceiling:
                continue
            found.append({
                "lane": lane,
                "target": row["id"],
                "score": row["score"],
                "transport_verified": row["transport_mapping"].get("runtime_verified") is True,
            })
            break
    return found


def lane_requirement(registry: dict, segments: list[list[str]], caller: dict | None) -> dict | None:
    """The cheapest target in one lane, and the caller effort that would reach it."""
    rows = []
    for fields in segments:
        if fields[0] in ("off", "vote") or len(fields) < 2:
            continue
        effort = fields[2] if len(fields) > 2 and fields[2] != "-" else None
        row, _ = caller_identity.resolve(registry, fields[0], fields[1], effort)
        if row is not None:
            rows.append(row)
    if not rows:
        return None
    cheapest = min(rows, key=lambda row: (row["score"], row["id"]))
    required = None
    if isinstance(caller, dict):
        reaching = sorted((row for row in registry["scored_configs"]
                           if row["vendor"] == caller.get("vendor") and row["model"] == caller.get("model")
                           and row["score"] >= cheapest["score"]),
                          key=lambda row: (row["score"], row["id"]))
        if reaching:
            required = reaching[0]["effort"] or "none"
    return {"target": cheapest["id"], "score": cheapest["score"], "required_caller_effort": required}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", required=True)
    parser.add_argument("--routing", action="append", default=[], type=Path,
                        help="routing file, highest precedence first; may repeat")
    parser.add_argument("--lane", help="the lane that was refused")
    args = parser.parse_args(argv)
    raw = sys.stdin.read()
    try:
        decision = json.loads(raw)
        ceiling = decision.get("effective_ceiling")
        if decision.get("allowed") is False and type(ceiling) is int:
            registry, _ = aa_policy.load_registry(args.registry)
            table = lanes(args.routing)
            decision["eligible_lanes"] = eligible(registry, table, ceiling)
            if args.lane in table and decision.get("code") == "target-above-effective-ceiling":
                # The decision describes the last candidate tried; the lane may hold a cheaper one.
                need = lane_requirement(registry, table[args.lane], decision.get("caller"))
                if need is not None:
                    decision["lane_requirement"] = dict(need, lane=args.lane)
                    if decision.get("inherited_ceiling") == decision.get("caller_score"):
                        decision["required_caller_effort"] = need["required_caller_effort"]
                        effort = need["required_caller_effort"]
                        held = (" The harness recorded no effort, so this caller is held to its "
                                "model's floor." if decision.get("caller_degraded") else "")
                        decision["reason"] = (
                            f"lane {args.lane}'s cheapest target {need['target']} scores "
                            f"{need['score']}, above this caller's ceiling of {ceiling}.{held} "
                            + (f"Relaunch the calling session at effort {effort} or higher, or have "
                               "a human operator dispatch it." if effort else
                               "No effort of the caller's model reaches it; a stronger caller model "
                               "or a human operator has to dispatch it."))
        sys.stdout.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")) + "\n")
    except (ValueError, OSError, aa_policy.PolicyError, KeyError, TypeError):
        # Guidance is best effort; the refusal itself must always get through.
        sys.stdout.write(raw if raw.endswith("\n") else raw + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
