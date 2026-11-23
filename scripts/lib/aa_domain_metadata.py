"""Validate version-1 domain evidence for an AA registry without granting authority."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
import re
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
EVIDENCE_KINDS = ("measured", "estimated", "derived", "unknown")

# Units are part of each metric's identity. Raw AA fractions stay fractions.
MEASURE_UNITS = {
    "terminal-bench-4.0": "ratio",
    "terminal-bench-2.1": "ratio",
    "scicode": "ratio",
    "hallucination-rate": "ratio",
    "omniscience": "index",
    "hle": "ratio",
    "gpqa": "ratio",
    "critpt": "ratio",
    "briefcase-analytical": "elo",
    "briefcase-overall": "elo",
    "briefcase-presentation": "elo",
    "gdpval-normalized": "ratio",
    "automationbench": "ratio",
    "mmmu-pro": "ratio",
    "mlcr-overall": "ratio",
    "aa-lcr": "ratio",
    "index-time-per-task": "seconds",
    "time-to-first-answer-token": "seconds",
    "index-run-cost": "usd",
}
VENDOR_DOMAINS = {
    "codex": ("openai.com",),
    "claude": ("anthropic.com", "claude.com"),
    "gemini": ("google.com", "google.dev", "blog.google", "deepmind.google"),
    "grok": ("x.ai",),
}
MEASUREMENT_FIELDS = {
    "lane", "measure_id", "value", "unit", "observed_at", "source_ref", "evidence_kind", "missing_reason",
}
OBSERVATION_KEY_FIELDS = ("lane", "measure_id", "config_id", "observed_at", "source_ref", "evidence_kind")
CLAIM_FIELDS = {
    "vendor", "product", "effort", "summary", "official_url", "verified_at", "surface",
    "plan", "region", "limitations",
}


def _object(value, field, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{field}: expected an object")
    missing = required - value.keys()
    if missing:
        raise ValueError(f"{field}.{min(missing)}: required")
    extra = value.keys() - required - set(optional)
    if extra:
        raise ValueError(f"{field}.{min(extra)}: unknown field")


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise ValueError(f"{field}: expected nonempty text")


def _schema(value, field):
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError(f"{field}: expected schema version {SCHEMA_VERSION}")


def _date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"{field}: expected YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field}: invalid calendar date") from None


def _array(value, field):
    if not isinstance(value, list):
        raise ValueError(f"{field}: expected an array")


def _sha256(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{field}: expected lowercase SHA-256")


def relative_path(value, field):
    """Validate a repository-relative path before the caller resolves symlinks."""
    _text(value, field)
    if any(part in ("", ".", "..") for part in value.split("/")) or any(c in value for c in "\\:%?#"):
        raise ValueError(f"{field}: expected a relative repository path without traversal")
    return value


def _https_host(value, field):
    _text(value, field)
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError(f"{field}: invalid HTTPS URL") from None
    if (
        parsed.scheme != "https" or not host or "@" in parsed.netloc or "\\" in value
        or any(c.isspace() for c in value) or port not in (None, 443)
        or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z](?:[a-z0-9-]*[a-z0-9])?", host)
    ):
        raise ValueError(f"{field}: expected an HTTPS URL with a valid hostname")
    return host


def _source(value, field):
    _text(value, field)
    if "://" in value:
        _https_host(value, field)
    else:
        path, separator, fragment = value.partition("#")
        relative_path(path, field)
        if separator and not fragment:
            raise ValueError(f"{field}: empty source fragment")


def _observation_key(row):
    fields = {name: row.get(name) for name in OBSERVATION_KEY_FIELDS}
    source = fields["source_ref"]
    if "://" in source:
        # Validation permits only HTTPS and its default port. Keep the raw suffix,
        # including empty query or fragment delimiters, and leave the input row intact.
        parsed = urlsplit(source)
        suffix = source[len("https://") + len(parsed.netloc):]
        fields["source_ref"] = f"https://{parsed.hostname}{suffix}"
    return tuple(fields.values())


def _measurement(row, field, config_ids):
    _object(row, field, MEASUREMENT_FIELDS, {"config_id"})
    lane = row["lane"]
    if not isinstance(lane, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", lane):
        raise ValueError(f"{field}.lane: expected a lane identifier")
    metric = row["measure_id"]
    if not isinstance(metric, str) or metric not in MEASURE_UNITS:
        raise ValueError(f"{field}.measure_id: unknown metric")
    unit = MEASURE_UNITS[metric]
    if row["unit"] != unit:
        raise ValueError(f"{field}.unit: expected {unit} for {metric}")
    config_id = row.get("config_id")
    if config_id is not None:
        _text(config_id, f"{field}.config_id")
        if config_id not in config_ids:
            raise ValueError(f"{field}.config_id: not in registry configs")
    _date(row["observed_at"], f"{field}.observed_at")
    _source(row["source_ref"], f"{field}.source_ref")
    kind, value, reason = row["evidence_kind"], row["value"], row["missing_reason"]
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"{field}.evidence_kind: unknown evidence kind")
    if kind == "unknown":
        if value is not None:
            raise ValueError(f"{field}.value: unknown evidence must be null")
        _text(reason, f"{field}.missing_reason")
        return
    if not (type(value) is int or (type(value) is float and math.isfinite(value))):
        raise ValueError(f"{field}.value: expected a finite number")
    if (unit == "ratio" and not 0 <= value <= 1) or (unit in ("seconds", "usd") and value < 0):
        raise ValueError(f"{field}.value: outside {unit} range")
    if reason is not None:
        raise ValueError(f"{field}.missing_reason: expected null for available evidence")


def _claim(row, field):
    _object(row, field, CLAIM_FIELDS)
    for key in sorted(CLAIM_FIELDS - {"official_url", "verified_at"}):
        _text(row[key], f"{field}.{key}")
    vendor = row["vendor"]
    if vendor not in VENDOR_DOMAINS:
        raise ValueError(f"{field}.vendor: unknown vendor")
    _date(row["verified_at"], f"{field}.verified_at")
    host = _https_host(row["official_url"], f"{field}.official_url")
    if not any(host == base or host.endswith("." + base) for base in VENDOR_DOMAINS[vendor]):
        raise ValueError(f"{field}.official_url: expected an official {vendor} domain")


def _unique_json_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"{key}: duplicate JSON field")
        obj[key] = value
    return obj


def _nonfinite_json(value):
    raise ValueError(f"non-JSON number {value}")


def decode_json(raw: bytes, field: str):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object,
                          parse_constant=_nonfinite_json)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{field}: invalid JSON: {exc}") from None


def validate_registry(registry: dict) -> set[str]:
    if not isinstance(registry, dict):
        raise ValueError("registry: expected an object")
    _schema(registry.get("schema_version"), "registry.schema_version")
    snapshot = registry.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("registry.snapshot: expected an object")
    _text(snapshot.get("id"), "registry.snapshot.id")
    if not isinstance(registry.get("policy"), dict):
        raise ValueError("registry.policy: expected an object")
    _array(registry.get("scored_configs"), "registry.scored_configs")
    ids = set()
    for collection in ("scored_configs", "unknown_configs", "reference_configs"):
        rows = registry.get(collection, [])
        _array(rows, f"registry.{collection}")
        for index, row in enumerate(rows):
            field = f"registry.{collection}[{index}].id"
            if not isinstance(row, dict):
                raise ValueError(f"{field}: expected an object with an id")
            config_id = row.get("id")
            _text(config_id, field)
            if config_id in ids:
                raise ValueError(f"{field}: duplicate config id")
            ids.add(config_id)
    return ids


def registry_payload_sha256(registry: dict) -> str:
    """Hash the canonical registry payload, excluding only the domain pointer."""
    snapshot = {key: value for key, value in registry["snapshot"].items() if key != "domain_strength"}
    try:
        payload = json.dumps(dict(registry, snapshot=snapshot), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"registry: cannot canonicalize payload: {exc}") from None
    return hashlib.sha256(payload).hexdigest()


def validate_pointer(pointer: dict) -> str:
    field = "registry.snapshot.domain_strength"
    _object(pointer, field, {"path", "schema_version", "sha256"})
    _schema(pointer["schema_version"], f"{field}.schema_version")
    _sha256(pointer["sha256"], f"{field}.sha256")
    return relative_path(pointer["path"], f"{field}.path")


def validate_companion(companion: dict, registry: dict) -> None:
    config_ids = validate_registry(registry)
    _object(companion, "companion", {"schema_version", "snapshot_id", "registry_sha256",
                                      "measurements", "vendor_claims"})
    _schema(companion["schema_version"], "companion.schema_version")
    _text(companion["snapshot_id"], "companion.snapshot_id")
    if companion["snapshot_id"] != registry["snapshot"]["id"]:
        raise ValueError("companion.snapshot_id: does not match registry.snapshot.id")
    _sha256(companion["registry_sha256"], "companion.registry_sha256")
    if companion["registry_sha256"] != registry_payload_sha256(registry):
        raise ValueError("companion.registry_sha256: does not match registry payload")
    _array(companion["measurements"], "companion.measurements")
    _array(companion["vendor_claims"], "companion.vendor_claims")
    observations = {}
    for index, row in enumerate(companion["measurements"]):
        field = f"companion.measurements[{index}]"
        _measurement(row, field, config_ids)
        key = _observation_key(row)
        if key in observations:
            identity = ", ".join(f"{name}={value!r}" for name, value in zip(OBSERVATION_KEY_FIELDS, key))
            previous = observations[key]
            if previous != row["value"]:
                raise ValueError(f"{field}.value: conflicting values for observation ({identity}): "
                                 f"{previous!r} and {row['value']!r}")
            raise ValueError(f"{field}: duplicate observation ({identity})")
        observations[key] = row["value"]
    for index, row in enumerate(companion["vendor_claims"]):
        _claim(row, f"companion.vendor_claims[{index}]")
