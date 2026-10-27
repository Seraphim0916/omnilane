"""Read-only, release-pinned transport evidence compatibility.

Scores and their provenance never authorize transport reuse. Unknown new row
fields do affect compatibility, so extending the schema fails closed by default.
The archive is shipped by the package, never selected by the environment.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
ARCHIVE = Path(__file__).resolve().parents[2] / "config/transport-compatibility.json"
APPROVED_ARCHIVE_SHA256 = "b8fbfffa0fd4a37ede37a7b0bf64899f94011aab37e173a92430cf05c0702f07"
SCORE_FIELDS = frozenset(("score", "score_raw", "estimated", "benchmark_version", "as_of",
                          "aa_slug", "source_urls", "evidence_report", "evidence_marker"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def row_contract(row):
    contract = {key: value for key, value in row.items() if key not in SCORE_FIELDS}
    # These are annotations, not arguments sent to the provider. Unknown fields
    # remain significant until explicitly reviewed as non-selector metadata.
    contract["transport_mapping"] = {key: value for key, value in row["transport_mapping"].items()
                                     if key not in ("status", "runtime_verified", "reason")}
    return contract


def selector_contract(row):
    import build_overlay
    proven = build_overlay.PROVEN.get(row["id"])
    if proven is None:
        return None
    selector, model, _ = proven
    return dict(vendor=row["vendor"], selector_type=selector, runtime_model=model,
                runtime_effort=row["effort"],
                cli_flag="--reasoning-effort" if selector == "cli_reasoning_effort" else None)


def engine_contract():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("probe.py", "probe_sweep.py")}


def archive():
    import aa_policy
    value, actual = aa_policy._read_json(ARCHIVE)
    aa_policy._check(actual == APPROVED_ARCHIVE_SHA256, "unapproved transport compatibility archive")
    aa_policy._check(value.get("compatibility_version") == VERSION,
                     "unsupported transport compatibility version")
    return value, actual


def source_rows(overlay, registry):
    """Only packaged source semantics can explain a legacy overlay's row IDs."""
    import aa_policy
    value, archive_sha = archive()
    expected = os.environ.get("OMNILANE_AA_COMPATIBILITY_SHA256")
    aa_policy._check(not expected or expected == archive_sha,
                     "transport compatibility changed after initial decision")
    source = value["snapshots"].get(overlay.get("snapshot_id"))
    if overlay.get("snapshot_id") == registry["snapshot"]["id"]:
        rows = registry["scored_configs"]
    else:
        aa_policy._check(source is not None,
                         "unknown transport source snapshot; run omnilane resign --check "
                         "to inspect the installed evidence before re-verifying")
        rows = source["rows"]
    return {r["id"]: r for r in rows}, source, archive_sha


def equivalent(old, new, source):
    return (source is not None and source.get("engine") == engine_contract()
            and row_contract(old) == row_contract(new)
            and source["selectors"].get(old["id"]) == selector_contract(new))


def expired(value):
    import aa_policy
    if value is None:
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        aa_policy._check(stamp.tzinfo is not None, "transport expiry requires a timezone")
    except (AttributeError, TypeError, ValueError) as error:
        raise aa_policy.PolicyError("invalid transport evidence expiry") from error
    return stamp <= datetime.now(timezone.utc)


def checked_descriptor(row, selector, files, descriptor):
    """Check positive or missing verdicts against complete source-bound raw proof.

    An explicit failure can never become a pass. Cached positive metadata cannot
    override a negative response, an incomplete record, or another row's request.
    """
    if "verdict" in descriptor and descriptor["verdict"] != "pass":
        return descriptor
    import probe
    import probe_sweep
    from probe_failure import contextual_failure

    result = ("fail", "incomplete-legacy-evidence", None, "selector-only")
    try:
        vendor = row["vendor"]
        if not selector or selector["vendor"] != vendor or selector["runtime_effort"] != row["effort"]:
            raise ValueError("source request contract missing")
        model = selector["runtime_model"]
        effort = None if vendor == "gemini" else row["effort"]
        expected = probe_sweep.command(vendor, model, effort)
        actual = descriptor.get("command")
        if not isinstance(actual, list) or not actual or not all(isinstance(p, str) for p in actual):
            raise ValueError("request command missing")
        # Executables are pinned separately. The private Gemini app directory is
        # per-probe state, not a model/effort selector. All other arguments count.
        def request(argv):
            return [p for p in argv[1:] if not (vendor == "gemini" and p.startswith("--app_data_dir="))]
        if request(actual) != request(expected):
            result = ("fail", "source-request-contract-mismatch", None, "selector-only")
        elif type(descriptor.get("exit_code")) is int and type(descriptor.get("timed_out")) is bool:
            streams = {suffix: Path(files[suffix]["path"]).read_text(errors="replace")
                       for suffix in ("stdout", "stderr")}
            extra = {suffix: Path(files[suffix]["path"]).read_text(errors="replace")
                     for suffix in ("rollout", "cli_log") if suffix in files}
            result = probe.verdict(descriptor, streams["stdout"], streams["stderr"],
                                   "agy" if vendor == "gemini" else vendor,
                                   probe_sweep.TOKENS[vendor], extra)
            result = contextual_failure(result, descriptor, streams["stdout"], streams["stderr"])
            if result[0] == "pass" and descriptor.get("observed_model") is not None \
                    and descriptor["observed_model"] != result[2]:
                result = ("fail", "probe-metadata-mismatch", result[2], "selector-only")
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Missing/incomplete old evidence cannot authorize a mapping.
    return dict(descriptor, **dict(zip(
        ("verdict", "verdict_reason", "observed_model", "evidence_tier"), result)))


def proof_records(overlay, rows=None, source=None):
    """Check the manifest and every nested raw proof; no log content is returned."""
    import aa_policy
    if rows is None:
        import build_overlay
        rows, source, _ = source_rows(overlay, build_overlay.REGISTRY)
    records = {}
    for evidence in overlay["evidence"]:
        if evidence.get("vendor") is not None:
            continue
        manifest, _ = aa_policy._read_json(evidence["path"])
        runs = manifest.get("probe_runs")
        aa_policy._check(isinstance(runs, dict), "transport evidence has no probe manifest")
        for cid, files in runs.items():
            aa_policy._check(cid not in records and isinstance(files, dict) and "json" in files,
                             "duplicate or incomplete transport proof")
            for entry in files.values():
                # Probe streams may be large; hash them without parsing or retaining contents.
                aa_policy._check(isinstance(entry, dict), "invalid transport proof entry")
                aa_policy._check(aa_policy.evidence_digest(entry["path"]) == entry["sha256"],
                                 "transport probe evidence changed")
            descriptor, _ = aa_policy._read_json(files["json"]["path"])
            row = rows.get(cid)
            selector = source["selectors"].get(cid) if source else selector_contract(row) if row else None
            descriptor = checked_descriptor(row, selector, files, descriptor)
            aa_policy._check(descriptor.get("verdict") == "pass",
                             "transport proof did not pass")
            records[cid] = (files, descriptor)
    return records


def provenance(row, snapshot_id, files, descriptor):
    return dict(source_snapshot_id=snapshot_id, source_row_digest=digest(row_contract(row)),
                transport_contract_digest=digest(dict(version=VERSION, engine=engine_contract(),
                                                      selector=selector_contract(row))),
                evidence_refs=files, verified_at=descriptor.get("probed_at"))
