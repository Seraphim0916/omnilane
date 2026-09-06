#!/usr/bin/env python3
"""Reconstruct only a validated job-owned authorizer; never borrow retry shell identity."""
import json
import os
from pathlib import Path
import sys
import aa_policy


def retry_args(directory):
    root = Path(directory)
    os.environ.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
    os.environ.pop("OMNILANE_AA_OVERLAY_SHA256", None)
    lineage, _ = aa_policy._read_json(root / "aa-lineage.json")
    aa_policy._check(lineage.get("schema_version") == 1, "unsupported retry lineage")
    registry_path = root / "aa-registry.json"
    registry, _ = aa_policy.load_registry(registry_path, lineage["registry_sha256"])
    result = ["--aa-policy", str(registry_path)]
    original = None
    original_limit = 100
    if lineage.get("operator_asserted_human") is not True:
        context = root / "aa-authorizer.json"
        aa_policy._check(isinstance(lineage.get("authorizer_sha256"), str), "missing retry authorizer hash")
        original, _ = aa_policy.load_caller(context, registry, lineage["authorizer_sha256"])
        rows = aa_policy._matching_rows(registry, original["caller"])
        aa_policy._check(len(rows) == 1, "unknown original retry authorizer")
        original_limit = min(rows[0]["score"], original["inherited_ceiling"])
    current_path = os.environ.get("OMNILANE_AA_CALLER_CONTEXT")
    current_human = os.environ.get("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN") == "1"
    aa_policy._check(bool(current_path) != current_human, "retry requires one current caller context or explicit current human assertion")
    if current_path:
        current, _ = aa_policy.load_caller(current_path, registry)
        rows = aa_policy._matching_rows(registry, current["caller"])
        aa_policy._check(len(rows) == 1, "unknown current retry caller")
        narrowed = dict(current)
        narrowed["inherited_ceiling"] = min(original_limit, current["inherited_ceiling"], rows[0]["score"])
        digest = aa_policy.hashlib.sha256(aa_policy._json_line(narrowed).encode()).hexdigest()
        context = root / ("aa-retry-authorizer-" + digest + ".json")
        aa_policy.atomic_json(context, narrowed)
        result += ["--caller-context", str(context)]
    elif original is not None:
        result += ["--caller-context", str(root / "aa-authorizer.json")]
    else:
        result += ["--operator-asserted-human"]
    if lineage.get("transport_overlay_sha256"):
        overlay = root / "aa-transport-overlay.json"
        _, digest = aa_policy._read_json(overlay)
        aa_policy._check(digest == lineage["transport_overlay_sha256"], "retry transport overlay changed")
        result += ["--transport-overlay", str(overlay)]
    config = lineage.get("target_config_id")
    if config:
        aa_policy._check(isinstance(config, str) and aa_policy.IDENTIFIER.fullmatch(config), "invalid retry target config")
        result += ["--target-config", config]
    return result


def metadata_fields(directory):
    value, _ = aa_policy._read_json(Path(directory) / "meta.json")
    keys = ("lane", "vendor", "model", "effort", "timeout", "job_timeout", "mode", "workdir")
    aa_policy._check(value.get("mode") in ("advise", "work"), "invalid retry mode")
    aa_policy._check(type(value.get("timeout")) is int and value["timeout"] > 0, "invalid retry timeout")
    aa_policy._check(value.get("job_timeout") is None or (type(value["job_timeout"]) is int and value["job_timeout"] > 0), "invalid retry job timeout")
    result = []
    for key in keys:
        field = value[key]
        text = "null" if field is None else str(field)
        aa_policy._check("\n" not in text and "\r" not in text and "\x00" not in text, "invalid retry metadata value")
        result.append(text)
    return result


if __name__ == "__main__":
    try:
        print("\n".join(metadata_fields(sys.argv[2]) if sys.argv[1] == "--metadata" else retry_args(sys.argv[1])))
    except (aa_policy.PolicyError, OSError, KeyError, TypeError):
        print("omnilane: retry requires intact AA lineage; current caller is missing or original authorizer/registry is changed", file=sys.stderr)
        sys.exit(3)
