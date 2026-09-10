#!/usr/bin/env python3
"""Report whether the configured AA transport overlay still loads.

Prints one `LEVEL<TAB>message` line for `omnilane doctor`. Always exits 0; the
caller decides how to grade the level. Nothing here contacts a provider.

Exists because no other doctor check observes the AA gate: every vendor CLI can
be reachable and every lane resolvable while `load_registry` refuses the whole
registry over one drifted evidence hash.
"""
import hashlib
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))


def emit(level: str, message: str) -> None:
    print(f"{level}\t{message}")
    raise SystemExit(0)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def offenders(overlay_path: Path) -> list[str]:
    """Name the evidence entries that no longer match, for an actionable report."""
    import json

    try:
        overlay = json.loads(overlay_path.read_text())
    except (OSError, ValueError):
        return []
    found = []
    for entry in overlay.get("evidence", []):
        path = Path(entry.get("path", ""))
        tag = entry.get("vendor") or "untagged"
        if not path.exists():
            found.append(f"{tag}:missing {path}")
        elif digest(path) != entry.get("sha256"):
            found.append(f"{tag}:hash drift {path}")
    return found


def main() -> None:
    overlay_path = os.environ.get("OMNILANE_AA_TRANSPORT_OVERLAY", "")
    if not overlay_path:
        emit("PASS", "no overlay configured; every runtime mapping stays unverified")
    if not Path(overlay_path).exists():
        emit("FAIL", f"configured overlay is missing: {overlay_path}")

    try:
        import aa_policy
    except ImportError as error:
        emit("WARN", f"cannot import aa_policy: {error}")

    try:
        registry, _ = aa_policy.load_registry(str(REPO / "config" / "aa-model-policy.json"))
    except Exception as error:  # PolicyError, OSError, and anything else fails the gate
        detail = "; ".join(offenders(Path(overlay_path))) or str(error)
        emit("FAIL", f"overlay rejected, every dispatch is refused: {error} ({detail})")

    tiers = registry.get("_transport_evidence_tiers", {})
    verified = Counter()
    for row in registry["scored_configs"]:
        if row["transport_mapping"].get("runtime_verified") is True:
            verified[row["vendor"], tiers.get(row["id"], "selector-only")] += 1
    summary = ", ".join(f"{vendor} {count} {tier}"
                        for (vendor, tier), count in sorted(verified.items())) or "none"

    import json

    overlay = json.loads(Path(overlay_path).read_text())
    unproven = overlay.get("unproven", [])
    extra = f"; {len(unproven)} config(s) recorded unproven" if unproven else ""
    weak = sorted({vendor for (vendor, tier) in verified if tier == "selector-only"})
    if weak:
        extra += (f"; {', '.join(weak)} prove only the request selector, re-probe to "
                  "record who answered")

    stale = registry.get("_stale_transport_vendors", [])
    if stale:
        detail = "; ".join(o for o in offenders(Path(overlay_path))) or "unknown cause"
        emit("WARN", f"stale vendor(s) {', '.join(stale)} degraded to unverified "
                     f"({detail}); still verified: {summary}{extra}")
    emit("PASS", f"verified mappings: {summary}{extra}")


main()
