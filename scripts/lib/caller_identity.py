#!/usr/bin/env python3
"""Read the exact AA caller identity of the CLI this process runs under.

Walks up the process tree to the nearest vendor CLI and maps its selector onto
one scored configuration. Codex app-server always uses the host-written current
turn; other Codex launches do so only without an explicit model. The rollout is
bound to the codex direct child's initial environment, never another session.

Prints the path of a caller-context file for that identity. Exits 3 with the
reason on stderr when it cannot decide. It never guesses: missing selector
evidence, an alias, or an identity not scored exactly once is a
refusal, not a fallback.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_policy  # noqa: E402

try:
    import tomllib
except ImportError:  # Explicit-model callers still work on Python 3.9/3.10.
    tomllib = None

REPO = Path(__file__).resolve().parents[2]
MAX_DEPTH = 64
ENCODED_EFFORTS = ("xhigh", "high", "medium", "low")
Selector = tuple[str, Optional[str], Optional[str]]
Lookup = Callable[[int], Optional[tuple[int, list[str]]]]
EnvironmentLookup = Callable[[int], dict[str, str]]
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
TURN_END_EVENTS = frozenset(("task_complete", "turn_complete", "turn_aborted"))
CODEX_SANDBOX_REFUSAL = (
    "Codex sandbox (CODEX_SANDBOX=seatbelt) blocked process inspection; omnilane also "
    "needs to write ~/.omnilane and access the network, which this sandbox does not allow; "
    "rerun this command outside the sandbox (approve running outside the sandbox when Codex "
    "asks, or use a conversation with full access)"
)


class _ProcessQueryUnavailable(Exception):
    """Keep a swallowed platform process-query failure available to read_caller()."""


def _vendor(executable: str) -> str | None:
    path = Path(executable)
    name = path.name
    if name == "claude" or (path.parent.name == "versions" and path.parent.parent.name == "claude"):
        return "claude"
    if name == "codex":
        return "codex"
    if name == "grok" or (name.startswith("grok-") and path.parent.name == "downloads"):
        return "grok"
    if name == "agy":
        return "gemini"
    return None


def _flag(argv: list[str], *names: str) -> str | None:
    """The last value given for any of names, as `--name value` or `--name=value`."""
    value = None
    for index, token in enumerate(argv):
        for name in names:
            if token == name and index + 1 < len(argv):
                value = argv[index + 1]
            elif token.startswith(name + "="):
                value = token.split("=", 1)[1]
    return value


def _codex_config(argv: list[str], name: str) -> str | None:
    selected = None
    tokens = iter(argv)
    for token in tokens:
        if token == "--":
            break
        if token in ("-c", "--config"):
            override = next(tokens, "")
        elif token.startswith("--config="):
            override = token.partition("=")[2]
        elif token.startswith("-c"):
            override = token[2:].removeprefix("=")
        else:
            continue
        key, sep, value = override.partition("=")
        if sep and key.strip() == name:
            selected = value.strip()
    if selected is None:
        return None
    if tomllib is None:
        if name == "model_reasoning_effort":
            return selected.strip("'\"")
        raise ValueError("model config decoding requires Python 3.11+ (tomllib)")
    try:
        value = tomllib.loads("value = " + selected)["value"]
    except ValueError:
        value = selected  # Codex treats invalid TOML as a literal string.
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} config must be a non-empty string")
    return value


def _codex_subcommand(argv: list[str]) -> str | None:
    takes_value = {"-c", "--config", "-m", "--model", "-p", "--profile", "-C", "--cd",
                   "-s", "--sandbox", "-a", "--ask-for-approval", "-i", "--image",
                   "--enable", "--disable", "--add-dir", "--local-provider"}
    tokens = iter(argv[1:])
    for token in tokens:
        if token == "--":
            return None
        if token in takes_value:
            next(tokens, None)
        elif not token.startswith("-"):
            return token
    return None

def read_selector(argv: list[str]) -> Selector | None:
    """(vendor, model, effort) when argv launches a vendor CLI, otherwise None."""
    if not argv:
        return None
    vendor = _vendor(argv[0])
    rest = argv[1:]
    if vendor == "claude":
        return vendor, _flag(rest, "--model"), _flag(rest, "--effort")
    if vendor == "codex":
        if _codex_subcommand(argv) == "app-server":
            return vendor, None, None
        model = _flag(rest, "-m", "--model")
        if model is None:
            model = _codex_config(rest, "model")
        if model is None:
            return vendor, None, None
        return vendor, model, _codex_config(rest, "model_reasoning_effort")
    if vendor == "grok":
        return vendor, _flag(rest, "-m", "--model"), _flag(rest, "--reasoning-effort")
    if vendor == "gemini":
        return vendor, _flag(rest, "--model", "-m"), None
    return None


def resolve(registry: dict, vendor: str, model: str | None,
            effort: str | None) -> tuple[dict | None, str]:
    """The one scored row a launch selector lands on, or None and the reason."""
    if not model:
        return None, f"{vendor} was launched without a model flag"
    if vendor == "gemini" and effort is None:
        base, _, suffix = model.rpartition("-")
        if suffix in ENCODED_EFFORTS:
            model, effort = base, suffix
    if vendor == "codex":
        if effort is None:
            return None, (f"codex was launched without model_reasoning_effort; its configured "
                          f"default is not read, so the effort of {model} is unknown")
        if effort == "none":
            effort = None
    rows = [row for row in registry["scored_configs"]
            if row["vendor"] == vendor and row["model"] == model and row["effort"] == effort]
    excluded = []
    if vendor == "claude":
        # ADR-0046: --effort has no reasoning-off value, so a non-reasoning row
        # can never be what a Claude launch selected.
        excluded = [row for row in rows if row["reasoning"] == "non-reasoning"]
        rows = [row for row in rows if row["reasoning"] != "non-reasoning"]
    if len(rows) == 1:
        return rows[0], ""
    if rows:
        return None, f"{vendor} {model} at effort {effort} matches {len(rows)} scored configurations"
    if excluded:
        return None, (f"the only scored {model} row at effort {effort} is non-reasoning, which a "
                      f"Claude launch cannot select")
    if vendor == "claude" and effort is None:
        return None, f"{model} was launched without --effort and no default is scored for it"
    return None, f"no scored configuration for {vendor} {model} at effort {effort}"


def _thread_environment(entries: list[bytes]) -> dict[str, str]:
    values = [entry.partition(b"=")[2] for entry in entries
              if entry.partition(b"=")[0] == b"CODEX_THREAD_ID"]
    if len(values) > 1:
        raise ValueError("duplicate CODEX_THREAD_ID in initial environment")
    return {"CODEX_THREAD_ID": values[0].decode(errors="replace")} if values else {}


def _parse_procargs2(data: bytes) -> tuple[list[str], dict[str, str]]:
    if len(data) < 4:
        raise ValueError("truncated KERN_PROCARGS2 header")
    argc = struct.unpack_from("=i", data)[0]
    offset = data.find(b"\0", 4)
    if argc <= 0 or offset < 0:
        raise ValueError("invalid KERN_PROCARGS2 header")
    while offset < len(data) and data[offset] == 0:
        offset += 1
    argv = []
    for _ in range(argc):
        end = data.find(b"\0", offset)
        if end < 0:
            raise ValueError("truncated KERN_PROCARGS2 argv")
        argv.append(data[offset:end].decode(errors="replace"))
        offset = end + 1
    # argc, not a KEY=value search, separates argv from the initial environment.
    entries = []
    while offset < len(data) and data[offset] != 0:
        end = data.find(b"\0", offset)
        if end < 0:
            raise ValueError("truncated KERN_PROCARGS2 environment")
        entries.append(data[offset:end])
        offset = end + 1
    return argv, _thread_environment(entries)


def _darwin_procargs(pid: int) -> tuple[list[str], dict[str, str]]:
    libc = ctypes.CDLL(None, use_errno=True)
    argmax = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    if libc.sysctlbyname(b"kern.argmax", ctypes.byref(argmax), ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), "cannot read kern.argmax")
    buffer = ctypes.create_string_buffer(argmax.value)
    size = ctypes.c_size_t(len(buffer))
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2.
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), "cannot read KERN_PROCARGS2")
    return _parse_procargs2(buffer.raw[:size.value])


def _initial_environment(pid: int) -> dict[str, str]:
    if sys.platform == "darwin":
        return _darwin_procargs(pid)[1]
    return _thread_environment(Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"))


def _process(pid: int) -> tuple[int, list[str]] | None:
    """(ppid, argv) for pid. /proc keeps argv exact; ps joins it with spaces, so
    argv[0] comes from `comm`, which keeps a path like `Application Support` whole."""
    proc = Path(f"/proc/{pid}")
    if proc.is_dir():
        try:
            argv = [part.decode(errors="replace")
                    for part in (proc / "cmdline").read_bytes().split(b"\0") if part]
            ppid = int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return ppid, argv
    try:
        head_result = subprocess.run(
            ["ps", "-o", "ppid=", "-o", "comm=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5)
        args_result = subprocess.run(
            ["ps", "-o", "args=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        raise _ProcessQueryUnavailable from None
    head = head_result.stdout.strip()
    args = args_result.stdout.strip()
    parts = head.split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    comm = parts[1].strip()
    if sys.platform == "darwin" and _vendor(comm) == "codex":
        argv, _ = _darwin_procargs(pid)
        return int(parts[0]), argv
    rest = args[len(comm):] if args.startswith(comm) else args.partition(" ")[2]
    return int(parts[0]), [comm, *rest.split()]


def _launcher(pid: int, lookup: Lookup) -> tuple[int, Selector, int | None] | None:
    """The nearest process at or above pid that is a vendor CLI, with its selector.

    Nearest wins: a codex worker started by a Claude session is a codex caller.
    """
    seen: set[int] = set()
    child = None
    for _ in range(MAX_DEPTH):
        if pid <= 0 or pid in seen:
            return None
        seen.add(pid)
        entry = lookup(pid)
        if entry is None:
            return None
        ppid, argv = entry
        selector = read_selector(argv)
        if selector is not None:
            return pid, selector, child
        child = pid
        pid = ppid
    return None


def find_launcher(pid: int, lookup: Lookup = _process) -> tuple[int, Selector] | None:
    try:
        found = _launcher(pid, lookup)
    except _ProcessQueryUnavailable:
        return None
    return found[:2] if found else None


def _rollout_age_hint(timestamp: object, now: Callable[[], datetime] | None) -> str:
    """Describe the last record without letting unusable time data change a refusal."""
    if not isinstance(timestamp, str):
        return ""
    try:
        recorded = datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
        current = now() if now is not None else datetime.now(timezone.utc)
        if recorded.tzinfo is None or current.tzinfo is None:
            return ""
        seconds = (current - recorded).total_seconds()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return ""
    if seconds < 0:
        return ""
    # Floor display units; compare the unrounded duration against the hint threshold.
    if seconds < 3600:
        age = f"{int(seconds // 60)}m"
    elif seconds < 86400:
        age = f"{int(seconds // 3600)}h"
    else:
        age = f"{int(seconds // 86400)}d"
    hint = f"; that rollout's last record is {timestamp}, {age} before now"
    if seconds > 600:
        hint += ", so CODEX_THREAD_ID may name an earlier conversation than the one running this command"
    return hint


def _rollout_candidates(home: Path, thread: str) -> list[Path]:
    """Resuming a thread opens a second rollout named <thread>_<session>, so match both forms."""
    sessions = home / "sessions"
    found = set(sessions.glob(f"*/*/*/rollout-*-{thread}.jsonl"))
    found |= set(sessions.glob(f"*/*/*/rollout-*-{thread}_*.jsonl"))
    return sorted(found, key=lambda path: (path.stat().st_mtime, path.name))


def _rollout_selector(thread: str,
                      current_environment: Mapping[str, str] = os.environ,
                      now: Callable[[], datetime] | None = None) -> tuple[Selector, str]:
    home = Path(current_environment.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        paths = _rollout_candidates(home, thread)
    except OSError as error:
        raise ValueError(f"cannot read rollout (errno {error.errno})") from None
    if not paths:
        raise ValueError("expected an active rollout for this thread; found 0")
    # The most recently written file is the live one; older files belong to earlier sessions.
    path = paths[-1]
    metadata = 0
    latest = None
    ended = None
    last_timestamp = None
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("invalid rollout record")
                last_timestamp = record.get("timestamp")
                kind = record.get("type")
                if kind not in ("session_meta", "turn_context", "event_msg"):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    raise ValueError("invalid rollout payload")
                if kind == "session_meta":
                    metadata += 1
                    if payload.get("id") != thread:
                        raise ValueError("session_meta id does not match CODEX_THREAD_ID")
                elif kind == "turn_context":
                    latest = {key: payload.get(key) for key in ("turn_id", "model", "effort")}
                    ended = None
                elif kind == "event_msg":
                    event = payload.get("type")
                    if not isinstance(event, str):
                        raise ValueError("invalid rollout event type")
                    if latest is not None and event in TURN_END_EVENTS:
                        event_turn = payload.get("turn_id")
                        if not isinstance(event_turn, str) or not event_turn.strip():
                            event_turn = None
                        context_turn = latest["turn_id"]
                        if not isinstance(context_turn, str) or not context_turn.strip():
                            context_turn = None
                        if event_turn is None or context_turn is None or event_turn == context_turn:
                            ended = (event, context_turn or "unknown", event_turn or "unknown")
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("rollout contains incomplete or invalid JSON") from None
    except OSError as error:
        raise ValueError(f"cannot read rollout (errno {error.errno})") from None
    if metadata != 1:
        raise ValueError("expected exactly one matching session_meta")
    if latest is None:
        raise ValueError("rollout has no turn_context")
    if ended:
        event, context_turn, event_turn = ended
        raise ValueError(f"latest turn_context turn {context_turn} has already ended "
                         f"({event}, turn {event_turn})" + _rollout_age_hint(last_timestamp, now))
    for key, value in latest.items():
        if not isinstance(value, str) or not value.strip():
            detail = " (turn unknown)" if key == "turn_id" else ""
            raise ValueError(f"latest turn_context {key} must be a non-empty string{detail}")
    return ("codex", latest["model"], latest["effort"]), f"thread {thread}, turn {latest['turn_id']}"


def read_caller(pid: int, lookup: Lookup = _process,
                environment: EnvironmentLookup = _initial_environment,
                current_environment: Mapping[str, str] = os.environ,
                now: Callable[[], datetime] | None = None) -> tuple[int, Selector, str]:
    if current_environment.get("OMNILANE_AA_CALLER_FROM_PROCESS") == "0":
        raise ValueError("caller identity from process is disabled")
    try:
        found = _launcher(pid, lookup)
    except _ProcessQueryUnavailable:
        if current_environment.get("CODEX_SANDBOX") == "seatbelt":
            raise ValueError(CODEX_SANDBOX_REFUSAL) from None
        found = None
    except OSError as error:
        if current_environment.get("CODEX_SANDBOX") == "seatbelt":
            raise ValueError(CODEX_SANDBOX_REFUSAL) from None
        raise ValueError(f"cannot query caller process (errno {error.errno})") from None
    if found is None:
        if current_environment.get("CODEX_SANDBOX") == "seatbelt":
            raise ValueError(CODEX_SANDBOX_REFUSAL)
        raise ValueError("no vendor CLI among this process's ancestors; a model caller passes "
                         "--caller-context FILE and a human operator --operator-asserted-human")
    launcher, selector, child = found
    if selector[0] != "codex" or selector[1] is not None:
        return launcher, selector, ""
    thread = current_environment.get("CODEX_THREAD_ID")
    if not thread or not UUID_PATTERN.fullmatch(thread):
        raise ValueError("current process CODEX_THREAD_ID is missing or not a UUID")
    if child is None:
        raise ValueError("cannot find the codex direct child in the ancestor chain")
    try:
        inherited = environment(child).get("CODEX_THREAD_ID")
    except (OSError, ValueError) as error:
        detail = f"errno {error.errno}" if isinstance(error, OSError) else "invalid environment block"
        raise ValueError(f"cannot read codex direct child initial environment ({detail})") from None
    if not inherited or not UUID_PATTERN.fullmatch(inherited):
        raise ValueError("codex direct child CODEX_THREAD_ID is missing or not a UUID")
    if inherited != thread:
        raise ValueError("CODEX_THREAD_ID mismatch between current process and codex direct child")
    selector, source = _rollout_selector(thread, current_environment, now)
    return launcher, selector, source


def load_registry(path: str | Path) -> tuple[dict, str]:
    """The approved registry without the transport overlay, which says nothing
    about the caller and must not stop one from learning who it is."""
    saved = {key: os.environ.pop(key)
             for key in ("OMNILANE_AA_TRANSPORT_OVERLAY", "OMNILANE_AA_OVERLAY_SHA256")
             if key in os.environ}
    try:
        return aa_policy.load_registry(path)
    finally:
        os.environ.update(saved)


def write_context(row: dict, registry: dict, home: Path) -> Path:
    """One file per identity: every session launched the same way is the same caller."""
    directory = Path(home) / "caller-context"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (row["id"].replace("/", "--") + ".json")
    text = json.dumps({
        "schema_version": 1,
        "snapshot_id": registry["snapshot"]["id"],
        "kind": "model",
        "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
        "inherited_ceiling": row["score"],
    }, indent=2, sort_keys=True) + "\n"
    try:
        if path.read_text() == text:
            return path
    except OSError:
        pass
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(text)
    os.replace(staging, path)
    return path


def main(argv: list[str] | None = None, environment: Mapping[str, str] = os.environ,
         lookup: Lookup = _process,
         process_environment: EnvironmentLookup = _initial_environment) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry",
                        default=os.environ.get("OMNILANE_AA_POLICY_FILE")
                        or str(REPO / "config" / "aa-model-policy.json"),
                        help="frozen AA registry (default: the repository copy)")
    args = parser.parse_args(argv)
    try:
        registry, _ = load_registry(args.registry)
    except (aa_policy.PolicyError, OSError, ValueError) as error:
        print(f"omnilane: cannot read the AA registry: {error}", file=sys.stderr)
        return 3
    try:
        pid, (vendor, model, effort), source = read_caller(
            os.getpid(), lookup, process_environment, environment)
    except ValueError as error:
        print(f"omnilane: cannot read the caller identity: {error}", file=sys.stderr)
        return 3
    row, reason = resolve(registry, vendor, model, effort)
    if row is None:
        print(f"omnilane: cannot read the caller identity from pid {pid}: {reason}", file=sys.stderr)
        return 3
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
    path = write_context(row, registry, home)
    provenance = f", {source}" if source else ""
    print(f"omnilane: caller is {row['id']} (score {row['score']}), read from pid {pid}{provenance}",
          file=sys.stderr)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
