"""Host-local permission to probe a changed executable, never dispatch authority."""

from __future__ import annotations
import contextlib
import fcntl
import json
import os
import socket
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cli_provenance as provenance

VENDORS = ("claude", "codex", "gemini", "grok")
LOCATION_KINDS = frozenset(("adhoc", "script", "no-platform-signature"))
FIRST_USE_KINDS = LOCATION_KINDS | {"developer-id"}
UNSAFE_KINDS = frozenset(("invalid", "unsigned", "unverifiable"))
MAX_BYTES = 128 * 1024


class OriginError(ValueError):
    pass


def policy_path():
    return Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane") / "transport-origins.local.json"


def install_family(path):
    if not isinstance(path, (str, Path)) or not str(path) or not Path(path).is_absolute():
        raise OriginError("missing or invalid executable path")
    return provenance.family(Path(path).resolve())


def _text(value, empty=False):
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= 4096 and not any(ord(c) < 32 for c in value)


def validate(data):
    if not isinstance(data, dict) or set(data) != {"schema_version", "host", "vendors"}:
        raise OriginError("invalid origin policy fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise OriginError("invalid origin schema")
    if data["host"] != socket.gethostname():
        raise OriginError("origin policy belongs to another host")
    if not isinstance(data["vendors"], dict) or set(data["vendors"]) - set(VENDORS):
        raise OriginError("invalid origin vendors")
    for entry in data["vendors"].values():
        if not isinstance(entry, dict) or set(entry) != {"channels"} or not isinstance(entry["channels"], list):
            raise OriginError("invalid origin channels")
        seen = set()
        for channel in entry["channels"]:
            if not isinstance(channel, dict):
                raise OriginError("invalid channel")
            kind = channel.get("kind")
            keys = {"kind", "install_family", "approved_at", "approved_via"}
            if not isinstance(kind, str):
                raise OriginError("invalid channel kind")
            if kind == "developer-id":
                keys |= {"team_id", "identifier_family"}
                if not isinstance(channel.get("team_id"), str) or not provenance.TEAM_ID.fullmatch(channel["team_id"]):
                    raise OriginError("invalid origin team")
                if not _text(channel.get("identifier_family"), empty=True):
                    raise OriginError("missing identifier family")
            elif kind == "location":
                keys.add("accepts")
                if not isinstance(channel.get("accepts"), str) or channel["accepts"] not in LOCATION_KINDS:
                    raise OriginError("invalid location kind")
            else:
                raise OriginError("unknown origin kind")
            if set(channel) != keys:
                raise OriginError("unknown or missing channel fields")
            location = channel["install_family"]
            if not _text(location) or not location.startswith("/") or ".." in Path(location).parts:
                raise OriginError("invalid install family")
            if channel["approved_via"] not in ("operator", "first-use", "migrated-from-overlay"):
                raise OriginError("invalid approval source")
            try:
                stamp = datetime.fromisoformat(channel["approved_at"])
                if stamp.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError):
                raise OriginError("invalid approval time") from None
            key = (kind, channel.get("accepts"), location)
            if key in seen:
                raise OriginError("duplicate channel kind and location")
            seen.add(key)
    return data


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OriginError("duplicate JSON key")
        result[key] = value
    return result


def _read(path):
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if path.is_symlink():
            raise OriginError("symbolic origin policy") from None
        return None
    except OSError:
        raise OriginError("cannot open origin policy") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise OriginError("origin policy must be an owned regular 0600 file")
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise OriginError("origin policy too large")
        return raw


def load(path=None):
    path = Path(path) if path is not None else policy_path()
    raw = _read(path)
    if raw is None:
        return {"schema_version": 1, "host": socket.gethostname(), "vendors": {}}
    try:
        return validate(json.loads(raw, object_pairs_hook=_pairs,
                                   parse_constant=lambda _: (_ for _ in ()).throw(OriginError("invalid JSON number"))))
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise OriginError("invalid origin policy: " + str(error)) from None


def _channel(kind, path, current, via="operator", team=None):
    channel = dict(kind="developer-id" if kind == "developer-id" else "location",
                   install_family=install_family(path),
                   approved_at=datetime.now(timezone.utc).isoformat(), approved_via=via)
    if kind == "developer-id":
        channel.update(team_id=team or current.get("signer"),
                       identifier_family=provenance.family(current.get("identifier") or ""))
    else:
        channel["accepts"] = kind
    return channel


def implicit_channels(recorded, recorded_path):
    if not isinstance(recorded, dict) or not recorded_path:
        return []
    try:
        channels = []
        signer = recorded.get("signer")
        if (isinstance(signer, str) and provenance.TEAM_ID.fullmatch(signer)
                and recorded.get("valid") is True
                and _text(recorded.get("identifier"))
                and recorded.get("kind", "developer-id") == "developer-id"):
            channels.append(_channel("developer-id", recorded_path, recorded, "migrated-from-overlay"))
        if recorded.get("operator_trust") == provenance.TRUST_ADHOC:
            channels.append(_channel("adhoc", recorded_path, recorded, "migrated-from-overlay"))
        return channels
    except (OriginError, TypeError):
        return []


def channels_for(vendor, recorded=None, recorded_path=None, path=None):
    if vendor not in VENDORS:
        raise OriginError("unknown vendor")
    data = load(path)
    if vendor in data["vendors"]:
        return data["vendors"][vendor]["channels"]
    return implicit_channels(recorded, recorded_path)


def gate(vendor, recorded, recorded_path, current, current_path, path=None):
    """Pure decision apart from reading policy. No execution, registration or waiver."""
    kind = current.get("kind") if isinstance(current, dict) else None
    if not isinstance(kind, str):
        return False, "unverifiable"
    if kind in UNSAFE_KINDS or kind not in LOCATION_KINDS | {"developer-id"}:
        return False, kind if kind in UNSAFE_KINDS else "unverifiable"
    if kind in ("developer-id", "adhoc") and current.get("valid") is not True:
        return False, "invalid"
    if kind == "developer-id" and (not isinstance(current.get("signer"), str)
            or not provenance.TEAM_ID.fullmatch(current["signer"]) or not _text(current.get("identifier"))):
        return False, "unverifiable"
    if kind == "adhoc" and current.get("signer") != "adhoc":
        return False, "unverifiable"
    try:
        channels = channels_for(vendor, recorded, recorded_path, path)
        location = install_family(current_path)
    except (OriginError, OSError, RuntimeError):
        return False, "policy-invalid"
    matching_kind = [c for c in channels if (c["kind"] == "developer-id" and kind == "developer-id")
                     or (c["kind"] == "location" and c["accepts"] == kind)]
    at_location = [c for c in matching_kind if c["install_family"] == location]
    if not at_location:
        return False, "location-changed" if matching_kind else "origin-unapproved"
    if kind == "developer-id":
        teams = [c for c in at_location if c["team_id"] == current["signer"]]
        if not teams:
            return False, "team-changed"
        if not any(not c["identifier_family"] or c["identifier_family"] == provenance.family(current["identifier"]) for c in teams):
            return False, "identifier-changed"
    return True, "origin-approved"


@contextlib.contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OriginError("invalid origin lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _save(path, data):
    validate(data)
    payload = (json.dumps(data, indent=2) + "\n").encode()
    if len(payload) > MAX_BYTES:
        raise OriginError("origin policy too large")
    raw = _read(path)
    if raw is not None:
        backup = Path(str(path) + ".before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        fd = os.open(str(backup), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    fd, name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path) != raw:
            raise OriginError("origin policy changed during write")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def record(vendor, channel, recorded=None, recorded_path=None, path=None):
    return record_many([(vendor, channel, recorded, recorded_path)], path)


def record_many(changes, path=None):
    path = Path(path) if path is not None else policy_path()
    with _lock(path):
        data = load(path)
        key = lambda c: (c["kind"], c.get("accepts"), c["install_family"])
        for vendor, channel, recorded, recorded_path in changes:
            if vendor not in VENDORS:
                raise OriginError("unknown vendor")
            old = (data["vendors"][vendor]["channels"] if vendor in data["vendors"]
                   else implicit_channels(recorded, recorded_path))
            data["vendors"][vendor] = {"channels": [c for c in old if key(c) != key(channel)] + [channel]}
        _save(path, data)


def revoke(vendor, kind, recorded=None, recorded_path=None, path=None):
    if vendor not in VENDORS or kind not in LOCATION_KINDS | {"developer-id", "location"}:
        raise OriginError("unknown vendor or channel kind")
    path = Path(path) if path is not None else policy_path()
    with _lock(path):
        data = load(path)
        old = channels_for(vendor, recorded, recorded_path, path)
        data["vendors"][vendor] = {"channels": [c for c in old if c["kind"] != kind and c.get("accepts") != kind]}
        _save(path, data)


def preserve_before_override(vendor, recorded, recorded_path, path=None):
    """Freeze old grants before a one-shot probe changes the overlay's signer.

    Otherwise the next load would migrate the newly observed signer as an old
    approval. This records no new grant, including for revoked/empty vendors.
    """
    if vendor not in VENDORS:
        raise OriginError("unknown vendor")
    path = Path(path) if path is not None else policy_path()
    with _lock(path):
        data = load(path)
        if vendor not in data["vendors"]:
            data["vendors"][vendor] = {"channels": implicit_channels(recorded, recorded_path)}
            _save(path, data)


def record_first_use(vendor, current, current_path, recorded=None, recorded_path=None, path=None):
    """Phase 2 calls this only before the first probe, under its resign lock."""
    path = Path(path) if path is not None else policy_path()
    with _lock(path):
        data = load(path)
        if vendor not in VENDORS or recorded is not None or recorded_path is not None or vendor in data["vendors"]:
            return False
        kind = current.get("kind") if isinstance(current, dict) else None
        if not isinstance(kind, str):
            return False
        if kind not in FIRST_USE_KINDS or kind not in LOCATION_KINDS | {"developer-id"}:
            return False
        if kind in ("developer-id", "adhoc") and current.get("valid") is not True:
            return False
        if kind == "adhoc" and current.get("signer") != "adhoc":
            return False
        if kind == "developer-id" and not _text(current.get("identifier")):
            return False
        channel = _channel(kind, current_path, current, "first-use")
        data["vendors"][vendor] = {"channels": [channel]}
        try:
            _save(path, data)
        except OriginError:
            return False
        return True
