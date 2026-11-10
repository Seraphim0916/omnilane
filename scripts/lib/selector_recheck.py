"""Retain selectors after environmental failures without retaining dispatch authority."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping

import build_overlay
import probe_failure
import probe_sweep


def summary_ids(value):
    """Legacy counts signal a condition but do not identify any selectors."""
    if isinstance(value, Mapping) and "config_ids" in value:
        value = value["config_ids"]
    if not isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return set()
    return {cid for cid in value if isinstance(cid, str)}


def has_unretained(value, retained):
    if not value:
        return False
    ids = summary_ids(value)
    # A count alone cannot prove that every failed row is retained.
    return not ids or bool(ids - summary_ids(retained))


def pending_rows(overlay, vendor=None):
    return {item["config_id"]: item for item in overlay.get("unproven", [])
            if item.get("pending_recheck") and
            (vendor is None or build_overlay.ROWS.get(item["config_id"], {}).get("vendor") == vendor)}


def retained_mappings(overlay, vendor):
    mappings = {m["config_id"]: m for m in overlay.get("mappings", [])
                if m["identity"]["vendor"] == vendor}
    for cid, item in pending_rows(overlay, vendor).items():
        mappings[cid] = item["pending_recheck"]["mapping"]
    return mappings


def regressions(root, overlay, vendor, result, *, expired=(), deferred=()):
    """Normalize new evidence; only a currently mapped row may hold the vendor."""
    for key in ("passed", "failed"):
        value = result[key]
        if value and not isinstance(value, (Mapping, list, tuple, set, frozenset)):
            raise ValueError("selector sweep " + key + " requires config IDs, not a count")
        # Unlike display summaries, proof reports must retain duplicates so the
        # retry validator can reject ambiguous evidence rather than repair it.
        result[key] = list(value) if value else []
    previous = retained_mappings(overlay, vendor)
    live = {m["config_id"] for m in overlay.get("mappings", []) if m["identity"]["vendor"] == vendor}
    pending = pending_rows(overlay, vendor)
    entries = {e["config_id"]: e for e in probe_sweep.plan(vendor)}
    environmental, conclusive = {}, {}
    for cid in sorted(set(result["passed"]) | set(result["failed"])):
        if cid in deferred:
            continue
        entry = entries[cid]
        descriptor, verdict = probe_failure.judge(root, entry, vendor,
                                         probe_sweep.command(vendor, entry["model"], entry["effort"]),
                                         probe_sweep.TOKENS[vendor])
        if (verdict[0] == "pass") != (cid in result["passed"]):
            raise ValueError("sweep result disagrees with raw selector evidence")
        original = dict(descriptor)
        if cid in previous and cid not in expired:
            weaker = (verdict[0] == "pass" and probe_failure.TIER_RANK[verdict[3]] <
                      probe_failure.TIER_RANK[previous[cid].get("evidence_tier", "selector-only")])
            if weaker and cid in pending:
                # This can only demote evidence. Keeping the minimum in the
                # anchored descriptor lets later raw checks reproduce the failure.
                descriptor["required_evidence_tier"] = previous[cid]["evidence_tier"]
                verdict = ("fail", "evidence-tier-regression", verdict[2], verdict[3])
                result["passed"].remove(cid)
                result["failed"].append(cid)
            if verdict[0] == "fail":
                if probe_failure.environment_reason(verdict[1]):
                    environmental[cid] = verdict[1]
                elif cid in live:
                    conclusive[cid] = verdict[1]
            elif weaker:
                conclusive[cid] = "evidence-tier-regression"
        descriptor.update(zip(("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict))
        if descriptor != original:
            (root / "evidence" / f"{entry['name']}.json").write_text(json.dumps(descriptor, indent=2) + "\n")
    return environmental, conclusive


def retain_pending(staged, overlay, vendors, checked_at, *, allow_shrink=False, expired=(), deferred=()):
    """Keep the old mapping for audit only, next to anchored negative evidence.

    Placing it under unproven prevents old proof from acquiring the new CLI pin.
    Neither the loader nor the retry engine consumes this mapping as authority.
    """
    for vendor in vendors:
        previous = retained_mappings(overlay, vendor)
        prior_pending = pending_rows(overlay, vendor)
        for item in staged.get("unproven", []):
            cid = item["config_id"]
            if cid not in previous or cid in expired or not probe_failure.environment_reason(item.get("verdict_reason")):
                continue
            if allow_shrink:
                item.pop("pending_recheck", None)
                continue
            prior = prior_pending.get(cid, {}).get("pending_recheck", {})
            if cid in deferred and prior:
                item["pending_recheck"] = copy.deepcopy(prior)
                continue
            item["pending_recheck"] = {
                "mapping": copy.deepcopy(previous[cid]), "reason": item["verdict_reason"],
                "source_snapshot_id": prior.get("source_snapshot_id", overlay["snapshot_id"]),
                "at": checked_at, "consecutive_runs": prior.get("consecutive_runs", 0) + 1,
            }


def partial_summary(overlay, counts, vendors):
    result = {}
    for vendor in vendors:
        rows = pending_rows(overlay, vendor)
        if rows:
            result[vendor] = dict(verified_count=counts[vendor], pending_count=len(rows),
                                  config_ids=sorted(rows))
    return result


def deterministic_rows(overlay, vendor=None):
    """Safe diagnostic reasons for completed failures that are not retry candidates."""
    return {item["config_id"]: probe_failure.public_reason(item["verdict_reason"])
            for item in overlay.get("unproven", []) if item.get("probed_at")
            and item.get("verdict_reason") and not probe_failure.environment_reason(item["verdict_reason"])
            and (vendor is None or build_overlay.ROWS.get(item["config_id"], {}).get("vendor") == vendor)}
