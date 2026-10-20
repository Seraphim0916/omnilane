#!/usr/bin/env python3
"""Host-written capability files for the inherited-worker fallback.

`omnilane native-context --install` runs outside any harness (a person at a
terminal, or an installer). For each harness whose own configuration names a
model, it writes $OMNILANE_HOME/native-context/host/<harness>.json: the model the
harness is configured to start with, marked as the host's statement
(caller_identity_verified: false, source: host-config), with no workdir or mode.

Dispatch calls `resolve` when it cannot read the caller and was given no
--native-context. The harness is told apart only by the environment the harness
sets; no signal, or the signals of more than one harness, is a refusal. The file
is used only if it still matches the install record next to it, and dispatch then
writes a standard capability file for this one --workdir and --mode.

The install record makes a change visible; it does not prevent one. A model runs
as the same user as the person who installed the file, so it can rewrite both.
That is accepted because an inherited worker never scores above its caller and
its completion is still checked against the stated model.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caller_identity  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ALIASES = REPO / "config" / "model-aliases.json"
SUPPORTED = ("codex", "claude-code")
VENDORS = {"codex": "codex", "claude-code": "claude"}
MAX_BYTES = 65536


class HostContextError(ValueError):
    pass


def home() -> Path:
    return Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")


def host_dir() -> Path:
    return home() / "native-context" / "host"


def harness_signals(environment=os.environ) -> list[str]:
    """Which harnesses the environment says this runs under, from variables the harness sets."""
    found = []
    if environment.get("CODEX_THREAD_ID") or environment.get("CODEX_SANDBOX"):
        found.append("codex")
    if environment.get("CLAUDECODE") == "1" or environment.get("CLAUDE_CODE_ENTRYPOINT"):
        found.append("claude-code")
    return found


def detect_harness(environment=os.environ) -> str:
    found = harness_signals(environment)
    if not found:
        raise HostContextError("no harness signal in the environment (CODEX_THREAD_ID, CODEX_SANDBOX, "
                               "CLAUDECODE, CLAUDE_CODE_ENTRYPOINT)")
    if len(found) > 1:
        raise HostContextError("the environment carries the signals of more than one harness: "
                               + ", ".join(found))
    return found[0]


def load_aliases(path: Path = ALIASES) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("aliases"), dict):
        raise HostContextError(f"invalid alias table {path}")
    return value["aliases"]


def _toml_strings(text: str) -> dict:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return _toml_fallback(text)
    return tomllib.loads(text)


def _toml_fallback(text: str) -> dict:
    """Top-level and `[profiles.NAME]` string keys of a Codex config, for Python before 3.11."""
    value: dict = {}
    section: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        header = re.fullmatch(r"\[\s*([^\]]+?)\s*\]", line)
        if header:
            section = [part.strip().strip('"') for part in header.group(1).split(".")]
            continue
        pair = re.fullmatch(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"', line)
        if not pair:
            continue
        if not section:
            value[pair.group(1)] = pair.group(2)
        elif len(section) == 2 and section[0] == "profiles":
            value.setdefault("profiles", {}).setdefault(section[1], {})[pair.group(1)] = pair.group(2)
    return value


def configured_model(harness: str, environment=os.environ) -> tuple[str, dict]:
    """The model a new session of `harness` starts with, and where that was read."""
    if harness == "codex":
        path = Path(environment.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
        config = _toml_strings(path.read_text(encoding="utf-8"))
        profile = config.get("profile")
        profiles = config.get("profiles") or {}
        if profile and isinstance(profiles.get(profile), dict) and profiles[profile].get("model"):
            return profiles[profile]["model"], {"file": str(path), "key": f"profiles.{profile}.model"}
        if config.get("model"):
            return config["model"], {"file": str(path), "key": "model"}
        raise HostContextError(f"{path} names no model")
    if harness == "claude-code":
        path = Path(environment.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        env_model = (settings.get("env") or {}).get("ANTHROPIC_MODEL")
        if env_model:
            return env_model, {"file": str(path), "key": "env.ANTHROPIC_MODEL"}
        if settings.get("model"):
            return settings["model"], {"file": str(path), "key": "model"}
        raise HostContextError(f"{path} names no model")
    raise HostContextError(f"unsupported harness {harness}")


def exact_model(harness: str, raw: str, registry: dict, aliases: dict) -> tuple[str, str | None]:
    vendor = VENDORS[harness]
    if caller_identity.floor_row(registry, vendor, raw):
        return raw, None
    mapped = (aliases.get(harness) or {}).get(raw)
    if not mapped:
        raise HostContextError(f"{harness}: '{raw}' is not a scored {vendor} model and "
                               f"{ALIASES.name} has no mapping for it")
    if not caller_identity.floor_row(registry, vendor, mapped):
        raise HostContextError(f"{harness}: alias '{raw}' maps to '{mapped}', which the registry does not score")
    return mapped, raw


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.replace(staging, path)


def install(harnesses: list[str], registry_path: str, environment=os.environ,
            log=print) -> tuple[int, list[Path]]:
    """Write one host file per harness; returns the exit code and the files written."""
    inside = harness_signals(environment)
    if inside:
        log(f"omnilane: --install runs outside a harness, not inside {', '.join(inside)}: run it from a "
            "terminal (this guards against accidents; it is not a security boundary)")
        return 2, []
    registry, _ = caller_identity.load_registry(registry_path)
    aliases = load_aliases()
    written, failed = [], 0
    for harness in harnesses:
        try:
            raw, source = configured_model(harness, environment)
            model, alias = exact_model(harness, raw, registry, aliases)
        except FileNotFoundError as error:
            log(f"omnilane: {harness}: skipped, no configuration ({error.filename})")
            continue
        except (HostContextError, ValueError, OSError) as error:
            log(f"omnilane: {harness}: not written: {error}")
            failed += 1
            continue
        installed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        value = {"schema_version": 1, "harness": harness, "vendor": VENDORS[harness],
                 "current_model": model, "inherits_caller_runtime": True,
                 "caller_identity_verified": False, "source": "host-config",
                 "source_detail": dict(source, value=raw, alias=alias), "installed_at": installed_at,
                 "limit": "the model a new session starts with; a session started with --model or "
                          "switched mid-conversation may run another, and completion then fails"}
        target = host_dir() / f"{harness}.json"
        _write_private(target, json.dumps(value, indent=2, sort_keys=True) + "\n")
        info = target.stat()
        record = {"schema_version": 1, "file": target.name, "sha256": _digest(target),
                  "size": info.st_size, "mtime_ns": info.st_mtime_ns, "installed_at": installed_at}
        _write_private(target.with_name(f"{harness}.install.json"), json.dumps(record, indent=2) + "\n")
        log(f"omnilane: {harness}: {model}" + (f" (alias {raw})" if alias else "")
            + f" from {source['file']} [{source['key']}] -> {target}")
        written.append(target)
    return 3 if failed and not written else (1 if failed else 0), written


def verify(harness: str) -> dict:
    target = host_dir() / f"{harness}.json"
    record_path = host_dir() / f"{harness}.install.json"
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        raise HostContextError(f"no host capability file for {harness}; run `omnilane native-context "
                               "--install` from a terminal") from None
    if not stat.S_ISREG(info.st_mode):
        raise HostContextError(f"{target} is not a regular file")
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise HostContextError(f"{target} is not owned by this user or is group/world-writable")
    if info.st_size > MAX_BYTES:
        raise HostContextError(f"{target} is too large")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HostContextError(f"{record_path} is missing or unreadable") from None
    if (record.get("size"), record.get("mtime_ns")) != (info.st_size, info.st_mtime_ns) \
            or record.get("sha256") != _digest(target):
        raise HostContextError(f"{target} changed after it was installed, reinstall it from a terminal")
    value = json.loads(target.read_text(encoding="utf-8"))
    if (value.get("schema_version"), value.get("harness"), value.get("source"),
            value.get("inherits_caller_runtime"), value.get("caller_identity_verified")) \
            != (1, harness, "host-config", True, False) or not value.get("current_model") \
            or value.get("vendor") != VENDORS[harness]:
        raise HostContextError(f"{target} is not a host capability file for {harness}")
    return value


def build_context(workdir: str, mode: str, environment=os.environ) -> tuple[dict, dict]:
    """Verified host file -> a standard capability context for one workdir and mode.

    Kept in memory: a read-only sandbox (Codex seatbelt) can read the host file but
    cannot write even a temp file.
    """
    if mode not in ("advise", "work"):
        raise HostContextError(f"--mode {mode} has no inherited worker")
    harness = detect_harness(environment)
    value = verify(harness)
    resolved = str(Path(workdir).expanduser().resolve())
    context = {"schema_version": 1, "harness": harness, "vendor": value["vendor"],
               "current_model": value["current_model"], "inherits_caller_runtime": True,
               "caller_identity_verified": False,
               "requirements": {"tools": [], "isolation": "shared-inherited", "lifecycle": "single-shot"},
               "capabilities": [{"model": value["current_model"], "efforts": ["unverified"],
                                 "modes": [mode], "workdirs": [resolved], "tools": [],
                                 "isolations": ["shared-inherited"], "lifecycles": ["single-shot"]}]}
    return context, value
