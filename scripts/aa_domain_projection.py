#!/usr/bin/env python3
"""Print a read-only schema-1 projection of AA domain evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__:
    from .lib import aa_domain_metadata as metadata
else:
    from lib import aa_domain_metadata as metadata


REPO = Path(__file__).resolve().parents[1]


def companion_path(path: Path, field: str) -> Path:
    """Resolve all symlinks and accept only regular files inside this checkout."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{field}: cannot resolve companion file: {exc}") from None
    if not resolved.is_relative_to(REPO) or not resolved.is_file():
        raise ValueError(f"{field}: companion must be a regular file inside the checkout")
    return resolved


def load_companion(path: Path, registry: dict, expected_sha256: str | None = None) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"companion: cannot read file: {exc}") from None
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("registry.snapshot.domain_strength.sha256: does not match companion bytes")
    companion = metadata.decode_json(raw, "companion")
    metadata.validate_companion(companion, registry)
    return companion, digest


def project(registry: dict, explicit_companion: Path | None = None) -> dict:
    metadata.validate_registry(registry)
    snapshot = registry["snapshot"]
    filename = expected_sha256 = None
    if "domain_strength" in snapshot:
        pointer = snapshot["domain_strength"]
        relative = metadata.validate_pointer(pointer)
        filename = companion_path(REPO / relative, "registry.snapshot.domain_strength.path")
        expected_sha256 = pointer["sha256"]
    if explicit_companion is not None:
        explicit = companion_path(explicit_companion, "companion.path")
        if filename is not None and explicit != filename:
            raise ValueError("companion.path: does not match registry.snapshot.domain_strength.path")
        filename = explicit

    companion_sha256 = None
    by_kind = {kind: [] for kind in metadata.EVIDENCE_KINDS}
    claims = []
    if filename is not None:
        companion, companion_sha256 = load_companion(filename, registry, expected_sha256)
        for row in companion["measurements"]:
            by_kind[row["evidence_kind"]].append(row)
        claims = companion["vendor_claims"]

    return {
        "schema_version": metadata.SCHEMA_VERSION,
        "snapshot_id": snapshot["id"],
        "registry_sha256": metadata.registry_payload_sha256(registry),
        "companion_sha256": companion_sha256,
        "dispatch_eligible": False,
        "policy": registry["policy"],
        "scored_configs": registry["scored_configs"],
        "measurements": by_kind,
        "vendor_claims": claims,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path, help="existing schema-1 AA registry")
    parser.add_argument("--companion", type=Path, help="optional companion file inside the checkout")
    args = parser.parse_args(argv)
    try:
        registry = metadata.decode_json(args.registry.read_bytes(), "registry")
        projection = project(registry, args.companion)
        serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False)
    except OSError as exc:
        parser.exit(2, f"aa_domain_projection: registry: cannot read file: {exc}\n")
    except ValueError as exc:
        parser.exit(2, f"aa_domain_projection: {exc}\n")
    sys.stdout.buffer.write((serialized + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
