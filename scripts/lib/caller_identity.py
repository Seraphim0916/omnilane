#!/usr/bin/env python3
"""Read the exact AA caller identity of the CLI this process runs under.

Walks up the process tree to the nearest vendor CLI, reads the model and effort
it was launched with, and maps them onto the one scored configuration they
select. Launch flags are the harness's request selector: the same class of
evidence the transport overlay carries, and unlike a hand-written caller-context
file, not something the model can edit.

Prints the path of a caller-context file for that identity. Exits 3 with the
reason on stderr when it cannot decide. It never guesses: a missing flag, an
alias, or an identity the frozen registry does not score exactly once is a
refusal, not a fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_policy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MAX_DEPTH = 64
ENCODED_EFFORTS = ("xhigh", "high", "medium", "low")
Selector = tuple[str, Optional[str], Optional[str]]
Lookup = Callable[[int], Optional[tuple[int, list[str]]]]


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


def _codex_effort(argv: list[str]) -> str | None:
    effort = None
    for index, token in enumerate(argv[:-1]):
        if token in ("-c", "--config"):
            key, _, value = argv[index + 1].partition("=")
            if key.strip() == "model_reasoning_effort":
                effort = value.strip().strip("'\"")
    return effort


def read_selector(argv: list[str]) -> Selector | None:
    """(vendor, model, effort) when argv launches a vendor CLI, otherwise None."""
    if not argv:
        return None
    vendor = _vendor(argv[0])
    rest = argv[1:]
    if vendor == "claude":
        return vendor, _flag(rest, "--model"), _flag(rest, "--effort")
    if vendor == "codex":
        return vendor, _flag(rest, "-m", "--model"), _codex_effort(rest)
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
        head = subprocess.run(["ps", "-o", "ppid=", "-o", "comm=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = head.split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    comm = parts[1].strip()
    rest = args[len(comm):] if args.startswith(comm) else args.partition(" ")[2]
    return int(parts[0]), [comm, *rest.split()]


def find_launcher(pid: int, lookup: Lookup = _process) -> tuple[int, Selector] | None:
    """The nearest process at or above pid that is a vendor CLI, with its selector.

    Nearest wins: a codex worker started by a Claude session is a codex caller.
    """
    seen: set[int] = set()
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
            return pid, selector
        pid = ppid
    return None


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


def main(argv: list[str] | None = None) -> int:
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
    found = find_launcher(os.getpid())
    if found is None:
        print("omnilane: no vendor CLI among this process's ancestors; a model caller passes "
              "--caller-context FILE and a human operator --operator-asserted-human",
              file=sys.stderr)
        return 3
    pid, (vendor, model, effort) = found
    row, reason = resolve(registry, vendor, model, effort)
    if row is None:
        print(f"omnilane: cannot read the caller identity from pid {pid}: {reason}", file=sys.stderr)
        return 3
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
    path = write_context(row, registry, home)
    print(f"omnilane: caller is {row['id']} (score {row['score']}), read from pid {pid}",
          file=sys.stderr)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
