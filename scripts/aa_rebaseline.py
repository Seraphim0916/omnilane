#!/usr/bin/env python3
"""Re-baseline config/aa-model-policy.json onto a newer AA Intelligence Index.

    fetch   download one AA model page and save the per-model records as an extract
    build   regenerate the registry from a saved extract (never from the network)
    report  write per-vendor evidence tables and the old-vs-new score diff
    matrix  show, per controller, the first reachable target of every lane
    lanes   print, per lane, each candidate with the measurements the lane is ordered on

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
    # what routing.yaml orders its lanes on
    "terminalBench40", "automationBenchPartialScore", "tauBanking", "mlcrOverall", "mmmuPro",
    "omniscienceBreakdown", "briefcaseBreakdown", "intelligenceIndexCost", "intelligenceIndexTimePerTask",
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
    ("claude/claude-opus-5-5", "claude", "claude-opus-5-5", "max", "adaptive",
     "claude-opus-5-5", "claude/claude-opus-5"),
    ("claude/claude-opus-5-5-xhigh", "claude", "claude-opus-5-5", "xhigh", "adaptive",
     "claude-opus-5-5-xhigh", "claude/claude-opus-5-xhigh"),
    ("claude/claude-opus-5-5-high", "claude", "claude-opus-5-5", "high", "adaptive",
     "claude-opus-5-5-high", "claude/claude-opus-5-high"),
    ("claude/claude-opus-5-5-medium", "claude", "claude-opus-5-5", "medium", "adaptive",
     "claude-opus-5-5-medium", "claude/claude-opus-5-medium"),
    ("claude/claude-opus-5-5-low", "claude", "claude-opus-5-5", "low", "adaptive",
     "claude-opus-5-5-low", "claude/claude-opus-5-low"),
    ("codex/gpt-5-3-codex", "codex", "gpt-5.3-codex", "xhigh", "reasoning",
     "gpt-5-3-codex", "codex/gpt-5-4"),
    # AA names no effort and no reasoning mode for Instant, so both stay unlabelled.
    ("codex/gpt-5-5-instant-06-26", "codex", "gpt-5.5-instant", None, "unspecified",
     "gpt-5-5-instant-06-26", "codex/gpt-5-5-non-reasoning"),
    ("gemini/gemini-3-5-flash-lite", "gemini", "gemini-3.5-flash-lite", "high", "unspecified",
     "gemini-3-5-flash-lite", "gemini/gemini-3-7-flash"),
    ("codex/gpt-oss-120b", "codex", "gpt-oss-120b", "high", "reasoning",
     "gpt-oss-120b", "codex/gpt-5-4"),
    ("codex/gpt-oss-120b-low", "codex", "gpt-oss-120b", "low", "reasoning",
     "gpt-oss-120b-low", "codex/gpt-5-4-low"),
    ("codex/gpt-oss-20b", "codex", "gpt-oss-20b", "high", "reasoning",
     "gpt-oss-20b", "codex/gpt-5-4"),
    ("codex/gpt-oss-20b-low", "codex", "gpt-oss-20b", "low", "reasoning",
     "gpt-oss-20b-low", "codex/gpt-5-4-low"),
    *[(f"codex/gpt-6-{family}" + ("" if effort == "max" else f"-{effort or 'non-reasoning'}"), "codex",
       f"gpt-6-{family}", effort, "non-reasoning" if effort is None else "reasoning",
       f"gpt-6-{family}" + ("" if effort == "max" else f"-{effort or 'non-reasoning'}"),
       f"codex/gpt-5-6-{family}" + ("" if effort == "max" else f"-{effort or 'non-reasoning'}"))
      for family in ("sol", "luna") for effort in ("max", "xhigh", "high", "medium", "low", None)],
]
NEW_ALIASES = {"grok-4.7": "grok-4.6", "gpt-6-sol": "gpt-6-astra",
               "gpt-6-luna": "gpt-6-astra"}  # new catalog model -> alias entry to clone


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
        if not isinstance(slug, str):
            continue
        # A model appears several times, each copy carrying a different subset of
        # fields; which copy comes first varies between fetches.
        merged = records.setdefault(slug, {})
        for key, value in obj.items():
            if merged.get(key) is None:
                merged[key] = value
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
    old = json.loads(Path(args.base).read_text())
    new = copy.deepcopy(old)
    # a second snapshot on the same day must not reuse the first one's id or overwrite its reports
    tag = as_of if args.revision == 1 else f"{as_of}-v{args.revision}"
    report = lambda vendor: f"docs/reports/aa-{vendor}-evidence-{tag}.md"  # noqa: E731

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
        # gemini selectors carry the effort in the model id, as target_row reads them
        row["transport_mapping"]["candidate_model_ids"] = [f"{model}-{effort}" if vendor == "gemini" else model]
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
    # aliases mirror scripts/configure.sh's catalog, so a new alias needs the model there too.
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
        id=f"aa-v{version}-{as_of}-v{args.revision}", benchmark_version=version, as_of=as_of,
        source={"extract": str(Path(args.extract).resolve().relative_to(REPO)),
                "page_url": extract["source_url"], "page_sha256": extract["page_sha256"],
                "fetched_at": extract["fetched_at"]},
        approval={"status": args.approval, "scope": f"aa-v{version}-rebaseline",
                  "source": f"docs/reports/aa-rebaseline-{tag}.md",
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
        # write where the rows point, so a same-day second snapshot never overwrites the first's report
        target = next((REPO / r["evidence_report"] for r in new["scored_configs"] if r["vendor"] == vendor),
                      out / f"aa-{vendor}-evidence-{as_of}.md")
        target.write_text("\n".join(lines) + "\n")
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


def _nested(*path):
    def read(record):
        for key in path:
            record = (record or {}).get(key)
        return record
    return read


MEASURES = {  # column title -> (reader, decimals, scale)
    "index": (_nested("intelligenceIndex"), 1, 1),
    "Terminal-Bench 4.0": (_nested("terminalBench40"), 3, 1),
    "Terminal-Bench 2.1": (_nested("terminalBench21"), 3, 1),
    "SciCode": (_nested("scicode"), 3, 1),
    "hallucination rate": (_nested("omniscienceBreakdown", "hallucinationRate"), 3, 1),
    "knowledge (omniscience)": (_nested("omniscience"), 1, 1),
    "HLE": (_nested("hle"), 3, 1),
    "GPQA": (_nested("gpqa"), 3, 1),
    "CritPt": (_nested("critpt"), 3, 1),
    "Briefcase analytical Elo": (_nested("briefcaseBreakdown", "analyticalQuality", "elo"), 0, 1),
    "Briefcase overall Elo": (_nested("briefcaseBreakdown", "overall", "elo"), 0, 1),
    "Briefcase presentation Elo": (_nested("briefcaseBreakdown", "presentation", "elo"), 0, 1),
    "GDPval": (_nested("gdpvalNormalized"), 3, 1),
    "AutomationBench": (_nested("automationBenchPartialScore"), 3, 1),
    "MMMU-Pro": (_nested("mmmuPro"), 3, 1),
    "mlcrOverall": (_nested("mlcrOverall"), 3, 1),
    "AA-LCR": (_nested("lcr"), 3, 1),
    "minutes / task": (_nested("intelligenceIndexTimePerTask"), 1, 1 / 60),
    "first answer token (s)": (_nested("timeToFirstAnswerToken", "total"), 0, 1),
    "index run cost ($)": (_nested("intelligenceIndexCost", "total"), 0, 1),
}
LANE_MEASURES = {
    "hardest-coding": ("Terminal-Bench 4.0", "SciCode", "hallucination rate", "index run cost ($)"),
    "bulk-mechanical": ("Terminal-Bench 4.0", "minutes / task", "index run cost ($)"),
    "triage": ("index", "index run cost ($)", "minutes / task"),
    "hard-judgment": ("HLE", "Briefcase analytical Elo", "CritPt", "hallucination rate", "index run cost ($)"),
    "taste-final": ("Briefcase overall Elo", "Briefcase presentation Elo", "GDPval", "index run cost ($)"),
    "consult": ("index", "index run cost ($)"),
    "ui-draft": ("MMMU-Pro", "Terminal-Bench 4.0", "index run cost ($)"),
    "long-context": ("mlcrOverall", "AA-LCR", "index run cost ($)"),
    "fast-agentic": ("AutomationBench", "minutes / task", "first answer token (s)", "index run cost ($)"),
    "live-search": ("knowledge (omniscience)", "hallucination rate", "index run cost ($)"),
    "coding-overflow": ("Terminal-Bench 4.0", "SciCode", "index run cost ($)"),
}
# Value-first ordering (the `value` command): lane -> (measure, near-tie band, second measure,
# how much worse a cheaper row may be on it, lowest ceiling to compute). Strict lanes trade
# quality for money only on a near-tie; the others also count a row as close when it costs at
# most half as much for a gap up to twice the band.
VALUE_LANES = {
    "hardest-coding": ("Terminal-Bench 4.0", 0.02, "SciCode", 0.05, 40),
    "bulk-mechanical": ("Terminal-Bench 4.0", 0.06, "minutes / task", 2.0, 38),
    "hard-judgment": ("HLE", 0.02, "Briefcase analytical Elo", 150, 40),
    "taste-final": ("Briefcase overall Elo", 30, "Briefcase presentation Elo", 100, 40),
    "ui-draft": ("MMMU-Pro", 0.01, "Terminal-Bench 4.0", 0.05, 40),
    "fast-agentic": ("AutomationBench", 0.03, "minutes / task", 1.0, 38),
    "long-context": ("mlcrOverall", 0.02, None, None, 30),
}
# A tool loop waits for every first token, so a slow starter is no fast row whatever it scores.
VALUE_CAPS = {"fast-agentic": ("first answer token (s)", 10)}
STRICT_LANES = {"hardest-coding", "hard-judgment", "taste-final", "long-context", "coding-overflow"}
VALUE_FAMILIES = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5",
                  "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra",
                  "grok-4.7", "grok-4.6", "gemini-3.8-flash"}
LOWER_IS_BETTER = {"minutes / task", "first answer token (s)", "hallucination rate"}


def cmd_value(args) -> int:
    """Per lane, the value pick for every caller ceiling and the chain those picks make."""
    records = json.loads(Path(args.extract).read_text())["records"]
    rows = json.loads(Path(args.registry).read_text())["scored_configs"]
    cost_of = MEASURES["index run cost ($)"][0]

    def measure(title, row):
        reader, _, scale = MEASURES[title]
        value = reader(records.get(row["aa_slug"]))
        return None if value is None else value * scale

    pool = [r for r in rows if r["reasoning"] != "non-reasoning" and r["effort"] != "max"
            and (r["model"].rsplit("-", 1)[0] if r["vendor"] == "gemini" else r["model"]) in VALUE_FAMILIES]
    for lane, (title, band, second, band2, floor) in VALUE_LANES.items():
        def pick(ceiling):
            reach = [r for r in pool if r["score"] <= ceiling and measure(title, r) is not None
                     and cost_of(records.get(r["aa_slug"])) is not None]
            if lane in VALUE_CAPS:
                cap_title, cap = VALUE_CAPS[lane]
                reach = [r for r in reach if measure(cap_title, r) is not None and measure(cap_title, r) <= cap]
            if not reach:
                return None
            cost = lambda r: cost_of(records.get(r["aa_slug"]))  # noqa: E731
            best = max(reach, key=lambda r: (measure(title, r), -cost(r)))
            near = []
            for r in reach:
                gap = measure(title, best) - measure(title, r)
                wide = lane not in STRICT_LANES and gap <= 2 * band and cost(r) <= 0.5 * cost(best)
                if not (gap <= band or wide):
                    continue
                if second and measure(second, best) is not None:
                    if measure(second, r) is None:
                        continue
                    worse = measure(second, r) - measure(second, best)
                    if (worse if second in LOWER_IS_BETTER else -worse) > band2:
                        continue
                near.append(r)
            return min(near, key=lambda r: (cost(r), -measure(title, r)))
        ceilings = range(max(r["score"] for r in rows), floor - 1, -1)
        picks = []
        for ceiling in ceilings:
            chosen = pick(ceiling)
            if chosen and chosen not in picks:
                picks.append(chosen)
        print(f"\n**{lane}** — {title}, band {band}" + (f"; {second} within {band2}" if second else "")
              + ("; near-ties only" if lane in STRICT_LANES else "; wide band") + "\n")
        for r in picks:
            print(f"- {r['vendor']} {r['model']} {r['effort']} (score {r['score']})")
        for ceiling in ceilings:
            want, got = pick(ceiling), next((r for r in picks if r["score"] <= ceiling), None)
            if want is not got:
                print(f"- ceiling {ceiling}: the chain gives {got and got['id']}, the rule prefers {want and want['id']}")
    return 0


def cmd_lanes(args) -> int:
    """Per lane, every candidate with the measurements that lane is ordered on."""
    records = json.loads(Path(args.extract).read_text())["records"]
    rows = json.loads(Path(args.registry).read_text())["scored_configs"]
    for lane, chain in lane_table(Path(args.routing)).items():
        titles = LANE_MEASURES.get(lane, ("index",))
        print(f"\n**{lane}**\n\n| # | candidate | score | " + " | ".join(titles) + " |")
        print("|---|---|---|" + "---|" * len(titles))
        for index, (vendor, model, effort) in enumerate(chain, 1):
            row = target_row(rows, vendor, model, effort)
            name = f"{vendor} {model}" + (f" {effort}" if effort else "")
            if row is None:
                print(f"| {index} | {name} | not scored | " + " | ".join("—" for _ in titles) + " |")
                continue
            cells = []
            for title in titles:
                reader, places, scale = MEASURES[title]
                value = reader(records.get(row["aa_slug"]))
                cells.append("not published" if value is None else f"{value * scale:.{places}f}")
            print(f"| {index} | {name} | {row['score']} | " + " | ".join(cells) + " |")
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
    build.add_argument("--base", default=str(REGISTRY),
                       help="the registry to re-score; pass the previous snapshot to rebuild from scratch")
    build.add_argument("--approval", default="proposed", choices=("proposed", "approved"))
    build.add_argument("--revision", type=int, default=1, help="snapshot number within the as-of day")
    report = sub.add_parser("report")
    report.add_argument("--old", required=True, help="the previous registry file")
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--registry", default=str(REGISTRY))
    matrix.add_argument("--routing", default=str(REPO / "routing.yaml"))
    matrix.add_argument("--controller", nargs="+", required=True, help="registry config ids")
    value = sub.add_parser("value")
    value.add_argument("--extract", required=True)
    value.add_argument("--registry", default=str(REGISTRY))
    lanes = sub.add_parser("lanes")
    lanes.add_argument("--extract", required=True)
    lanes.add_argument("--registry", default=str(REGISTRY))
    lanes.add_argument("--routing", default=str(REPO / "routing.yaml"))
    args = parser.parse_args()
    return {"fetch": cmd_fetch, "build": cmd_build, "report": cmd_report, "matrix": cmd_matrix,
            "lanes": cmd_lanes, "value": cmd_value}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
