#!/usr/bin/env python3
"""Re-baseline config/aa-model-policy.json onto a newer AA Intelligence Index.

    fetch   download one AA model page and save the per-model records as an extract
    build   regenerate the registry from a saved extract (never from the network)
    report  write per-vendor evidence tables and the old-vs-new score diff
    matrix  show, per controller, the first reachable target of every lane

The registry is an approval artifact: build only rewrites the file. Pinning its
sha256 in scripts/lib/aa_policy.py and re-signing the host overlay stay manual.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "config/aa-model-policy.json"
PAGE = "https://artificialanalysis.ai/models/{slug}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
VENDOR_SLUG = re.compile(r"^(gpt|claude|gemini|grok)-")
KEEP = (
    "name", "intelligenceIndex", "intelligenceIndexIsEstimated", "releaseDate", "deprecated",
    "contextWindowTokens", "terminalBench21", "terminalbenchHard", "scicode", "hle", "gpqa",
    "omniscience", "tau2", "lcr", "ifbench", "critpt", "gdpvalNormalized", "itBenchSre",
    "apexAgents", "price1mInputTokens", "price1mOutputTokens", "price1mBlended7To2To1",
    "intelligenceIndexOutputTokensPerTask", "timeToFirstAnswerToken",
)

# Rows this snapshot adds. identity mirrors the sibling rows of the same model.
NEW_ROWS = [
    # (id, vendor, model, effort, reasoning, aa_slug, copy transport/shape from)
    ("grok/grok-4-7", "grok", "grok-4.7", "xhigh", "reasoning", "grok-4-7", "grok/grok-4-6-xhigh"),
    ("grok/grok-4-7-high", "grok", "grok-4.7", "high", "reasoning", "grok-4-7-high", "grok/grok-4-6"),
    ("claude/claude-sonnet-5-xhigh", "claude", "claude-sonnet-5", "xhigh", "adaptive",
     "claude-sonnet-5-xhigh", "claude/claude-sonnet-5"),
    ("claude/claude-sonnet-5-high", "claude", "claude-sonnet-5", "high", "adaptive",
     "claude-sonnet-5-high", "claude/claude-sonnet-5"),
    ("claude/claude-sonnet-5-medium", "claude", "claude-sonnet-5", "medium", "adaptive",
     "claude-sonnet-5-medium", "claude/claude-sonnet-5"),
    ("claude/claude-sonnet-5-low", "claude", "claude-sonnet-5", "low", "adaptive",
     "claude-sonnet-5-low", "claude/claude-sonnet-5"),
]
NEW_ALIASES = {"grok-4.7": "grok-4.6"}  # new catalog model -> alias entry to clone


def half_up(value: float) -> int:
    # round() is banker's rounding; a registry score must not depend on parity.
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dump(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def parse_records(body: str) -> dict[str, dict]:
    """The page embeds every model as escaped JSON; take each object that carries a score."""
    text = body.replace('\\"', '"').replace("\\\\", "\\")
    records: dict[str, dict] = {}
    for match in re.finditer(r'"intelligenceIndex":', text):
        start, depth = match.start(), 0
        while start > 0:
            char = text[start]
            if char == "}":
                depth += 1
            elif char == "{":
                if depth == 0:
                    break
                depth -= 1
            start -= 1
        end, depth = match.start(), 0
        while end < len(text):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        try:
            obj = json.loads(text[start:end + 1])
        except ValueError:
            continue
        slug = obj.get("slug")
        if isinstance(slug, str) and slug not in records:
            records[slug] = obj
    return records


def cmd_fetch(args) -> int:
    url = PAGE.format(slug=args.slug)
    body = subprocess.run(["curl", "-sSL", "--fail", "-A", UA, "--max-time", "120", url],
                          check=True, capture_output=True, text=True).stdout
    version = re.search(r"Intelligence Index v([0-9.]+)", body)
    if not version:
        sys.exit("fetch: the page names no Intelligence Index version")
    records = parse_records(body)
    kept = {}
    for slug, obj in sorted(records.items()):
        if not VENDOR_SLUG.match(slug) or obj.get("intelligenceIndex") is None:
            continue
        row = {key: obj.get(key) for key in KEEP}
        effort = obj.get("effort")
        row["effort_label"] = effort.get("slug") if isinstance(effort, dict) else effort
        kept[slug] = row
    extract = {
        "source_url": url,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "page_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "benchmark_version": version.group(1),
        "records_on_page": len(records),
        "records": kept,
    }
    Path(args.out).write_bytes(dump(extract))
    print(f"fetch: AA v{extract['benchmark_version']}, {len(records)} records on page, "
          f"{len(kept)} vendor records kept -> {args.out}")
    return 0


def rescore(row: dict, record: dict, version: str, as_of: str, report: str) -> None:
    row["score"] = half_up(record["intelligenceIndex"])
    row["score_raw"] = round(record["intelligenceIndex"], 2)
    row["estimated"] = bool(record["intelligenceIndexIsEstimated"])
    row["evidence_marker"] = "estimated" if row["estimated"] else "unmarked"
    row["benchmark_version"], row["as_of"], row["evidence_report"] = version, as_of, report


def cmd_build(args) -> int:
    extract = json.loads(Path(args.extract).read_text())
    records, version, as_of = extract["records"], extract["benchmark_version"], args.as_of
    old = json.loads(REGISTRY.read_text())
    new = copy.deepcopy(old)
    report = lambda vendor: f"docs/reports/aa-{vendor}-evidence-{as_of}.md"  # noqa: E731

    scored, dropped = [], []
    for row in new["scored_configs"]:
        record = records.get(row["aa_slug"])
        if record is None:
            dropped.append(row)
            continue
        rescore(row, record, version, as_of, report(row["vendor"]))
        scored.append(row)
    by_id = {row["id"]: row for row in old["scored_configs"]}
    for cid, vendor, model, effort, reasoning, slug, shape in NEW_ROWS:
        if cid in {row["id"] for row in scored}:
            continue
        if slug not in records:
            sys.exit(f"build: {slug} is not in the extract")
        row = copy.deepcopy(by_id[shape])
        row.update(id=cid, vendor=vendor, model=model, effort=effort, reasoning=reasoning,
                   fallback=None, aa_slug=slug, source_urls=[PAGE.format(slug=slug)])
        row["transport_mapping"]["candidate_model_ids"] = [model]
        rescore(row, records[slug], version, as_of, report(vendor))
        scored.append(row)
    scored.sort(key=lambda row: (-row["score"], -row["score_raw"], row["id"]))
    new["scored_configs"] = scored

    added = {(v, m, e, r) for _, v, m, e, r, _, _ in NEW_ROWS}
    unknown = [row for row in new["unknown_configs"]
               if (row["vendor"], row["model"], row["effort"], row["reasoning"]) not in added]
    for row in unknown:
        row["benchmark_version"], row["as_of"] = version, as_of
        row["evidence_report"] = report(row["vendor"])
    for row in dropped:
        unknown.append({
            "id": "/".join(str(row[key]) for key in ("vendor", "model", "effort", "reasoning")).replace("None", "none"),
            **{key: row[key] for key in ("vendor", "model", "effort", "reasoning", "fallback")},
            "score": None, "estimated": None, "benchmark_version": version, "as_of": as_of,
            "status": "unknown", "authority_eligible": False,
            "reason": f"AA v{version} no longer lists {row['aa_slug']}; the earlier score is not carried over",
            "source_urls": [], "evidence_report": report(row["vendor"]), "mapping_status": "unknown",
            "transport_mapping": {"status": "unknown", "runtime_verified": False, "resolved_config_id": None},
        })
    new["unknown_configs"] = unknown

    for row in new["reference_configs"]:
        if row["aa_slug"] in records:
            rescore(row, records[row["aa_slug"]], version, as_of, report(row["vendor"]))

    live_ids = {row["id"] for row in scored}
    for alias in new["aliases"]:
        alias["candidate_config_ids"] = [cid for cid in alias["candidate_config_ids"] if cid in live_ids]
        for cid, vendor, model, *_ in NEW_ROWS:
            if (alias["catalog_vendor"], alias["catalog_model"]) == (vendor, model) \
                    and cid not in alias["candidate_config_ids"]:
                alias["candidate_config_ids"].append(cid)
    have = {(alias["catalog_vendor"], alias["catalog_model"]) for alias in new["aliases"]}
    for model, source in NEW_ALIASES.items():
        template = next(a for a in new["aliases"] if a["catalog_model"] == source)
        if (template["catalog_vendor"], model) not in have:
            clone = copy.deepcopy(template)
            clone["catalog_model"] = model
            clone["candidate_config_ids"] = [cid for cid, _, m, *_ in NEW_ROWS if m == model]
            new["aliases"].insert(new["aliases"].index(template), clone)

    vendors: dict[str, int] = {}
    for row in scored:
        vendors[row["vendor"]] = vendors.get(row["vendor"], 0) + 1
    new["coverage"] = {"scored_configs": len(scored), "reference_configs": len(new["reference_configs"]),
                       "unknown_configs": len(unknown), "aliases": len(new["aliases"]),
                       "by_vendor": {v: vendors[v] for v in old["coverage"]["by_vendor"]}}
    new["snapshot"].update(
        id=f"aa-v{version}-{as_of}-v1", benchmark_version=version, as_of=as_of,
        source={"extract": str(Path(args.extract).resolve().relative_to(REPO)),
                "page_url": extract["source_url"], "page_sha256": extract["page_sha256"],
                "fetched_at": extract["fetched_at"]},
        approval={"status": args.approval, "scope": f"aa-v{version}-rebaseline",
                  "source": f"docs/reports/aa-rebaseline-{as_of}.md",
                  "estimated_scores": "approved_provisional" if args.approval == "approved"
                  else "provisional_pending_review"})
    new.setdefault("schema_notes", {})["score_rounding"] = (
        "score is score_raw rounded half-up to an integer; score_raw is the AA index to two decimals")
    REGISTRY.write_bytes(dump(new))
    print(f"build: {len(scored)} scored ({sum(r['estimated'] for r in scored)} estimated), "
          f"{len(unknown)} unknown, dropped {[r['id'] for r in dropped]}")
    print(f"build: sha256 {hashlib.sha256(REGISTRY.read_bytes()).hexdigest()}")
    return 0


def cmd_report(args) -> int:
    old = {row["id"]: row for row in json.loads(Path(args.old).read_text())["scored_configs"]}
    new = json.loads(REGISTRY.read_text())
    as_of, version = new["snapshot"]["as_of"], new["snapshot"]["benchmark_version"]
    out = REPO / "docs/reports"
    out.mkdir(parents=True, exist_ok=True)
    for vendor in new["coverage"]["by_vendor"]:
        lines = [f"# AA v{version} evidence: {vendor} ({as_of})", "",
                 f"Generated by `scripts/aa_rebaseline.py report` from `{new['snapshot']['source']['extract']}` "
                 f"(page sha256 `{new['snapshot']['source']['page_sha256'][:16]}…`).", "",
                 "| config | effort | raw | score | estimated | previous | source |", "|---|---|---|---|---|---|---|"]
        for row in new["scored_configs"]:
            if row["vendor"] != vendor:
                continue
            before = old.get(row["id"])
            lines.append(f"| {row['id']} | {row['effort']} | {row['score_raw']} | {row['score']} | "
                         f"{'yes' if row['estimated'] else 'no'} | "
                         f"{before['score'] if before else 'new'} | {row['source_urls'][0]} |")
        gone = [cid for cid, row in old.items() if row["vendor"] == vendor
                and cid not in {r["id"] for r in new["scored_configs"]}]
        if gone:
            lines += ["", "No longer listed by AA, moved to unknown_configs: " + ", ".join(gone)]
        (out / f"aa-{vendor}-evidence-{as_of}.md").write_text("\n".join(lines) + "\n")
    print(f"report: wrote {len(new['coverage']['by_vendor'])} vendor reports under {out}")
    return 0


def lane_table(path: Path) -> dict[str, list[tuple[str, str, str | None]]]:
    table = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([a-z][a-z-]*):\s*(.+)$", line.split("#")[0].rstrip())
        if not match or match.group(2).split()[0] in ("off", "vote"):
            continue
        chain = []
        for segment in match.group(2).split("|"):
            parts = segment.split()
            if len(parts) >= 2:
                chain.append((parts[0], parts[1], parts[2] if len(parts) > 2 and parts[2] != "-" else None))
        table[match.group(1)] = chain
    return table


def target_row(rows: list[dict], vendor: str, model: str, effort: str | None) -> dict | None:
    hits = [row for row in rows if row["vendor"] == vendor and row["reasoning"] != "non-reasoning"
            and (f"{row['model']}-{row['effort']}" == model if vendor == "gemini"
                 else row["model"] == model and row["effort"] == effort)]
    return hits[0] if len(hits) == 1 else None


def cmd_matrix(args) -> int:
    """Score-only view: it ignores transport verification, which is per host."""
    rows = json.loads(Path(args.registry).read_text())["scored_configs"]
    by_id = {row["id"]: row for row in rows}
    table = lane_table(Path(args.routing))
    print("| controller (ceiling) | " + " | ".join(table) + " |")
    print("|---|" + "---|" * len(table))
    for cid in args.controller:
        ceiling = by_id[cid]["score"]
        cells = []
        for chain in table.values():
            cell = "none"
            for index, (vendor, model, effort) in enumerate(chain):
                row = target_row(rows, vendor, model, effort)
                if row and row["score"] <= ceiling:
                    cell = f"{row['id'].split('/')[1]} {row['score']}" + (f" (#{index + 1})" if index else "")
                    break
            cells.append(cell)
        print(f"| {cid.split('/')[1]} ({ceiling}) | " + " | ".join(cells) + " |")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--slug", default="grok-4-7")
    fetch.add_argument("--out", required=True)
    build = sub.add_parser("build")
    build.add_argument("--extract", required=True)
    build.add_argument("--as-of", required=True)
    build.add_argument("--approval", default="proposed", choices=("proposed", "approved"))
    report = sub.add_parser("report")
    report.add_argument("--old", required=True, help="the previous registry file")
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--registry", default=str(REGISTRY))
    matrix.add_argument("--routing", default=str(REPO / "routing.yaml"))
    matrix.add_argument("--controller", nargs="+", required=True, help="registry config ids")
    args = parser.parse_args()
    return {"fetch": cmd_fetch, "build": cmd_build, "report": cmd_report, "matrix": cmd_matrix}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
