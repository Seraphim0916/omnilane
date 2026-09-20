#!/usr/bin/env python3
"""Write the native capability file for the harness this runs under.

Dispatch only hands work to a harness's own sub-agent tool when the harness says
what that tool can do; without such a file every same-harness target goes out
through an external CLI. This writes the file from what is observable: the
caller's vendor, model and effort are read exactly as `omnilane whoami` reads
them. What cannot be observed from a shell is stated by the host that runs this,
with flags: `--inherits-caller-runtime` says its sub-agent tool, given no model
override, runs the caller's own model and effort. Where the identity cannot be
read at all, `--vendor` and `--model` are the host's statement of what it is; the
file then says so, and serves an inherited worker only. Prints the file's path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caller_identity  # noqa: E402

HARNESSES = {"codex": "codex", "claude": "claude-code", "grok": "grok-build", "gemini": "antigravity"}
UNVERIFIED_EFFORT = "unverified"
HOST_STATEMENT_HINT = ("omnilane: if this harness cannot be read, state what it is: omnilane native-context "
                       "--vendor VENDOR --model MODEL --inherits-caller-runtime (serves --inherit only)")


def build(vendor: str, model: str, effort: str | None, *, harness: str, workdirs: list[str],
          modes: list[str], inherits: bool, verified: bool = True) -> dict:
    value = {
        "schema_version": 1,
        "harness": harness,
        "vendor": vendor,
        "current_model": model,
        "requirements": {"tools": [], "isolation": "shared-inherited", "lifecycle": "single-shot"},
        "capabilities": [{
            "model": model,
            # An unrecorded effort matches no lane target; only --inherit can use this row.
            "efforts": [effort or UNVERIFIED_EFFORT],
            "modes": modes,
            "workdirs": workdirs,
            "tools": [],
            "isolations": ["shared-inherited"],
            "lifecycles": ["single-shot"],
        }],
    }
    if effort:
        value["current_effort"] = effort
    if inherits:
        value["inherits_caller_runtime"] = True
    if not verified:
        value["caller_identity_verified"] = False
    return value


def main(argv: list[str] | None = None, read_caller=caller_identity.read_caller) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", action="append", type=Path,
                        help="directory a native worker may be given; may repeat (default: cwd)")
    parser.add_argument("--mode", action="append", choices=("advise", "work"),
                        help="task intent the host accepts; may repeat (default: advise)")
    parser.add_argument("--harness", help="harness name (default: derived from the caller's vendor)")
    parser.add_argument("--inherits-caller-runtime", action="store_true",
                        help="the host states that a sub-agent spawned with no model override "
                             "runs the caller's own model and effort")
    parser.add_argument("--registry",
                        default=os.environ.get("OMNILANE_AA_POLICY_FILE")
                        or str(caller_identity.REPO / "config" / "aa-model-policy.json"))
    parser.add_argument("--vendor", help="host-asserted vendor, used only when the identity cannot be read")
    parser.add_argument("--model", help="host-asserted current model, used only when the identity cannot be read")
    parser.add_argument("--out", type=Path, help="file to write (default: under ~/.omnilane)")
    args = parser.parse_args(argv)
    if bool(args.vendor) != bool(args.model):
        print("omnilane: --vendor and --model go together", file=sys.stderr)
        return 2
    for name in (args.vendor, args.model):
        if name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            print(f"omnilane: invalid name: {name}", file=sys.stderr)
            return 2
    verified, recorded = True, None
    try:
        registry, _ = caller_identity.load_registry(args.registry)
        _, (vendor, model, effort), _ = read_caller(os.getpid())
    except (ValueError, OSError) as error:
        if not args.vendor:
            print(f"omnilane: cannot read the caller identity: {error}\n{HOST_STATEMENT_HINT}", file=sys.stderr)
            return 3
        print(f"omnilane: caller identity not verified ({error}); using the host's statement",
              file=sys.stderr)
        verified, vendor, model = False, args.vendor, args.model
    else:
        if args.vendor and (args.vendor != vendor or (model is not None and args.model != model)):
            print(f"omnilane: this harness reads as {vendor}/{model}, not {args.vendor}/{args.model}",
                  file=sys.stderr)
            return 2
        row, reason = caller_identity.resolve(registry, vendor, model, effort)
        if row is not None:
            # Codex spells its reasoning-off effort "none"; the registry stores it as null.
            recorded = row["effort"] or ("none" if vendor == "codex" else None)
        elif not (vendor == "codex" and model and effort is None
                  and caller_identity.floor_row(registry, vendor, model)):
            if not args.vendor:
                print(f"omnilane: cannot describe this harness: {reason}\n{HOST_STATEMENT_HINT}",
                      file=sys.stderr)
                return 3
            print(f"omnilane: caller identity not verified ({reason}); using the host's statement",
                  file=sys.stderr)
            verified, model = False, args.model
    workdirs = []
    for path in args.workdir or [Path.cwd()]:
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            print(f"omnilane: not a directory: {path}", file=sys.stderr)
            return 2
        workdirs.append(str(resolved))
    value = build(vendor, model, recorded,
                  harness=args.harness or HARNESSES.get(vendor, vendor),
                  workdirs=sorted(set(workdirs)), modes=sorted(set(args.mode or ["advise"])),
                  inherits=args.inherits_caller_runtime, verified=verified)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    out = args.out
    if out is None:
        home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        out = home / "native-context" / f"{value['harness']}--{model.replace('.', '-')}--{digest}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    staging.write_text(text)
    os.replace(staging, out)
    note = "" if args.inherits_caller_runtime else (
        "; pass --inherits-caller-runtime if this harness's sub-agent tool inherits your runtime")
    print(f"omnilane: native context for {value['harness']} {model} "
          f"(effort {recorded or 'unrecorded'}{'' if verified else ', identity host-asserted'}){note}",
          file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
