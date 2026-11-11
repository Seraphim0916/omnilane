"""Read-only presentation of negative evidence; never grants retry authority."""

from __future__ import annotations

import json
import json_depth
import shlex
from pathlib import Path

from probe_failure import environment_reason, public_reason
from evidence_integrity import INVALIDATED_REASON, FAILED_INVALIDATED_REASON


def read_object(path: Path) -> dict:
    try:
        raw = path.read_text()
    except (OSError, ValueError):
        return {}
    try:
        value = json_depth.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def validate_unproven(overlay: dict) -> list[dict]:
    """Reject malformed diagnostic metadata before policy or presentation uses it."""
    if not isinstance(overlay, dict):
        raise ValueError("overlay must be an object containing unproven metadata")
    items = overlay.get("unproven", [])
    if not isinstance(items, list):
        raise ValueError("unproven must be an array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"unproven[{index}] must be an object")
        if not isinstance(item.get("config_id", "unknown"), str):
            raise ValueError(f"unproven[{index}].config_id must be a string")
    return items


def unproven_rows(overlay: dict, registry: dict) -> list[dict]:
    """Expose metadata and paths, never provider responses or request bodies."""
    items = validate_unproven(overlay)

    import probe_sweep

    configs = {row["id"]: row for row in registry["scored_configs"]}
    manifests = [Path(e["path"]) for e in overlay.get("evidence", [])
                 if "vendor" not in e and Path(e["path"]).name == "probe-manifest.json"]
    failed = {}
    for path in manifests:
        runs = read_object(path).get("failed_probe_runs", {})
        if not isinstance(runs, dict):
            raise ValueError("failed_probe_runs must be an object")
        for cid, files in runs.items():
            if not isinstance(files, dict):
                raise ValueError(f"failed_probe_runs[{cid}] must be an object")
            for kind, entry in files.items():
                if not isinstance(entry, dict):
                    raise ValueError(f"failed_probe_runs[{cid}][{kind}] must be an object")
                if "path" in entry and not isinstance(entry["path"], str):
                    raise ValueError(f"failed_probe_runs[{cid}][{kind}].path must be a string")
        failed.update(runs)
    rows = []
    for item in items:
        cid = item.get("config_id", "unknown")
        config = configs.get(cid, {})
        vendor = config.get("vendor") or cid.split("/", 1)[0]
        files = failed.get(cid, {})
        descriptor_path = files.get("json", {}).get("path")
        # Old manifests did not list failed files; use the canonical probe name.
        if not descriptor_path and len(manifests) == 1 and vendor in probe_sweep.VENDORS:
            entry = next((e for e in probe_sweep.plan(vendor) if e["config_id"] == cid), None)
            if entry:
                descriptor_path = str(manifests[0].parent / "evidence" / f"{entry['name']}.json")
        invalidated = cid in registry.get("_transport_invalidated_evidence", {})
        descriptor = read_object(Path(descriptor_path)) if descriptor_path and not invalidated else {}
        reason = item.get("verdict_reason") or "unknown"
        if invalidated and reason != INVALIDATED_REASON:
            reason = FAILED_INVALIDATED_REASON
        failure_class = "environment" if environment_reason(reason) else "conclusive"
        if item.get("pending_recheck"):
            reason = public_reason(reason)
        if reason in ("not probed on this host", "vendor CLI is not installed", INVALIDATED_REASON,
                      FAILED_INVALIDATED_REASON):
            failure_class = "unprobed"
        if failure_class == "environment":
            next_step = f"omnilane resign --retry-failed --vendor {shlex.quote(vendor)}"
        elif failure_class == "unprobed":
            next_step = f"check CLI availability, then omnilane resign --vendor {shlex.quote(vendor)}"
            if reason == FAILED_INVALIDATED_REASON:
                next_step += " in an interactive terminal; unverified failure timestamps cannot authorize automatic retry"
        else:
            next_step = ("will not retry automatically; inspect the original request, CLI model/effort "
                         "selectors and provider model mapping before arranging fresh verification")
        paths = {entry["path"] for entry in files.values() if isinstance(entry, dict) and entry.get("path")}
        if descriptor_path:
            paths.add(descriptor_path)
        rows.append(dict(config_id=cid, vendor=vendor,
                         requested_model=config.get("model"), requested_effort=config.get("effort"),
                         observed_model=None if invalidated else item.get("observed_model") or descriptor.get("observed_model"),
                         observed_effort=None if invalidated else item.get("observed_effort") or descriptor.get("observed_effort"),
                         failure_class=failure_class, reason=reason,
                         last_probe_at=None if invalidated else item.get("probed_at") or descriptor.get("probed_at")
                         or item.get("last_failed_at"),
                         evidence_paths=sorted(paths), next_step=next_step))
    return rows


def row_message(row: dict) -> str:
    def shown(value):
        return "not recorded" if value is None else str(value)

    return (f"{row['config_id']}: requested model={shown(row['requested_model'])}, "
            f"effort={shown(row['requested_effort'])}; observed model={shown(row['observed_model'])}, "
            f"effort={shown(row['observed_effort'])}; {row['failure_class']}: {row['reason']}; "
            f"last_probe_at={shown(row['last_probe_at'])}; "
            f"evidence paths={', '.join(row['evidence_paths']) or 'not recorded'}; "
            f"next: {row['next_step']}")


def recovery_guidance(repo: Path, overlay_path: Path) -> str:
    """Describe supported operator recovery without modifying any trust material."""
    evidence = read_object(overlay_path).get("evidence")
    manifests = [e["path"] for e in (evidence if isinstance(evidence, list) else [])
                 if isinstance(e, dict) and isinstance(e.get("path"), str)
                 and Path(e["path"]).name == "probe-manifest.json"]
    sweep = shlex.quote(str(repo / "scripts/lib/probe_sweep.py"))
    build = shlex.quote(str(repo / "scripts/lib/build_overlay.py"))
    command = ('REPROBE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/omnilane-reprobe.XXXXXX") && '
               f'python3 {sweep} --root "$REPROBE_ROOT" && '
               f'python3 {build} --root "$REPROBE_ROOT"')
    return (f"Evidence for overlay {overlay_path} (manifest paths: {', '.join(manifests) or 'not recorded'}): "
            "restore the affected evidence file(s) from a "
            "known-good backup, then rerun `omnilane doctor`; or investigate the cause and "
            f"explicitly create fresh evidence in a desktop terminal: `{command}`. "
            "Review the new transport-contracts.local.json through the documented overlay "
            "installation and signer-approval steps; the current overlay is not replaced by these commands.")
