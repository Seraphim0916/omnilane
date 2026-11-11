"""Row-local evidence loss; release-pinned transport semantics stay in transport_compat."""

from datetime import datetime
from pathlib import Path

import transport_compat as compat

INVALIDATED_REASON = "probe evidence invalidated"
FAILED_INVALIDATED_REASON = "failed probe evidence invalidated"


def damaged_files(files):
    """Validate references strictly and return only missing or hash-mismatched files."""
    import aa_policy
    aa_policy._check(isinstance(files, dict) and "json" in files, "incomplete transport proof")
    damaged = []
    for entry in files.values():
        aa_policy._check(isinstance(entry, dict)
                         and isinstance(entry.get("path"), str)
                         and isinstance(entry.get("sha256"), str)
                         and len(entry["sha256"]) == 64
                         and all(c in "0123456789abcdef" for c in entry["sha256"]),
                         "invalid transport proof entry")
        try:
            matches = aa_policy.evidence_digest(entry["path"]) == entry["sha256"]
        except FileNotFoundError:
            matches = False
        if not matches:
            damaged.append(entry["path"])
    return sorted(set(damaged))


def reject_replaced_directories(runs, invalidated):
    """A complete directory of unanchored, recognizable descriptors is foreign.

    Inspect mismatched JSON only for rejection, never as verification authority.
    Missing/truncated descriptors or partial replacement remain row-local damage.
    """
    import aa_policy
    directories = {}
    for cid, files in runs.items():
        path = files["json"]["path"]
        directories.setdefault(str(Path(path).parent), []).append((cid, path))
    for entries in directories.values():
        if not all(path in invalidated.get(cid, ()) for cid, path in entries):
            continue
        for _, path in entries:
            try:
                descriptor, _ = aa_policy._read_json(path)
                if not (isinstance(descriptor.get("command"), list) and descriptor["command"]
                        and all(isinstance(arg, str) for arg in descriptor["command"])
                        and type(descriptor.get("exit_code")) is int
                        and type(descriptor.get("timed_out")) is bool
                        and isinstance(descriptor.get("probed_at"), str)):
                    break
                stamp = datetime.fromisoformat(descriptor["probed_at"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    break
            except (FileNotFoundError, ValueError, AttributeError):
                break
        else:
            raise aa_policy.EvidenceDirectoryError("transport evidence directory does not belong to this overlay")


def proof_records(overlay, rows=None, source=None, *, invalidated=None):
    """Check shared anchors strictly; optionally collect row-local evidence loss.

    Only missing bytes or a digest mismatch in a manifest's raw files can be
    isolated. The manifest itself, structure, source binding and verdicts remain
    fail-closed. Damaged bytes are never parsed or used to authorize a mapping.
    """
    import aa_policy
    if rows is None:
        import build_overlay
        rows, source, _ = compat.source_rows(overlay, build_overlay.REGISTRY)
    records = {}
    for evidence in overlay["evidence"]:
        if evidence.get("vendor") is not None:
            continue
        manifest, actual = aa_policy._read_json(evidence["path"])
        aa_policy._check(actual == evidence["sha256"], "transport contract evidence changed")
        runs = manifest.get("probe_runs")
        aa_policy._check(isinstance(runs, dict), "transport evidence has no probe manifest")
        for cid, files in runs.items():
            aa_policy._check(cid not in records and isinstance(files, dict) and "json" in files,
                             "duplicate or incomplete transport proof")
            damaged = damaged_files(files)
            if damaged:
                aa_policy._check(invalidated is not None, "transport probe evidence changed")
                invalidated[cid] = sorted(set(damaged))
                records[cid] = (files, {})
                continue
            descriptor, _ = aa_policy._read_json(files["json"]["path"])
            row = rows.get(cid)
            selector = source["selectors"].get(cid) if source else compat.selector_contract(row) if row else None
            descriptor = compat.checked_descriptor(row, selector, files, descriptor)
            aa_policy._check(descriptor.get("verdict") == "pass",
                             "transport proof did not pass")
            records[cid] = (files, descriptor)
    return records
