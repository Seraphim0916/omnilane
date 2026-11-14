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
import sqlite3
import stat
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_policy  # noqa: E402
import json_depth  # noqa: E402

try:
    import tomllib
except ImportError:  # Explicit-model callers still work on Python 3.9/3.10.
    tomllib = None

REPO = Path(__file__).resolve().parents[2]
MAX_DEPTH = 64
ENCODED_EFFORTS = ("xhigh", "high", "medium", "low")
_CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_CLAUDE_TAIL_BYTES = 4 * 1024 * 1024
Selector = tuple[str, Optional[str], Optional[str]]
Lookup = Callable[[int], Optional[tuple[int, list[str]]]]
EnvironmentLookup = Callable[[int], dict[str, str]]
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
CODEX_TOOL_HOSTS = frozenset(("codex-code-mode-host",))
ANTIGRAVITY_LANGUAGE_SERVER = "/Applications/Antigravity.app/Contents/Resources/bin/language_server"
ANTIGRAVITY_APP_EXECUTABLE = "/Applications/Antigravity.app/Contents/MacOS/Antigravity"
_TRACKED_ENV_KEYS = (b"CODEX_THREAD_ID", b"ANTIGRAVITY_CONVERSATION_ID")
SHELLS = frozenset(("zsh", "bash", "sh", "dash", "fish"))
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
    # codex-profile-switch launches the desktop app-server as `codex-modified`; its sibling
    # `codex-code-mode-host` is a tool host, not the CLI, so the match stays exact.
    if name in ("codex", "codex-modified"):
        return "codex"
    if name == "grok" or (name.startswith("grok-") and path.parent.name == "downloads"):
        return "grok"
    if name == "agy":
        return "gemini"
    return None


def _flag(argv: list[str], *names: str) -> str | None:
    """The last flag value before `--`, as `--name value` or `--name=value`."""
    value = None
    for index, token in enumerate(argv):
        if token == "--":
            break
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


NO_ROW = "no scored configuration for"


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
        if not aa_policy.authority_eligible(rows[0]):
            return None, "derived or authority-ineligible rows cannot supply caller authority"
        return rows[0], ""
    if rows:
        return None, f"{vendor} {model} at effort {effort} matches {len(rows)} scored configurations"
    if excluded:
        return None, (f"the only scored {model} row at effort {effort} is non-reasoning, which a "
                      f"Claude launch cannot select")
    if vendor == "claude" and effort is None:
        return None, f"{model} was launched without --effort and no default is scored for it"
    snapshot = registry.get("snapshot", {}).get("id", "unknown snapshot")
    return None, (f"{NO_ROW} {vendor} {model} at effort {effort}: the score table ({snapshot}) "
                  f"has no row for this model or this effort, and a family match is not accepted")


def is_authority_eligible_row(row: dict) -> bool:
    """True when a registry row is measured and eligible to grant caller authority."""
    policy_check = getattr(aa_policy, "authority_eligible", None)
    if callable(policy_check) and not policy_check(row):
        return False
    return (
        row.get("authority_eligible") is not False
        and not row.get("score_source")
        and not row.get("estimated")
    )


def floor_row(registry: dict, vendor: str, model: str | None) -> dict | None:
    """The lowest-scored row of a known model, for a caller whose effort is unrecorded.

    Whatever effort actually ran scores at least this much, so the floor can only
    narrow what the caller may dispatch; it never widens it.
    """
    rows = [row for row in registry["scored_configs"]
            if row["vendor"] == vendor and row["model"] == model
            and aa_policy.authority_eligible(row)]
    return min(rows, key=lambda row: (row["score"], row["id"])) if rows else None


def _thread_environment(entries: list[bytes]) -> dict[str, str]:
    """Extract only whitelisted conversation/thread IDs from raw environment entries.

    Security boundary: neither macOS (sysctl KERN_PROCARGS2) nor Linux
    (/proc/<pid>/environ) offers a kernel syscall to fetch a single environment
    key from another process. The raw initial-environment buffer therefore enters
    this process's local memory briefly and is filtered immediately in memory to
    _TRACKED_ENV_KEYS only; unrelated variables (tokens, CSRF secrets, addresses)
    are never decoded, retained, persisted, printed, or interpolated into errors.
    """
    result: dict[str, str] = {}
    for key in _TRACKED_ENV_KEYS:
        values = [entry.partition(b"=")[2] for entry in entries
                  if entry.partition(b"=")[0] == key]
        if len(values) > 1:
            raise ValueError(f"duplicate {key.decode()} in initial environment")
        if values:
            result[key.decode()] = values[0].decode(errors="replace")
    return result


def _parse_procargs2(data: bytes) -> tuple[list[str], dict[str, str]]:
    """Parse Darwin KERN_PROCARGS2 in local memory and retain only _TRACKED_ENV_KEYS."""
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
    """Read KERN_PROCARGS2 into transient process memory (OS provides no single-key query)."""
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
    """Return only _TRACKED_ENV_KEYS from pid's initial environment (filtered in memory)."""
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


def _is_antigravity_desktop(pid: int, ppid: int, argv: list[str], lookup: Lookup, seen: set[int]) -> bool:
    """Match Antigravity's language_server only when its direct parent is Antigravity.app."""
    if not argv or os.path.realpath(argv[0]) != ANTIGRAVITY_LANGUAGE_SERVER:
        return False
    if ppid <= 0 or ppid in seen:
        return False
    parent_entry = lookup(ppid)
    if parent_entry is None or not parent_entry[1]:
        return False
    return os.path.realpath(parent_entry[1][0]) == ANTIGRAVITY_APP_EXECUTABLE


def _launcher(pid: int, lookup: Lookup) -> tuple[int, Selector, int | None] | None:
    """The nearest process at or above pid that is a vendor CLI, with its selector.

    Nearest wins: a codex worker started by a Claude session is a codex caller.
    """
    seen: set[int] = set()
    below: list[tuple[int, list[str]]] = []
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
            return pid, selector, _thread_anchor(below)
        if _is_antigravity_desktop(pid, ppid, argv, lookup, seen):
            return pid, ("gemini", None, None), (below[-1][0] if below else None)
        below.append((pid, argv))
        pid = ppid
    return None


def _thread_anchor(below: list[tuple[int, list[str]]]) -> int | None:
    """The process whose initial environment codex wrote for this conversation.

    That is codex's direct child, unless codex runs commands through its own tool
    host: one host serves every conversation of an app-server, so it carries no
    thread id and the process it starts for the command does.
    """
    for pid, argv in reversed(below):
        if not (argv and Path(argv[0]).name in CODEX_TOOL_HOSTS):
            return pid
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
                record = json_depth.loads(line)
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
    except (json.JSONDecodeError, UnicodeError, json_depth.JSONDepthError):
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
    for key in ("turn_id", "model"):
        value = latest[key]
        if not isinstance(value, str) or not value.strip():
            detail = " (turn unknown)" if key == "turn_id" else ""
            raise ValueError(f"latest turn_context {key} must be a non-empty string{detail}")
    effort = latest["effort"]
    source = f"thread {thread}, turn {latest['turn_id']}"
    if effort is None:
        # Heartbeat automations wake a thread without recording an effort. The
        # model is still known, so the caller degrades instead of going blind.
        return ("codex", latest["model"], None), source + ", turn_context records no effort"
    if not isinstance(effort, str) or not effort.strip():
        raise ValueError("latest turn_context effort must be a non-empty string")
    return ("codex", latest["model"], effort), source


def _claude_process_start(pid: int) -> str:
    """Read ps's C-locale start time in UTC, never the invoking shell's timezone."""
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        check=True, timeout=5,
    )
    return result.stdout.strip()


def _claude_start_second(value: str | datetime | None) -> datetime | None:
    """Session procStart and the injectable live start share UTC second precision."""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value.strip(), "%a %b %d %H:%M:%S %Y")
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


class _IncompleteClaudeIdentity(ValueError):
    """A bound current transcript cannot safely reuse the launch identity."""


def _claude_tail_selector(path: Path) -> tuple[Selector | None, str]:
    """Read at most 4 MiB; inspect identity fields only and never return content."""
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        offset = max(0, size - _CLAUDE_TAIL_BYTES)
        stream.seek(offset)
        tail = stream.read(_CLAUDE_TAIL_BYTES)
    if offset:
        # The first line may be incomplete. Do not interpret a truncated record.
        tail = tail.partition(b"\n")[2]
    unverified = None
    for line in reversed(tail.splitlines()):
        try:
            record = json_depth.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        if record.get("isSidechain") is True:
            continue
        message = record.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if unverified is not None:
            # A known model's floor is not necessarily below another model's
            # last proven ceiling. Refuse that ambiguous model-switch case.
            previous_efforts = [record.get(key) for key in ("perTurnEffort", "effort")
                                if isinstance(record.get(key), str) and record[key]]
            if (not isinstance(model, str) or not model or not previous_efforts
                    or any(value not in _CLAUDE_EFFORTS for value in previous_efforts)):
                continue
            if re.sub(r"-[0-9]{8}$", "", model) != unverified[1]:
                raise _IncompleteClaudeIdentity("latest assistant effort missing after model switch")
            return unverified, "latest assistant effort missing"
        if not isinstance(model, str) or not model:
            raise _IncompleteClaudeIdentity("latest assistant model missing")
        efforts = [record.get(key) for key in ("perTurnEffort", "effort")
                   if isinstance(record.get(key), str) and record[key]]
        if not efforts:
            # Missing identity on the latest main record must not resurrect a
            # previous turn's model or ceiling after a mid-session switch.
            unverified = ("claude", re.sub(r"-[0-9]{8}$", "", model), None)
            continue
        if any(effort not in _CLAUDE_EFFORTS for effort in efforts):
            raise _IncompleteClaudeIdentity("latest assistant effort unrecognized")
        effort = min(efforts, key=_CLAUDE_EFFORTS.index)
        return ("claude", re.sub(r"-[0-9]{8}$", "", model), effort), ""
    if unverified is not None:
        return unverified, "latest assistant effort missing"
    return None, "no qualifying assistant in transcript tail"


def _claude_transcript_overlay(
    launcher: int, selector: Selector, environment: Mapping[str, str],
    claude_dir: str | Path | None,
    start_time: Callable[[int], str | datetime | None] | None,
) -> tuple[Selector, str]:
    """Bind a main-thread transcript to the nearest live Claude process."""
    def fallback(reason: str) -> tuple[Selector, str]:
        # Keep the pre-overlay contract: callers treat "" as "launch flags as read".
        # reason only labels each refusal branch for readers.
        return selector, ""

    if environment.get("OMNILANE_AA_CLAUDE_TRANSCRIPT") == "0":
        return fallback("disabled")
    try:
        home = Path(claude_dir) if claude_dir is not None else (
            Path(environment["CLAUDE_CONFIG_DIR"])
            if environment.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude"
        )
        try:
            session = json_depth.loads((home / "sessions" / f"{launcher}.json").read_text(
                encoding="utf-8"))
        except FileNotFoundError:
            return fallback("sessions file missing")
        if not isinstance(session, dict):
            return fallback("invalid sessions file")
        if type(session.get("pid")) is not int or session["pid"] != launcher:
            return fallback("sessions pid mismatch")
        recorded = _claude_start_second(session.get("procStart"))
        live = _claude_start_second((start_time or _claude_process_start)(launcher))
        if recorded is None or live is None:
            return fallback("process start time unavailable")
        if recorded != live:
            return fallback("process start time mismatch")
        sid = session.get("sessionId")
        if not isinstance(sid, str) or not UUID_PATTERN.fullmatch(sid):
            return fallback("invalid session id")
        if ("CLAUDE_CODE_SESSION_ID" in environment
                and environment["CLAUDE_CODE_SESSION_ID"] != sid):
            return fallback("environment session id mismatch")
        matches = (home / "projects").glob(f"*/{sid}.jsonl")
        path = next(matches, None)
        if path is None or next(matches, None) is not None:
            return fallback("transcript match not unique")
        current, reason = _claude_tail_selector(path)
        if current is None:
            return fallback(reason)
        prefix = f"transcript {sid[:8]}: "
        if current == selector:
            return selector, prefix + "matches launch flags"
        return current, (prefix + f"{current[1]} at {current[2]} "
                         f"(launch flags said {selector[1]} at {selector[2] or 'none'})")
    except _IncompleteClaudeIdentity:
        raise
    except (OSError, ValueError, TypeError, OverflowError, RecursionError,
            subprocess.SubprocessError):
        # Do not interpolate exception text: paths or decoded records may be private.
        return fallback("transcript metadata or file unreadable")


INELIGIBLE_AUTHORITY_REASON = "derived or authority-ineligible rows cannot supply caller authority"
UNRECOGNIZED_ANTIGRAVITY_MODEL = "Antigravity model selector at 10.1.28 names an unrecognized model"
_PROTO_MAX_FIELD_NUMBER = (1 << 29) - 1
_ANTIGRAVITY_METADATA_MAX_BYTES = 262_144
_ANTIGRAVITY_MODEL_SELECTOR_MAX_BYTES = 128
_ANTIGRAVITY_GEMINI_MODEL_PATTERN = re.compile(r"gemini-[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")


def known_antigravity_gemini_models(registry: dict) -> set[str]:
    """Return the set of known Gemini model IDs registered in the local policy table."""
    known: set[str] = set()
    for section in ("scored_configs", "unknown_configs", "reference_configs"):
        entries = registry.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("vendor") != "gemini":
                continue
            model = entry.get("model")
            if isinstance(model, str) and model.startswith("gemini-"):
                known.add(model)
    return known


def validate_antigravity_caller(
    registry: dict, vendor: str, model: str | None, source: object
) -> None:
    """Reject any Antigravity Desktop model not in the local policy Gemini whitelist."""
    if isinstance(source, str) and source.startswith("antigravity-desktop"):
        if vendor != "gemini" or not model or model not in known_antigravity_gemini_models(registry):
            raise ValueError(UNRECOGNIZED_ANTIGRAVITY_MODEL)
_ANTIGRAVITY_METADATA_QUERY = """
SELECT
    idx,
    typeof(idx),
    typeof(data),
    length(data),
    CASE
        WHEN typeof(idx) = 'integer'
             AND typeof(data) = 'blob'
             AND length(data) <= ?
        THEN data
        ELSE NULL
    END,
    EXISTS(
        SELECT 1 FROM executor_metadata
        WHERE idx IS NULL OR typeof(idx) != 'integer'
    )
FROM executor_metadata
ORDER BY idx DESC
LIMIT 2
"""


def _default_antigravity_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity"


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode an unsigned 64-bit protobuf varint, rejecting >10 bytes or >uint64."""
    value = 0
    shift = 0
    for count in range(10):
        if offset >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        if count == 9 and byte > 0x01:
            raise ValueError("protobuf varint exceeds 64 bits")
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset
        shift += 7
    raise ValueError("protobuf varint exceeds 64 bits")


def _iter_proto_fields(data: bytes):
    offset = 0
    end = len(data)
    while offset < end:
        tag, offset = _read_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if field_number == 0 or field_number > _PROTO_MAX_FIELD_NUMBER:
            raise ValueError("invalid protobuf field number")
        if wire_type == 0:
            _, offset = _read_varint(data, offset)
            yield field_number, wire_type, b""
        elif wire_type == 1:
            if offset + 8 > end:
                raise ValueError("truncated protobuf 64-bit field")
            offset += 8
            yield field_number, wire_type, b""
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            if length < 0 or offset + length > end:
                raise ValueError("truncated protobuf length-delimited field")
            payload = data[offset : offset + length]
            offset += length
            yield field_number, wire_type, payload
        elif wire_type == 5:
            if offset + 4 > end:
                raise ValueError("truncated protobuf 32-bit field")
            offset += 4
            yield field_number, wire_type, b""
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")


def _proto_path_values(data: bytes, path: tuple[int, ...]) -> list[bytes]:
    if not path:
        return [data]
    target = path[0]
    rest = path[1:]
    matches: list[bytes] = []
    for field_number, wire_type, payload in _iter_proto_fields(data):
        if field_number != target:
            continue
        if wire_type != 2:
            raise ValueError(
                f"protobuf field {field_number} is wire type {wire_type}, expected length-delimited"
            )
        matches.extend(_proto_path_values(payload, rest))
    return matches


def _read_antigravity_conversation_db(
    conv_id: str, agy_dir: Path, allowed_models: set[str] | None = None
) -> tuple[Selector, str]:
    """Read the latest executor_metadata protobuf path 10.1.28 from <conv_id>.db.

    Security and ordering boundaries:
    - SQL checks `length(data) <= _ANTIGRAVITY_METADATA_MAX_BYTES` before projecting
      `data`, so oversized BLOBs return NULL in SQL without being materialized in
      Python memory and are rejected fail-closed. Bounded `executor_metadata.data`
      bytes are parsed in local process memory to extract only protobuf path `10.1.28`;
      other fields and untrusted field values are never persisted, printed, or
      interpolated into error messages, and `10.1.28` model IDs must match the known
      Gemini model whitelist in the local AA policy registry.
    - Ordering uses `ORDER BY idx DESC LIMIT 2` and rejects `idx IS NULL`,
      non-integer `idx`, or duplicate maximum `idx` values fail-closed. Selecting the
      unique maximum `idx` assumes the conversation database's latest persisted
      `executor_metadata` row corresponds to the active turn; concurrent turns in the
      same conversation or an unpersisted mid-session model switch before DB flush are
      known limitations rather than a kernel-enforced per-turn binding.
    """
    uid = os.getuid()
    conv_dir = agy_dir / "conversations"
    try:
        dir_st = os.lstat(conv_dir)
    except OSError:
        raise ValueError("Antigravity conversation database directory is missing or unreadable") from None
    if stat.S_ISLNK(dir_st.st_mode) or not stat.S_ISDIR(dir_st.st_mode):
        raise ValueError("Antigravity conversation database directory must be a real directory")
    if dir_st.st_uid != uid:
        raise ValueError("Antigravity conversation database directory owner mismatch")

    db_path = conv_dir / f"{conv_id}.db"
    try:
        db_st = os.lstat(db_path)
    except FileNotFoundError:
        raise ValueError("Antigravity conversation database is missing") from None
    except OSError:
        raise ValueError("Antigravity conversation database is unreadable") from None
    if stat.S_ISLNK(db_st.st_mode):
        raise ValueError("Antigravity conversation database must not be a symlink")
    if not stat.S_ISREG(db_st.st_mode):
        raise ValueError("Antigravity conversation database is not a regular file")
    if db_st.st_uid != uid:
        raise ValueError("Antigravity conversation database owner mismatch")

    conn = None
    try:
        conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True, timeout=2.0)
        rows = conn.execute(_ANTIGRAVITY_METADATA_QUERY, (_ANTIGRAVITY_METADATA_MAX_BYTES,)).fetchall()
    except sqlite3.Error:
        raise ValueError("cannot read Antigravity conversation database (sqlite error)") from None
    finally:
        if conn is not None:
            conn.close()

    if not rows:
        raise ValueError("Antigravity executor_metadata is empty")

    top_idx, idx_type, data_type, data_len, blob, has_invalid_idx = rows[0]
    if has_invalid_idx or idx_type != "integer" or type(top_idx) is not int:
        raise ValueError("Antigravity executor_metadata contains a null or non-integer idx")
    if len(rows) > 1 and rows[0][0] == rows[1][0]:
        raise ValueError("Antigravity executor_metadata has duplicate maximum idx")
    if data_type != "blob":
        raise ValueError("Antigravity executor_metadata latest row is not a blob")
    if type(data_len) is not int or data_len > _ANTIGRAVITY_METADATA_MAX_BYTES:
        raise ValueError(
            f"Antigravity executor_metadata exceeds {_ANTIGRAVITY_METADATA_MAX_BYTES} bytes"
        )
    if not isinstance(blob, bytes) or len(blob) > _ANTIGRAVITY_METADATA_MAX_BYTES:
        raise ValueError("Antigravity executor_metadata is empty")

    try:
        matches = _proto_path_values(blob, (10, 1, 28))
    except ValueError as error:
        raise ValueError(f"Antigravity executor_metadata protobuf is malformed ({error})") from None
    if not matches:
        raise ValueError("Antigravity executor_metadata has no model field at protobuf path 10.1.28")
    if len(matches) != 1:
        raise ValueError(
            f"Antigravity executor_metadata has {len(matches)} values at protobuf path 10.1.28; expected 1"
        )
    if len(matches[0]) > _ANTIGRAVITY_MODEL_SELECTOR_MAX_BYTES:
        raise ValueError("Antigravity model field at 10.1.28 is empty or malformed")
    try:
        raw = matches[0].decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Antigravity model field at 10.1.28 is not valid UTF-8") from None
    if not raw or raw != raw.strip():
        raise ValueError("Antigravity model field at 10.1.28 is empty or malformed")

    if raw.startswith(("claude-", "gpt-", "o1-", "o3-", "o4-")):
        raise ValueError("Antigravity 的非 Gemini 模型尚未支援")
    base, sep, suffix = raw.rpartition("-")
    if not sep or not base or suffix not in ENCODED_EFFORTS:
        raise ValueError("Antigravity model selector at 10.1.28 must be <model>-<effort>")
    if not base.startswith("gemini-"):
        raise ValueError("Antigravity 的非 Gemini 模型尚未支援")
    if not _ANTIGRAVITY_GEMINI_MODEL_PATTERN.fullmatch(base):
        raise ValueError("Antigravity model selector at 10.1.28 is malformed")
    if allowed_models is not None and base not in allowed_models:
        raise ValueError(UNRECOGNIZED_ANTIGRAVITY_MODEL)
    return ("gemini", base, suffix), f"antigravity-desktop conversation {conv_id}"


def _read_antigravity_caller(
    pid: int,
    child: int | None,
    lookup: Lookup,
    environment: EnvironmentLookup,
    current_environment: Mapping[str, str],
    antigravity_dir: str | Path | None,
    registry: dict | None = None,
) -> tuple[Selector, str]:
    agy_dir = Path(antigravity_dir) if antigravity_dir is not None else _default_antigravity_dir()
    if lookup is not _process and antigravity_dir is None and agy_dir == Path.home() / ".gemini" / "antigravity":
        raise ValueError("fake process table cannot read real ~/.gemini without antigravity_dir")
    conv_id = current_environment.get("ANTIGRAVITY_CONVERSATION_ID")
    if not conv_id or not UUID_PATTERN.fullmatch(conv_id):
        raise ValueError("current process ANTIGRAVITY_CONVERSATION_ID is missing or not a UUID")
    if child is None:
        raise ValueError("cannot find the Antigravity direct child in the ancestor chain")
    try:
        inherited = environment(child).get("ANTIGRAVITY_CONVERSATION_ID")
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and "duplicate ANTIGRAVITY_CONVERSATION_ID" in str(error):
            raise
        detail = f"errno {error.errno}" if isinstance(error, OSError) else "invalid environment block"
        raise ValueError(f"cannot read Antigravity direct child initial environment ({detail})") from None
    if not inherited or not UUID_PATTERN.fullmatch(inherited):
        entry = lookup(child)
        raw_name = Path(entry[1][0]).name if entry and entry[1] else "unknown"
        name = raw_name if re.fullmatch(r"-?[A-Za-z0-9._+-]{1,64}\Z", raw_name) else "process"
        hint = ""
        if name.lstrip("-") in SHELLS:
            hint = (
                "; run the omnilane command as the only command of the tool call, "
                "with no `;`, `&&`, `|`, subshell or VAR=value shell prefix around it "
                "(use env VAR=value if needed)"
            )
        raise ValueError(
            f"Antigravity direct child ANTIGRAVITY_CONVERSATION_ID is missing or not a UUID "
            f"(read from pid {child}, {name}){hint}"
        )
    if inherited != conv_id:
        raise ValueError(
            "ANTIGRAVITY_CONVERSATION_ID mismatch between current process and Antigravity direct child"
        )
    # Also verify no intermediate process between pid and child carries a conflicting conversation id.
    cur = pid
    seen: set[int] = set()
    for _ in range(MAX_DEPTH):
        if cur <= 0 or cur in seen or cur == child:
            break
        seen.add(cur)
        try:
            env_map = environment(cur)
        except OSError:
            env_map = {}
        other = env_map.get("ANTIGRAVITY_CONVERSATION_ID")
        if other is not None and other != conv_id:
            raise ValueError("ANTIGRAVITY_CONVERSATION_ID mismatch in Antigravity process chain")
        entry = lookup(cur)
        if entry is None:
            break
        cur = entry[0]

    if registry is None:
        policy_path = current_environment.get("OMNILANE_AA_POLICY_FILE") or str(
            REPO / "config" / "aa-model-policy.json"
        )
        try:
            effective_registry, _ = load_registry(policy_path)
        except (aa_policy.PolicyError, OSError, ValueError):
            raise ValueError("cannot verify Antigravity model against AA registry") from None
    else:
        effective_registry = registry
    allowed_models = known_antigravity_gemini_models(effective_registry)
    return _read_antigravity_conversation_db(conv_id, agy_dir, allowed_models)


def read_caller(pid: int, lookup: Lookup = _process,
                environment: EnvironmentLookup = _initial_environment,
                current_environment: Mapping[str, str] = os.environ,
                now: Callable[[], datetime] | None = None, *,
                claude_dir: str | Path | None = None,
                start_time: Callable[[int], str | datetime | None] | None = None,
                antigravity_dir: str | Path | None = None,
                registry: dict | None = None) -> tuple[int, Selector, str]:
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
    if selector[0] == "claude":
        # A fake process table carries fake pids; never pair them with the real
        # ~/.claude/sessions or the real ps unless the test injects both seams.
        if lookup is not _process and (claude_dir is None or start_time is None):
            return launcher, selector, ""
        current, source = _claude_transcript_overlay(
            launcher, selector, current_environment, claude_dir, start_time)
        return launcher, current, source
    if selector[0] == "gemini" and selector[1] is None:
        current, source = _read_antigravity_caller(
            pid, child, lookup, environment, current_environment, antigravity_dir, registry
        )
        return launcher, current, source
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
        entry = lookup(child)
        name = Path(entry[1][0]).name if entry and entry[1] else "unknown"
        hint = ""
        if name.lstrip("-") in SHELLS:
            # codex starts `zsh -lc '<command>'`; the shell sets the thread id and, for one
            # simple command, execs into it, so that command is the direct child. With
            # `;`, `&&`, `|` or a subshell the shell stays, and its own start had no id.
            hint = ("; run the omnilane command as the only command of the tool call, "
                    "with no `;`, `&&`, `|` or subshell around it")
        raise ValueError(f"codex direct child CODEX_THREAD_ID is missing or not a UUID "
                         f"(read from pid {child}, {name}){hint}")
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


def write_context(row: dict, registry: dict, home: Path, effort_unverified: bool = False) -> Path:
    """One file per identity: every session launched the same way is the same caller."""
    if not aa_policy.authority_eligible(row):
        raise ValueError("derived or authority-ineligible rows cannot supply caller authority")
    directory = Path(home) / "caller-context"
    directory.mkdir(parents=True, exist_ok=True)
    stem = row["id"].replace("/", "--")
    if effort_unverified:
        # Not the same caller as one that really launched at the floor effort.
        stem = f"{row['vendor']}--{row['model'].replace('.', '-')}--effort-unverified"
    path = directory / (stem + ".json")
    value = {
        "schema_version": 1,
        "snapshot_id": registry["snapshot"]["id"],
        "kind": "model",
        "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
        "inherited_ceiling": row["score"],
    }
    if effort_unverified:
        value["effort_unverified"] = True
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        if path.read_text() == text:
            return path
    except OSError:
        pass
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(text)
    os.replace(staging, path)
    return path


def public_caller_error(error: BaseException) -> str:
    """Format a caller-identity error for CLI stderr without echoing untrusted values."""
    if isinstance(error, OSError):
        if getattr(error, "errno", None) is not None:
            return f"cannot read caller identity input (errno {error.errno})"
        return "cannot read caller identity input"
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(error)).split())
    if "Antigravity" in text and ("got '" in text or 'got "' in text or "尚未支援:" in text):
        if "尚未支援" in text:
            return "Antigravity 的非 Gemini 模型尚未支援"
        return "Antigravity model selector at 10.1.28 is malformed"
    return text


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
            os.getpid(), lookup, process_environment, environment, registry=registry)
        validate_antigravity_caller(registry, vendor, model, source)
    except ValueError as error:
        from refusal_evidence import caller_guidance, print_human
        diagnostic = public_caller_error(error)
        print_human(dict(failed_gate="caller-identity", **caller_guidance(diagnostic)))
        print(f"omnilane: cannot read the caller identity: {diagnostic}", file=sys.stderr)
        return 3
    row, reason = resolve(registry, vendor, model, effort)
    if row is not None and source.startswith("antigravity-desktop"):
        if not is_authority_eligible_row(row):
            reason = INELIGIBLE_AUTHORITY_REASON
            row = None
    degraded = False
    # Nonempty Claude provenance only comes from a successfully bound current
    # transcript. Launch-only missing effort keeps its existing resolution.
    # Even a scored default must degrade: the transcript did not verify it.
    if model and effort is None and (
            (row is None and vendor == "codex") or (vendor == "claude" and source)):
        row = floor_row(registry, vendor, model)
        degraded = row is not None
    if row is None:
        from refusal_evidence import caller_guidance, print_human
        print_human(dict(failed_gate="caller-identity", **caller_guidance(reason)))
        print(f"omnilane: cannot read the caller identity from pid {pid}: {reason}", file=sys.stderr)
        if reason.startswith(NO_ROW) or reason == INELIGIBLE_AUTHORITY_REASON:
            print(f"omnilane: to hand work to your own sub-agent instead, state the runtime: "
                  f"omnilane native-context --vendor {vendor} --model {model} "
                  f"--inherits-caller-runtime --workdir <dir>, then omnilane route --inherit "
                  f"--native-context <file> <lane> \"<task>\" (serves --inherit only; lanes need "
                  f"a snapshot that scores this model at this effort)", file=sys.stderr)
        return 3
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
    path = write_context(row, registry, home, effort_unverified=degraded)
    provenance = f", {source}" if source else ""
    if degraded:
        print(f"omnilane: caller is {vendor}/{model} at an unrecorded effort; degraded to its "
              f"lowest-scored row {row['id']} (ceiling {row['score']}), read from pid {pid}"
              f"{provenance}. Lanes above that ceiling need a session launched with an "
              f"explicit effort.", file=sys.stderr)
    else:
        print(f"omnilane: caller is {row['id']} (score {row['score']}), read from pid {pid}"
              f"{provenance}", file=sys.stderr)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
