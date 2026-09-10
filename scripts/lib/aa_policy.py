#!/usr/bin/env python3
"""Frozen exact-AA downward-delegation decision engine.

This module is intentionally provider-free.  It validates one explicit caller
assertion against the frozen registry, resolves only runtime-verified target
mappings, and emits a structured allow/deny decision.  The JSON context is
cooperative workflow metadata, not operating-system authentication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import socket
import sys
from typing import Any


MAX_BYTES = 1_048_576
# Approval anchor for the frozen AA v4.2 2026-09-07 source bytes. Updating this
# constant is a governance change, never a caller argument/environment override.
APPROVED_REGISTRY_SHA256 = "0782c87de123c02738c3ff60e4bc3c1cc10d110113e872b8f8627212861cdaab"

IDENTITY_FIELDS = ("vendor", "model", "effort", "reasoning", "fallback")
TRANSPORT_EVIDENCE_VENDORS = frozenset(("codex", "claude", "grok", "gemini"))
# How strongly a mapping's probe identified the responder. Reported, never
# enforced: dispatch turns on runtime_verified alone, as it did before the field
# existed, so a weaker tier can never refuse a lane that used to run.
TRANSPORT_EVIDENCE_TIERS = frozenset(("billed-model", "client-echo", "selector-only"))
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


class PolicyError(ValueError):
    """A public, non-sensitive policy validation error."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _check(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _read_bytes(path: str | Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        _check(stat.S_ISREG(info.st_mode), "policy input must be a regular file")
        _check(info.st_size <= MAX_BYTES, "policy input is too large")
        data = stream.read(MAX_BYTES + 1)
    _check(len(data) <= MAX_BYTES, "policy input is too large")
    return data


def _read_json(path: str | Path) -> tuple[dict[str, Any], str]:
    data = _read_bytes(path)
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(PolicyError("invalid JSON number")),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError("invalid JSON input") from error
    _check(isinstance(value, dict), "policy input must be a JSON object")
    return value, hashlib.sha256(data).hexdigest()


def _exact_fields(value: dict[str, Any], required: set[str]) -> None:
    _check(set(value) == required, "missing or unknown caller-context fields")


def _identity(value: Any, label: str) -> dict[str, Any]:
    _check(isinstance(value, dict), f"{label} must be a JSON object")
    _exact_fields(value, set(IDENTITY_FIELDS))
    for key in ("vendor", "model", "reasoning"):
        field = value[key]
        _check(isinstance(field, str) and bool(IDENTIFIER.fullmatch(field)),
               f"invalid {label}.{key}")
    for key in ("effort", "fallback"):
        field = value[key]
        _check(field is None or (isinstance(field, str) and bool(IDENTIFIER.fullmatch(field))),
               f"invalid {label}.{key}")
    return {key: value[key] for key in IDENTITY_FIELDS}


def _validate_registry(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "snapshot", "policy", "scored_configs",
        "unknown_configs", "reference_configs", "aliases", "coverage",
    }
    _check(required <= set(value) <= required | {"schema_notes"},
           "unsupported AA registry schema")
    _check(type(value["schema_version"]) is int and value["schema_version"] == 1,
           "unsupported AA registry version")
    snapshot = value["snapshot"]
    _check(isinstance(snapshot, dict), "invalid AA registry snapshot")
    for key in ("id", "benchmark_version", "as_of", "frozen"):
        _check(key in snapshot, "incomplete AA registry snapshot")
    _check(snapshot["benchmark_version"] == "4.2"
           and snapshot["as_of"] == "2026-09-07"
           and snapshot["frozen"] is True,
           "AA registry is not frozen v4.2 dated 2026-09-07")
    policy = value["policy"]
    _check(isinstance(policy, dict)
           and policy.get("decision") == "target_score <= min(caller_score, inherited_ceiling)"
           and policy.get("same_score_allowed") is True
           and policy.get("unknown") == "deny"
           and policy.get("transport_mapping_requires_runtime_verification") is True,
           "AA registry policy contract mismatch")
    rows = value["scored_configs"]
    _check(isinstance(rows, list) and rows, "AA registry has no scored configurations")
    seen: set[str] = set()
    for row in rows:
        _check(isinstance(row, dict), "invalid AA scored configuration")
        for key in (*IDENTITY_FIELDS, "id", "score", "estimated", "benchmark_version",
                    "as_of", "transport_mapping"):
            _check(key in row, "incomplete AA scored configuration")
        _check(isinstance(row["id"], str) and row["id"] not in seen,
               "duplicate AA scored configuration")
        seen.add(row["id"])
        _identity({key: row[key] for key in IDENTITY_FIELDS}, "registry identity")
        _check(type(row["score"]) is int and 0 <= row["score"] <= 100,
               "invalid AA score")
        _check(type(row["estimated"]) is bool, "invalid AA estimated flag")
        _check(row["benchmark_version"] == "4.2" and row["as_of"] == "2026-09-07",
               "mixed AA registry snapshot")
        _check(isinstance(row["transport_mapping"], dict), "invalid transport mapping")
    return value


def apply_transport_overlay(registry: dict[str, Any]) -> None:
    """Host-local request selector proof, independent of frozen AA scores."""
    path = os.environ.get("OMNILANE_AA_TRANSPORT_OVERLAY")
    if not path:
        return
    overlay, digest = _read_json(path)
    expected = os.environ.get("OMNILANE_AA_OVERLAY_SHA256")
    _check(not expected or expected == digest, "transport overlay changed after initial decision")
    _check(overlay.get("schema_version") == 1, "unsupported transport overlay")
    _check(overlay.get("snapshot_id") == registry["snapshot"]["id"], "transport overlay snapshot mismatch")
    _check(overlay.get("host") == socket.gethostname(), "transport overlay host mismatch")
    stale_vendors: set[str] = set()
    for evidence in overlay.get("evidence", []):
        _check(isinstance(evidence, dict), "invalid transport evidence")
        vendor = evidence.get("vendor")
        _check(
            "vendor" not in evidence
            or type(vendor) is str and vendor in TRANSPORT_EVIDENCE_VENDORS,
            "invalid transport evidence vendor",
        )
        evidence_path = evidence["path"]
        evidence_sha256 = evidence["sha256"]
        _check(type(evidence_path) is str, "invalid transport evidence path")
        _check(type(evidence_sha256) is str, "invalid transport evidence digest")
        try:
            fd = os.open(evidence_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if vendor is None:
                raise
            stale_vendors.add(vendor)
            continue
        with os.fdopen(fd, "rb") as stream:
            _check(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "invalid transport evidence file")
            digest_file = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest_file.update(block)
        if digest_file.hexdigest() != evidence_sha256:
            if vendor is None:
                _check(False, "transport contract evidence changed")
            stale_vendors.add(vendor)
    _check(bool(overlay.get("evidence")), "transport overlay requires local evidence")
    tiers: dict[str, str] = {}
    for mapping in overlay.get("mappings", []):
        rows = [row for row in registry["scored_configs"] if row["id"] == mapping.get("config_id")]
        _check(len(rows) == 1, "unknown overlay config")
        row = rows[0]
        _check(mapping.get("identity") == _row_identity(row), "overlay exact identity mismatch")
        _check(mapping.get("verification") == "request-selector-contract", "unsupported overlay verification")
        _check(mapping.get("runtime_effort") == row["effort"], "overlay effort mismatch")
        selector_type = mapping.get("selector_type", "model_and_effort")
        _check(selector_type in ("model_and_effort", "model_id_encoded_effort", "cli_reasoning_effort"), "unknown selector type")
        if selector_type == "cli_reasoning_effort":
            _check(row["vendor"] == "grok", "unsupported CLI-effort vendor")
            _check(mapping.get("cli_flag") == "--reasoning-effort", "unproven CLI-effort flag")
            _check(mapping.get("runtime_effort") in ("low", "medium", "high", "xhigh"), "unsupported CLI effort")
        if selector_type == "model_id_encoded_effort":
            _check(row["vendor"] == "gemini", "unsupported encoded-effort vendor")
            _check(mapping.get("runtime_model") in row["transport_mapping"].get("candidate_model_ids", []), "unproven encoded model selector")
            _check(mapping["runtime_model"].endswith("-" + row["effort"]), "encoded effort does not match exact tuple")
        else:
            _check(mapping.get("runtime_model") == row["model"], "overlay model mismatch")
        tier = mapping.get("evidence_tier", "selector-only")
        _check(tier in TRANSPORT_EVIDENCE_TIERS, "unknown transport evidence tier")
        if row["vendor"] in stale_vendors:
            continue
        tiers[row["id"]] = tier
        row["transport_mapping"].update(
            status="verified", runtime_verified=True,
            runtime_model=mapping["runtime_model"], runtime_effort=mapping["runtime_effort"],
            selector_type=selector_type,
            cli_flag=mapping.get("cli_flag") if selector_type == "cli_reasoning_effort" else None,
            verification="request-selector-contract", upstream_identity_verified=False,
            overlay_sha256=digest, overlay_host=overlay["host"],
        )
    registry["_stale_transport_vendors"] = sorted(stale_vendors)
    registry["_transport_evidence_tiers"] = tiers


def load_registry(path: str | Path, expected_sha256: str | None = None) -> tuple[dict[str, Any], str]:
    value, digest = _read_json(path)
    _check(digest == APPROVED_REGISTRY_SHA256, "unapproved AA registry; use the approved frozen snapshot byte-for-byte")
    if expected_sha256:
        _check(digest == expected_sha256, "AA registry changed after initial decision")
    registry = _validate_registry(value)
    apply_transport_overlay(registry)
    return registry, digest


def load_caller(path: str | Path, registry: dict[str, Any],
                expected_sha256: str | None = None) -> tuple[dict[str, Any], str]:
    value, digest = _read_json(path)
    if expected_sha256:
        _check(digest == expected_sha256, "caller context changed after initial decision")
    _check(type(value.get("schema_version")) is int and value["schema_version"] == 1,
           "unsupported caller-context version")
    _check(value.get("snapshot_id") == registry["snapshot"]["id"],
           "caller-context snapshot does not match frozen registry")
    _check(value.get("kind") == "model", "unsupported caller-context kind")
    _exact_fields(value, {"schema_version", "snapshot_id", "kind", "caller", "inherited_ceiling"})
    value["caller"] = _identity(value["caller"], "caller")
    ceiling = value["inherited_ceiling"]
    _check(type(ceiling) is int and 0 <= ceiling <= 100,
           "invalid inherited caller ceiling")
    return value, digest


def _row_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in IDENTITY_FIELDS}


def _matching_rows(registry: dict[str, Any], identity: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in registry["scored_configs"] if _row_identity(row) == identity]


def _unknown_reason(registry: dict[str, Any], identity: dict[str, Any]) -> str | None:
    for row in registry["unknown_configs"]:
        if all(row.get(key) == identity[key] for key in IDENTITY_FIELDS):
            return row.get("reason")
    return None


def _runtime_target(registry: dict[str, Any], vendor: str, model: str,
                    effort: str | None, target_config: str | None) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    exact_id = [row for row in registry["scored_configs"] if row["id"] == target_config] if target_config else registry["scored_configs"]
    if target_config and not exact_id:
        return None, "unknown-target-config", {"target_config": target_config}
    if vendor in registry.get("_stale_transport_vendors", []):
        return None, "unknown-target-runtime", {"vendor": vendor, "model": model, "effort": effort}
    vendor_rows = [row for row in exact_id if row["vendor"] == vendor]
    candidates: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for row in vendor_rows:
        mapping = row["transport_mapping"]
        model_ids = mapping.get("candidate_model_ids", [])
        runtime_model = mapping.get("runtime_model")
        runtime_effort = mapping.get("runtime_effort", row["effort"])
        model_match = model == runtime_model if runtime_model is not None else model in model_ids
        encoded_effort = mapping.get("selector_type") == "model_id_encoded_effort"
        if not model_match:
            continue
        if encoded_effort and effort not in (None, runtime_effort):
            return None, "encoded-effort-conflict", {"model": model, "effort": effort, "encoded_effort": runtime_effort}
        if not encoded_effort and effort != runtime_effort:
            continue
        if mapping.get("runtime_verified") is True and mapping.get("status") in ("verified", "resolved"):
            candidates.append(row)
        else:
            unresolved.append(row["id"])
    if vendor == "grok" and candidates and any(
        row["transport_mapping"].get("selector_type") != "cli_reasoning_effort"
        or row["transport_mapping"].get("cli_flag") != "--reasoning-effort"
        or effort not in ("low", "medium", "high", "xhigh")
        for row in candidates
    ):
        # Only the verified single-shot CLI selector proves scored effort.
        # Legacy/encoded selectors and live ACP remain unsupported.
        return None, "runtime-effort-discarded", {"vendor": vendor, "model": model, "effort": effort}
    if len(candidates) == 1:
        return candidates[0], "runtime-mapping-verified", {}
    if len(candidates) > 1:
        return None, "ambiguous-runtime-mapping", {"candidate_config_ids": [row["id"] for row in candidates]}
    if unresolved:
        return None, "runtime-mapping-unverified", {"candidate_config_ids": unresolved}
    return None, "unknown-target-runtime", {"vendor": vendor, "model": model, "effort": effort}


def decide(registry: dict[str, Any], registry_sha256: str, *,
           vendor: str, model: str, effort: str | None,
           caller: dict[str, Any] | None, caller_sha256: str | None,
           operator_asserted_human: bool = False,
           target_config: str | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_id": registry["snapshot"]["id"],
        "registry_sha256": registry_sha256,
        "allowed": False,
        "code": "deny",
        "message": "AA policy denied dispatch",
        "caller_kind": "operator-asserted-human" if operator_asserted_human else "model",
        "target_request": {"vendor": vendor, "model": model, "effort": effort},
        "target_config_id": target_config,
        "caller_score": None,
        "inherited_ceiling": None,
        "effective_ceiling": None,
        "target_score": None,
        "target_estimated": None,
        "child_context": None,
        "evidence_limit": "workflow metadata and selected model arguments do not prove actual provider identity",
    }
    if operator_asserted_human:
        base.update(
            allowed=True,
            code="operator-asserted-human-exemption",
            message="explicit operator assertion bypassed model-level AA ceiling",
            evidence_limit="operator assertion is cooperative metadata, not authentication or provider-identity proof",
        )
        return base
    if caller is None:
        base.update(
            code="missing-caller-context",
            message="provide --caller-context FILE or explicitly assert --operator-asserted-human",
        )
        return base
    base["caller_context_sha256"] = caller_sha256
    caller_identity = caller["caller"]
    caller_rows = _matching_rows(registry, caller_identity)
    if len(caller_rows) != 1:
        detail = _unknown_reason(registry, caller_identity)
        base.update(
            code="unknown-caller-config" if not caller_rows else "ambiguous-caller-config",
            message=detail or "caller exact vendor/model/effort/reasoning/fallback is not uniquely scored",
            caller=caller_identity,
        )
        return base
    caller_row = caller_rows[0]
    effective = min(caller_row["score"], caller["inherited_ceiling"])
    base.update(
        caller=caller_identity,
        caller_score=caller_row["score"],
        inherited_ceiling=caller["inherited_ceiling"],
        effective_ceiling=effective,
    )
    target_row, mapping_code, detail = _runtime_target(
        registry, vendor, model, effort, target_config
    )
    if target_row is None:
        base.update(code=mapping_code, message="target runtime cannot be mapped to one verified exact AA configuration", **detail)
        return base
    target_score = target_row["score"]
    target_identity = _row_identity(target_row)
    base.update(
        target_config_id=target_row["id"],
        target=target_identity,
        target_score=target_score,
        target_estimated=target_row["estimated"],
    )
    if target_score > effective:
        base.update(
            code="target-above-effective-ceiling",
            message=f"target score {target_score} exceeds effective caller ceiling {effective}",
        )
        return base
    child_context = {
        "schema_version": 1,
        "snapshot_id": registry["snapshot"]["id"],
        "kind": "model",
        "caller": target_identity,
        "inherited_ceiling": effective,
    }
    base.update(
        allowed=True,
        code="same-score-allowed" if target_score == effective else "downward-allowed",
        message="target exact AA score is at or below effective caller ceiling",
        child_context=child_context,
    )
    return base


def _normalized_effort(value: str) -> str | None:
    return None if value in ("", "-") else value


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def atomic_bytes(path: Path, content: bytes) -> None:
    """Publish private metadata without exposing a partial file."""
    fd, name = tempfile.mkstemp(prefix=".aa-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_bytes(path, _json_line(value).encode("utf-8"))


def publish_context(directory: str | Path, registry: dict[str, Any],
                    caller: dict[str, Any] | None, decision: dict[str, Any],
                    registry_source: str | Path | None = None) -> dict[str, Any]:
    """Keep immutable-by-convention authorizer and a distinct narrowed child identity.

    This is lineage metadata, not authentication. The frozen registry content is
    copied so later retries cannot silently select a different score snapshot.
    """
    root = Path(directory)
    _check(root.is_dir() and not root.is_symlink(), "invalid context publication directory")
    _check(decision.get("allowed") is True, "cannot publish a denied decision")
    source = Path(registry_source) if registry_source else Path(__file__).resolve().parents[2] / "config/aa-model-policy.json"
    source_bytes = _read_bytes(source)
    _check(hashlib.sha256(source_bytes).hexdigest() == APPROVED_REGISTRY_SHA256, "unapproved AA registry snapshot publication")
    atomic_bytes(root / "aa-registry.json", source_bytes)
    registry_sha = hashlib.sha256((root / "aa-registry.json").read_bytes()).hexdigest()
    if caller is not None:
        atomic_json(root / "aa-authorizer.json", caller)
    child = decision.get("child_context")
    if child is not None:
        atomic_json(root / "aa-child-context.json", child)
    lineage = {
        "schema_version": 1,
        "registry_sha256": registry_sha,
        "authorizer_sha256": hashlib.sha256((root / "aa-authorizer.json").read_bytes()).hexdigest() if caller else None,
        "operator_asserted_human": decision["caller_kind"] == "operator-asserted-human",
        "target_config_id": decision["target_config_id"],
        "target_request": decision["target_request"],
        "effective_ceiling": decision["effective_ceiling"],
        "child_context": child,
    }
    overlay_path = os.environ.get("OMNILANE_AA_TRANSPORT_OVERLAY")
    if overlay_path:
        overlay, _ = _read_json(overlay_path)
        atomic_json(root / "aa-transport-overlay.json", overlay)
        lineage["transport_overlay_sha256"] = hashlib.sha256((root / "aa-transport-overlay.json").read_bytes()).hexdigest()
    atomic_json(root / "aa-lineage.json", lineage)
    atomic_json(root / "aa-decision.json", decision)
    return lineage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--expected-registry-sha256")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--caller-context")
    source.add_argument("--operator-asserted-human", action="store_true")
    parser.add_argument("--expected-caller-sha256")
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--target-config")
    parser.add_argument("--publish-dir")
    args = parser.parse_args(argv)
    try:
        registry, registry_sha = load_registry(args.registry, args.expected_registry_sha256)
        caller = None
        caller_sha = None
        if args.caller_context:
            caller, caller_sha = load_caller(
                args.caller_context, registry, args.expected_caller_sha256
            )
        result = decide(
            registry,
            registry_sha,
            vendor=args.vendor,
            model=args.model,
            effort=_normalized_effort(args.effort),
            caller=caller,
            caller_sha256=caller_sha,
            operator_asserted_human=args.operator_asserted_human,
            target_config=args.target_config,
        )
        if args.publish_dir and result["allowed"]:
            publish_context(args.publish_dir, registry, caller, result, args.registry)
        print(_json_line(result), end="")
        return 0 if result["allowed"] else 3
    except (PolicyError, OSError, RecursionError, TypeError, KeyError) as error:
        message = str(error) if isinstance(error, PolicyError) else "invalid or inaccessible AA policy input"
        print(_json_line({
            "schema_version": 1,
            "allowed": False,
            "code": "invalid-policy-input",
            "message": message,
        }), end="")
        return 2


if __name__ == "__main__":
    sys.exit(main())
