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
        if os.environ.get("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN") == "1":
            emit("PASS", "no overlay configured; fine for a human operator, but a model caller "
                         "would be refused on every lane (README 'First install')")
        emit("WARN", "no overlay configured, so a model caller is refused on every lane (runtime-mapping-unverified). First install: probe_sweep.py --root ROOT, build_overlay.py --root ROOT, then export OMNILANE_AA_TRANSPORT_OVERLAY in local.sh; see README 'First install'")
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
    # The gate hashes the path the overlay recorded. An update that installs beside
    # the old executable leaves that path intact, so only this comparison sees it.
    moved = []
    try:
        import resign
        report = resign.detect(overlay, resign.current_anchors())
        for vendor, entry in report.items():
            # Only what the overlay actually pinned can have moved.
            reasons = [reason for reason in entry["reasons"]
                       if reason.startswith("runs ") or reason.endswith(" changed")]
            if reasons and entry["recorded_cli_path"] and vendor not in stale:
                moved.append(f"{vendor}: {'; '.join(reasons)}")
    except Exception:  # noqa: BLE001 - a health line must never raise
        moved = []
    if moved and not stale:
        emit("WARN", f"the runners no longer execute what the overlay pinned ({' | '.join(moved)}); "
                     f"run `omnilane resign`; still loading: {summary}{extra}")
    if stale:
        detail = "; ".join(o for o in offenders(Path(overlay_path))) or "unknown cause"
        if moved:
            detail += "; also moved without going stale: " + " | ".join(moved)
        emit("WARN", f"stale vendor(s) {', '.join(stale)} degraded to unverified "
                     f"({detail}); run `omnilane resign`; still verified: {summary}{extra}")
    emit("PASS", f"verified mappings: {summary}{extra}")


main()
