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
# Approval anchor for the frozen AA source bytes. Updating these constants is a
# governance change, never a caller argument/environment override.
# scripts/aa_rebaseline.py regenerates the registry; it never touches this pin.
APPROVED_BENCHMARK_VERSION = "4.3.2"
APPROVED_AS_OF = "2026-10-18"
APPROVED_REGISTRY_SHA256 = "6ac4158930fac4f4b435e837e374fa1434ce9c29e8a122e9be57ff68e1a3e1b5"

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
    _check(snapshot["benchmark_version"] == APPROVED_BENCHMARK_VERSION
           and snapshot["as_of"] == APPROVED_AS_OF
           and snapshot["frozen"] is True,
           f"AA registry is not frozen v{APPROVED_BENCHMARK_VERSION} dated {APPROVED_AS_OF}")
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
        _check(row["benchmark_version"] == APPROVED_BENCHMARK_VERSION
               and row["as_of"] == APPROVED_AS_OF,
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
    validate_transport_overlay(registry, overlay, digest)


def validate_transport_overlay(registry: dict[str, Any], overlay: dict[str, Any], digest: str) -> None:
    """Shared read-only validation for dispatch, maintenance, and diagnostic tools."""
    import transport_compat as compat
    _check(type(overlay.get("schema_version")) is int and overlay["schema_version"] in (1, 2),
           "unsupported transport overlay")
    _check(not ({"carried_forward", "runtime_verified"} & set(overlay)),
           "untrusted transport carry-forward assertion")
    _check(overlay.get("host") == socket.gethostname(), "transport overlay host mismatch")
    source_rows, source, compatibility_sha = compat.source_rows(overlay, registry)
    cross_snapshot = overlay["snapshot_id"] != registry["snapshot"]["id"]
    current_rows = {row["id"]: row for row in registry["scored_configs"]}
    _check(len(current_rows) == len(registry["scored_configs"]), "duplicate AA scored configuration")
    identities = [tuple(row[k] for k in IDENTITY_FIELDS) for row in registry["scored_configs"]]
    _check(len(set(identities)) == len(identities), "duplicate AA configuration identity")
    _check(isinstance(overlay.get("evidence"), list), "invalid transport evidence list")
    _check(isinstance(overlay.get("mappings"), list), "invalid transport mappings")
    if overlay["schema_version"] == 2:
        _check(overlay.get("compatibility_version") == compat.VERSION,
               "unsupported transport compatibility version")
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
    records = compat.proof_records(overlay, source_rows, source) if cross_snapshot or overlay["schema_version"] == 2 else {}
    from probe_failure import pinned_failures
    pinned_failures(overlay)
    mapped_ids = {m.get("config_id") for m in overlay["mappings"] if isinstance(m, dict)}
    _check(not any(u.get("config_id") in mapped_ids for u in overlay.get("unproven", [])
                   if isinstance(u, dict)), "contradictory verified and unproven transport row")
    audit = []
    unverified = {}
    seen = set()
    tiers: dict[str, str] = {}
    for mapping in overlay.get("mappings", []):
        _check(isinstance(mapping, dict), "invalid transport mapping")
        allowed_fields = {"config_id", "identity", "verification", "runtime_effort", "runtime_model",
                          "selector_type", "cli_flag", "evidence_tier", "pending_recheck", "expires_at"}
        if overlay["schema_version"] == 2:
            allowed_fields.update(("source_snapshot_id", "source_row_digest", "transport_contract_digest",
                                   "evidence_refs", "verified_at"))
        _check(set(mapping) <= allowed_fields, "untrusted transport mapping fields")
        cid = mapping.get("config_id")
        _check(isinstance(cid, str) and cid not in seen, "duplicate overlay config")
        seen.add(cid)
        _check(cid in source_rows, "unknown overlay config")
        row = source_rows[cid]
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
        files, descriptor = records.get(cid, ({}, {}))
        if cross_snapshot or overlay["schema_version"] == 2:
            _check(bool(files), "overlay mapping has no anchored probe evidence")
            _check(any(e.get("vendor") == row["vendor"] for e in overlay["evidence"]),
                   "overlay mapping has no executable evidence")
        if overlay["schema_version"] == 2:
            _check(mapping.get("source_snapshot_id") == overlay["snapshot_id"],
                   "overlay source snapshot mismatch")
            _check(mapping.get("source_row_digest") == compat.digest(compat.row_contract(row)),
                   "overlay source row digest mismatch")
            _check(mapping.get("evidence_refs") == files and
                   mapping.get("verified_at") == descriptor.get("probed_at"),
                   "overlay proof reference mismatch")
            # Use the source engine, not the newly installed engine, when validating provenance.
            source_engine = source["engine"] if source else compat.engine_contract()
            source_selector = source["selectors"].get(cid) if source else compat.selector_contract(row)
            expected_contract = compat.digest(dict(version=compat.VERSION, engine=source_engine,
                                                   selector=source_selector))
            _check(mapping.get("transport_contract_digest") == expected_contract,
                   "overlay transport contract digest mismatch")
        if cid not in current_rows:
            continue  # A valid proof for a retired row is not an error or dispatch authority.
        if cross_snapshot and not compat.equivalent(row, current_rows[cid], source):
            unverified[cid] = "request-contract-changed"
            continue
        if compat.expired(mapping.get("expires_at")) or compat.expired(descriptor.get("expires_at")):
            unverified[cid] = "evidence-expired"
            continue
        row = current_rows[cid]
        if row["vendor"] in stale_vendors:
            continue
        if cross_snapshot:
            audit.append(dict(config_id=cid, source_snapshot_id=overlay["snapshot_id"],
                              target_snapshot_id=registry["snapshot"]["id"],
                              source_overlay_sha256=digest, evidence_refs=files,
                              source_row_digest=compat.digest(compat.row_contract(row)),
                              compatibility_version=compat.VERSION,
                              compatibility_sha256=compatibility_sha))
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
    registry["_transport_carry_forward"] = audit
    registry["_transport_unverified"] = unverified
    from probe_failure import CONCLUSIVE_REASONS, environment_reason
    # Display-only hints; never feed failure metadata into verified mappings.
    registry["_transport_environment_failures"] = {
        item.get("config_id"): environment_reason(item.get("verdict_reason"))
        for item in overlay.get("unproven", []) if isinstance(item, dict)
        and item.get("config_id") in current_rows and environment_reason(item.get("verdict_reason"))}
    registry["_transport_conclusive_failures"] = {
        item["config_id"]: item["verdict_reason"]
        for item in overlay.get("unproven", []) if isinstance(item, dict)
        and item.get("config_id") in current_rows
        and isinstance(item.get("verdict_reason"), str)
        and item["verdict_reason"].split(":", 1)[0] in CONCLUSIVE_REASONS}
    registry["_transport_compatibility_sha256"] = compatibility_sha
    registry["_transport_executable_paths"] = {
        vendor: [e["path"] for e in overlay["evidence"] if e.get("vendor") == vendor
                 and Path(e["path"]).name != f"run-{vendor}.sh"]
        for vendor in TRANSPORT_EVIDENCE_VENDORS}


def evidence_digest(path: str) -> str:
    """Hash a raw proof without following links or loading its possibly large contents."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _check(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "invalid transport evidence file")
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


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
    # effort_unverified marks a caller degraded to its model's lowest-scored row
    # because the launching harness recorded no effort; it may only be true.
    unverified = value.pop("effort_unverified", None)
    _check(unverified is None or unverified is True, "invalid caller-context effort_unverified")
    _exact_fields(value, {"schema_version", "snapshot_id", "kind", "caller", "inherited_ceiling"})
    if unverified:
        value["effort_unverified"] = True
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
    vendor_rows = [row for row in exact_id if row["vendor"] == vendor]
    candidates: list[dict[str, Any]] = []
    unresolved: list[str] = []
    missing_effort = False
    for row in vendor_rows:
        mapping = row["transport_mapping"]
        model_ids = mapping.get("candidate_model_ids", [])
        runtime_model = mapping.get("runtime_model")
        runtime_effort = mapping.get("runtime_effort", row["effort"])
        model_match = model == runtime_model if runtime_model is not None else model in model_ids
        encoded_effort = mapping.get("selector_type") == "model_id_encoded_effort"
        if not model_match:
            continue
        verified = mapping.get("runtime_verified") is True and mapping.get("status") in ("verified", "resolved")
        # A known alias can expose a conflicting effort before transport proof
        # exists. This inference only diagnoses; it never verifies a mapping.
        if not verified and runtime_effort and model.endswith(f"-{runtime_effort}"):
            encoded_effort = True
        if encoded_effort and effort not in (None, runtime_effort):
            return None, "encoded-effort-conflict", {"model": model, "effort": effort, "encoded_effort": runtime_effort}
        if not encoded_effort and effort != runtime_effort:
            if effort is None:
                if verified:
                    missing_effort = True
                else:
                    unresolved.append(row["id"])
            continue
        if verified:
            candidates.append(row)
        else:
            unresolved.append(row["id"])
    if not candidates and not unresolved and not missing_effort:
        available_models = sorted({
            name
            for row in vendor_rows
            for name in ([row["transport_mapping"]["runtime_model"]]
                         if row["transport_mapping"].get("runtime_model") is not None
                         else row["transport_mapping"].get("candidate_model_ids", []))
        })
        return None, "unknown-model-name", {
            "vendor": vendor, "model": model, "effort": effort,
            "available_models": available_models,
        }
    request = registry.get("_runtime_executable_request")
    if request is not None and any(item["config_id"] in {r["id"] for r in exact_id if r["vendor"] == vendor}
                                   for item in registry.get("_transport_carry_forward", [])):
        if not request or str(Path(request).resolve()) not in registry.get("_transport_executable_paths", {}).get(vendor, []):
            return None, "runtime-executable-changed", {"vendor": vendor, "model": model, "effort": effort}
    if vendor in registry.get("_stale_transport_vendors", []):
        return None, "unknown-target-runtime", {"vendor": vendor, "model": model, "effort": effort}
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
    if unresolved and not missing_effort:
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
            message=("no caller identity: a model caller runs `omnilane whoami` and passes the "
                     "file it prints as --caller-context, which dispatch does itself when the "
                     "launching CLI names its model and effort; a human operator passes "
                     "--operator-asserted-human"),
            failed_gate="caller-identity",
            reason="no caller identity reached the gate",
            next_command="omnilane whoami",
        )
        return base
    base["caller_context_sha256"] = caller_sha256
    caller_identity = caller["caller"]
    degraded = caller.get("effort_unverified") is True
    base["caller_degraded"] = degraded
    caller_rows = _matching_rows(registry, caller_identity)
    if len(caller_rows) != 1:
        detail = _unknown_reason(registry, caller_identity)
        base.update(
            code="unknown-caller-config" if not caller_rows else "ambiguous-caller-config",
            message=detail or "caller exact vendor/model/effort/reasoning/fallback is not uniquely scored",
            caller=caller_identity,
            failed_gate="caller-identity",
            reason="the caller context names a configuration the frozen registry does not score exactly once",
            next_command="omnilane whoami",
        )
        return base
    caller_row = caller_rows[0]
    model_rows = [row for row in registry["scored_configs"]
                  if row["vendor"] == caller_row["vendor"] and row["model"] == caller_row["model"]]
    if degraded and caller_row["score"] != min(row["score"] for row in model_rows):
        base.update(
            code="invalid-degraded-caller",
            message="an effort-unverified caller must name its model's lowest-scored row",
            caller=caller_identity,
            failed_gate="caller-identity",
            reason="effort_unverified was set on a row above the model's floor",
            next_command="omnilane whoami",
        )
        return base
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
        if mapping_code == "unknown-model-name":
            selectors = detail["available_models"]
            effort_en = f" with effort {effort!r}" if effort is not None else ""
            effort_zh = f"（指定強度：{effort}）" if effort is not None else ""
            registered_en = (f"Registered model selectors in this scope: {', '.join(selectors)}. "
                             if selectors else "No registered model selectors in this scope. ")
            registered_zh = (f"此範圍已登錄的型號寫法：{'、'.join(selectors)}。"
                             if selectors else "此範圍沒有已登錄型號。")
            base.update(
                code=mapping_code,
                failed_gate="target-model-name",
                message=(f"Vendor {vendor}: no configuration matches model {model!r}{effort_en}. "
                         f"{registered_en}Listing is not dispatch approval; matching effort, "
                         "transport verification and caller ceiling still apply. See omnilane list."),
                message_zh_tw=(f"廠商「{vendor}」找不到符合「{model}」{effort_zh}的型號設定。"
                               f"{registered_zh}名稱列入清單不代表可派工；仍須符合強度、"
                               "傳輸驗證與呼叫端上限。請執行 omnilane list 查看。"),
                reason="no scored configuration matches the requested vendor/model/effort selector",
                next_command="omnilane list",
                **detail)
            return base
        from verification_status import transport_guidance
        guidance = transport_guidance(vendor, target_config, detail.get("candidate_config_ids", []),
                                      registry.get("_transport_environment_failures", {}),
                                      registry.get("_transport_conclusive_failures", ()))
        base.update(
            code=mapping_code,
            message=guidance["message"],
            failed_gate="target-transport",
            reason=("the caller is identified; this host's transport overlay does not currently "
                    f"verify {vendor} {model} at effort {effort}"),
            next_command=guidance["next_command"],
            requires_gui_session=guidance["requires_gui_session"],
            message_zh_tw=guidance["message_zh_tw"],
            **detail)
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
        reaching = sorted((row for row in model_rows if row["score"] >= target_score),
                          key=lambda row: (row["score"], row["id"]))
        required = reaching[0]["effort"] or "none" if reaching else None
        if caller["inherited_ceiling"] < caller_row["score"]:
            reason = ("this worker inherited a ceiling below the target; only the session that "
                      "dispatched it, or a human operator, can reach this target")
            required = None
        elif required is None:
            reason = (f"no scored effort of {caller_row['model']} reaches {target_score}; a "
                      "stronger caller model or a human operator has to dispatch this target")
        elif degraded:
            reason = (f"the launching harness recorded no effort, so the caller was held to "
                      f"{caller_row['model']}'s floor of {effective}; relaunch the session with an "
                      f"explicit effort of {required} or higher")
        else:
            reason = (f"relaunch the calling session at effort {required} or higher, or have a "
                      "human operator dispatch this target")
        base.update(
            code="target-above-effective-ceiling",
            message=f"target score {target_score} exceeds effective caller ceiling {effective}",
            failed_gate="downward-ceiling",
            reason=reason,
            required_caller_effort=required,
            next_command="omnilane list",
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


def decide_inherited(registry: dict[str, Any], registry_sha256: str, *,
                     caller: dict[str, Any] | None, caller_sha256: str | None,
                     operator_asserted_human: bool = False,
                     host_asserted: dict[str, str] | None = None) -> dict[str, Any]:
    """A native worker that inherits the caller's own model and effort unchanged.

    It runs what the caller runs, so it scores what the caller scores and cannot
    be an upward dispatch, whether or not the caller's effort is known. There is
    no external CLI, so no transport mapping is involved. It is nobody's lane
    target: the decision names no target configuration and must not be reported
    as satisfying one.

    None of that rests on knowing who the caller is, so a caller whose identity
    cannot be read still gets an inherited worker, on the host's word for its
    vendor and model. That decision is marked unverified, carries no ceiling and
    publishes no child context: a worker that tried to dispatch would have no
    identity to dispatch with.
    """
    base: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_id": registry["snapshot"]["id"],
        "registry_sha256": registry_sha256,
        "allowed": False,
        "code": "deny",
        "message": "AA policy denied dispatch",
        "caller_kind": "operator-asserted-human" if operator_asserted_human else "model",
        "target_request": {"vendor": None, "model": None, "effort": None, "inherit": True},
        "target_config_id": None,
        "caller_score": None,
        "inherited_ceiling": None,
        "effective_ceiling": None,
        "target_score": None,
        "target_estimated": None,
        "child_context": None,
        "evidence_limit": ("the host asserts that a sub-agent spawned without a model override "
                           "inherits the caller's runtime; omnilane cannot observe it"),
    }
    if operator_asserted_human:
        base.update(code="inherit-requires-model-caller",
                    message="only a model running inside a harness has a runtime to inherit",
                    failed_gate="caller-identity",
                    reason="a human operator has no sub-agent tool; dispatch a lane instead",
                    next_command="omnilane list")
        return base
    if caller is None and host_asserted is not None:
        base.update(allowed=True, code="native-inherited-unverified-caller",
                    message="the worker inherits the caller's own runtime, so it cannot score above it; "
                            "the caller's vendor and model are the host's statement, not a verified identity",
                    caller_kind="model-unverified", caller_identity_verified=False,
                    caller_identity_source="host-asserted",
                    target={"inherit": True, "vendor": host_asserted["vendor"],
                            "model": host_asserted["model"]})
        return base
    if caller is None:
        base.update(code="missing-caller-context",
                    message="no caller identity: an inherited worker still has to know whose runtime it inherits",
                    failed_gate="caller-identity",
                    reason="no caller identity reached the gate and the capability file names no current_model",
                    next_command="omnilane whoami  # if that cannot read this harness: omnilane "
                                 "native-context --vendor VENDOR --model MODEL --inherits-caller-runtime")
        return base
    base["caller_context_sha256"] = caller_sha256
    base["caller_identity_verified"] = True
    base["caller_identity_source"] = "caller-context"
    identity = caller["caller"]
    degraded = caller.get("effort_unverified") is True
    rows = _matching_rows(registry, identity)
    if len(rows) != 1:
        base.update(code="unknown-caller-config" if not rows else "ambiguous-caller-config",
                    message=_unknown_reason(registry, identity)
                    or "caller exact vendor/model/effort/reasoning/fallback is not uniquely scored",
                    caller=identity, failed_gate="caller-identity",
                    reason="the caller context names a configuration the frozen registry does not score exactly once",
                    next_command="omnilane whoami")
        return base
    row = rows[0]
    floor = min(r["score"] for r in registry["scored_configs"]
                if r["vendor"] == row["vendor"] and r["model"] == row["model"])
    if degraded and row["score"] != floor:
        base.update(code="invalid-degraded-caller",
                    message="an effort-unverified caller must name its model's lowest-scored row",
                    caller=identity, failed_gate="caller-identity",
                    reason="effort_unverified was set on a row above the model's floor",
                    next_command="omnilane whoami")
        return base
    effective = min(row["score"], caller["inherited_ceiling"])
    child = {"schema_version": 1, "snapshot_id": registry["snapshot"]["id"], "kind": "model",
             "caller": identity, "inherited_ceiling": effective}
    if degraded:
        child["effort_unverified"] = True
    base.update(allowed=True, code="native-inherited-allowed",
                message="the worker inherits the caller's own runtime, so it cannot score above it",
                caller=identity, caller_degraded=degraded, caller_score=row["score"],
                inherited_ceiling=caller["inherited_ceiling"], effective_ceiling=effective,
                target={"inherit": True, "vendor": row["vendor"], "model": row["model"]},
                child_context=child)
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
        lineage["transport_compatibility_sha256"] = registry.get("_transport_compatibility_sha256")
        lineage["transport_carry_forward"] = registry.get("_transport_carry_forward", [])
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
    parser.add_argument("--runtime-executable", default="")
    parser.add_argument("--publish-dir")
    args = parser.parse_args(argv)
    try:
        registry, registry_sha = load_registry(args.registry, args.expected_registry_sha256)
        registry["_runtime_executable_request"] = args.runtime_executable
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
            "next_command": "omnilane doctor",
            "message_zh_tw": "評分表或本機證據無法通過檢查。請先執行 omnilane doctor 查明來源、檔案與權限；不要直接加 --approve。",
        }), end="")
        return 2


if __name__ == "__main__":
    sys.exit(main())
