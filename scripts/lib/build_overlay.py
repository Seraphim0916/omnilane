#!/usr/bin/env python3
"""Build the merged omnilane AA transport overlay from verified probe evidence.

Only (config_id, selector) pairs listed in PROVEN are written. Every entry here
corresponds to a probe run under the selected evidence root whose raw
stdout/stderr is hashed into the manifest, so the overlay's evidence[] anchors
the whole set.
"""
import argparse
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("OMNILANE_REPO", "/Users/vincentw/dev/omnilane"))
HOME = Path.home()
SWEEP_ID = os.environ.get("OMNILANE_TRANSPORT_SWEEP_ID", "overlay-reprobe-20260909")
DEFAULT_ROOT = HOME / ".omnilane" / "transport-evidence" / SWEEP_ID
REGISTRY = json.loads((REPO / "config/aa-model-policy.json").read_text())
ROWS = {r["id"]: r for r in REGISTRY["scored_configs"]}
IDENTITY_FIELDS = ("vendor", "model", "effort", "reasoning", "fallback")

# config_id -> (selector_type, runtime_model, probe evidence basename)
PROVEN: dict[str, tuple[str, str, str]] = {}

for model, slug in [("gpt-6-astra", "gpt-6-astra"), ("gpt-5.6-sol", "gpt-5_6-sol"),
                    ("gpt-5.6-luna", "gpt-5_6-luna"), ("gpt-5.6-terra", "gpt-5_6-terra")]:
    base = model.replace(".", "-").replace("gpt-", "gpt-")
    for effort in ["max", "xhigh", "high", "medium", "low"]:
        cid = f"codex/{model.replace('.', '-')}" + ("" if effort == "max" else f"-{effort}")
        ev = f"cx-avail-{model.replace('.', '_')}" if effort == "high" else f"cx-{model.replace('.', '_')}-{effort}"
        PROVEN[cid] = ("model_and_effort", model, ev)

for effort in ["xhigh", "medium"]:
    PROVEN[f"codex/gpt-5-4-mini" + ("" if effort == "xhigh" else f"-{effort}")] = (
        "model_and_effort", "gpt-5.4-mini", f"cx-gpt-5_4-mini-{effort}")

PROVEN["grok/grok-4-6"] = ("cli_reasoning_effort", "grok-4.6", "PRIOR:grok-effort-2026-09-07")
for effort in ["xhigh", "medium", "low"]:
    PROVEN[f"grok/grok-4-6-{effort}"] = ("cli_reasoning_effort", "grok-4.6", f"gk-grok-4_6-{effort}")
PROVEN["grok/grok-4-5"] = ("cli_reasoning_effort", "grok-4.5", "gk-grok-4_5-high")

for cid, rid, ev in [
    ("gemini/gemini-3-8-flash", "gemini-3.8-flash-high", "PRIOR:gemini-flash-high"),
    ("gemini/gemini-3-8-flash-medium", "gemini-3.8-flash-medium", "agy-gemini-3_8-flash-medium"),
    ("gemini/gemini-3-8-flash-low", "gemini-3.8-flash-low", "agy-gemini-3_8-flash-low"),
    ("gemini/gemini-3-7-flash", "gemini-3.7-flash-high", "agy-gemini-3_7-flash-high"),
    ("gemini/gemini-3-7-flash-medium", "gemini-3.7-flash-medium", "agy-gemini-3_7-flash-medium"),
    ("gemini/gemini-3-7-flash-low", "gemini-3.7-flash-low", "agy-gemini-3_7-flash-low"),
    ("gemini/gemini-3-6-flash", "gemini-3.6-flash-high", "agy-gemini-3_6-flash-high"),
]:
    PROVEN[cid] = ("model_id_encoded_effort", rid, ev)

for effort in ["max", "xhigh", "high", "medium", "low"]:
    cid = "claude/claude-opus-5" + ("" if effort == "max" else f"-{effort}")
    PROVEN[cid] = ("model_and_effort", "claude-opus-5", f"cl-claude-opus-5-{effort}")
# gpt-6-astra rejects effort "none" upstream ("Unsupported value: 'none' is not
# supported with the 'gpt-6-astra' model"), so it has no non-reasoning selector.
for model in ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini"]:
    PROVEN[f"codex/{model.replace('.', '-')}-non-reasoning"] = (
        "model_and_effort", model, f"cx-{model.replace('.', '_')}-none")

# Default (no --effort) Haiku 4.5 spent 124 thinking tokens, so the selector
# lands on the reasoning row rather than its non-reasoning sibling.
PROVEN["claude/claude-4-5-haiku-reasoning"] = (
    "model_and_effort", "claude-haiku-4-5", "cl-rmode-claude-haiku-4-5-noeffort")

for cid, model in [("claude/claude-sonnet-5", "claude-sonnet-5"),
                   ("claude/claude-opus-4-8", "claude-opus-4-8"),
                   ("claude/claude-opus-4-7", "claude-opus-4-7"),
                   ("claude/claude-opus-4-6-adaptive", "claude-opus-4-6"),
                   ("claude/claude-sonnet-4-6-adaptive", "claude-sonnet-4-6")]:
    PROVEN[cid] = ("model_and_effort", model, f"cl-{model}-max")

CORE_EVIDENCE = [
    (HOME / ".grok/downloads/grok-1.0.13-macos-aarch64", "grok"),
    (REPO / "scripts/runners/run-grok.sh", "grok"),
    (HOME / ".codex/packages/standalone/releases/0.153.4-aarch64-apple-darwin/bin/codex", "codex"),
    (REPO / "scripts/runners/run-codex.sh", "codex"),
    (HOME / ".local/share/claude/versions/2.1.263", "claude"),
    (REPO / "scripts/runners/run-claude.sh", "claude"),
    (HOME / ".local/bin/agy", "gemini"),
    (REPO / "scripts/runners/run-gemini.sh", "gemini"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"probe sweep root (default: {DEFAULT_ROOT})",
    )
    args = parser.parse_args(argv)
    root = args.root.expanduser()

    manifest = {"probe_runs": {}}
    unproven = []
    for cid, (_, _, ev) in sorted(PROVEN.items()):
        if ev.startswith("PRIOR:"):
            manifest["probe_runs"][cid] = {"source": ev, "note": "verified in the 2026-09-07 Codex run"}
            continue
        entry = {}
        for suffix in ("json", "stdout", "stderr"):
            path = root / "evidence" / f"{ev}.{suffix}"
            if path.exists():
                entry[suffix] = {"path": str(path), "sha256": sha256(path)}
        if "json" not in entry:
            raise SystemExit(f"missing probe evidence for {cid}: {ev}")
        descriptor_path = Path(entry["json"]["path"])
        descriptor = json.loads(descriptor_path.read_text())
        if not isinstance(descriptor, dict):
            raise SystemExit(f"invalid probe descriptor for {cid}: {ev}")
        if "verdict" not in descriptor:
            print(f"warning: legacy evidence (verdict=unknown): {cid}: {ev}")
        elif descriptor["verdict"] != "pass":
            unproven.append({
                "config_id": cid,
                "verdict_reason": descriptor.get("verdict_reason") or f"verdict: {descriptor['verdict']}",
                "observed_model": descriptor.get("observed_model"),
                "probed_at": descriptor.get("probed_at") or datetime.fromtimestamp(
                    descriptor_path.stat().st_mtime, timezone.utc).isoformat(),
            })
            # Visibility only: failed evidence must not enter the signed manifest.
            continue
        manifest["probe_runs"][cid] = entry
    manifest_path = root / "probe-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    mappings = []
    for cid, (selector, runtime_model, _) in sorted(PROVEN.items()):
        if cid not in manifest["probe_runs"]:
            continue
        row = ROWS[cid]
        mapping = {
            "config_id": cid,
            "identity": {key: row[key] for key in IDENTITY_FIELDS},
            "runtime_model": runtime_model,
            "runtime_effort": row["effort"],
            "selector_type": selector,
            "verification": "request-selector-contract",
        }
        if selector == "cli_reasoning_effort":
            mapping["cli_flag"] = "--reasoning-effort"
        mappings.append(mapping)

    evidence = [
        {"path": str(path), "sha256": sha256(path), "vendor": vendor}
        for path, vendor in CORE_EVIDENCE
    ]
    evidence.append({"path": str(manifest_path), "sha256": sha256(manifest_path)})

    overlay = {
        "schema_version": 1,
        "snapshot_id": REGISTRY["snapshot"]["id"],
        "host": socket.gethostname(),
        "source": ("claude-code / MacStudio / operator-directed full sweep 2026-09-07; "
                   "gemini selectors re-probed 2026-09-09 after agy 1.1.27 -> 1.1.28"),
        "evidence": evidence,
        "mappings": mappings,
        "unproven": unproven,
    }
    out = root / "transport-contracts.local.json"
    out.write_text(json.dumps(overlay, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out} with {len(mappings)} mappings and {len(evidence)} evidence anchors")


if __name__ == "__main__":
    main()
