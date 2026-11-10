"""Interactive first installation using the existing proof and origin gates."""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

import aa_policy
import build_overlay
import cli_provenance
import origin_policy
import probe_failure


def initialize(args, configured, log):
    import resign
    if not sys.stdin.isatty() or getattr(args, "daily", False):
        log("omnilane: --init requires a human operator in an interactive terminal; it cannot run unattended")
        return resign.EXIT_UNCONFIGURED
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane").expanduser().absolute()
    default = home / "transport-contracts.local.json"
    live = Path(configured).expanduser().absolute() if configured else default
    if any(path.exists() or path.is_symlink() for path in (live, default)):
        log("omnilane: --init refused: an overlay already exists. Next: omnilane resign")
        return resign.EXIT_UNCONFIGURED
    try:
        live.parent.mkdir(parents=True, exist_ok=True)
        with resign.resign_lock(live) as holder:
            if holder is not None:
                log("omnilane: another resign is running; retry --init after it finishes")
                return resign.EXIT_BUSY
            if any(path.exists() or path.is_symlink() for path in (live, default)):
                log("omnilane: --init refused: an overlay already exists. Next: omnilane resign")
                return resign.EXIT_UNCONFIGURED
            return _initialize(args, home, live, log)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, aa_policy.PolicyError) as error:
        log(f"omnilane: initialization failed ({error}); configuration was not installed")
        return resign.EXIT_HOST_CONFIG


def _initialize(args, home, live, log):
    import resign
    local = home / "local.sh"
    if local.is_symlink():
        raise ValueError("local.sh is a symlink; inspect its destination before initialization")
    old_local = local.read_bytes() if local.exists() else None
    old_local_mode = stat.S_IMODE(local.stat().st_mode) if old_local is not None else 0o600
    origin_live = origin_policy.policy_path()
    # Validate the existing policy before any probe; changed signers cannot use first trust.
    old_policy = origin_policy.load()
    old_origin = origin_live.read_bytes() if origin_live.exists() else None
    root = home / "transport-evidence" / ("init-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                                          + "-" + secrets.token_hex(3))
    root.mkdir(parents=True)
    (root / "evidence").mkdir()
    if old_local is not None:
        shutil.copy2(local, root / "local.sh.BEFORE")
    if old_origin is not None:
        shutil.copy2(origin_live, root / "origin-policy.BEFORE.json")
    policy_candidate = root / "origin-policy.init.json"
    aa_policy.atomic_json(policy_candidate, old_policy)
    anchors = resign.current_anchors()
    wanted = list(dict.fromkeys(args.vendor or resign.VENDORS))
    summary = dict(installed=False, initializing=True, vendors={}, held=[], sweeps={}, sweep_root=str(root),
                   live_overlay=str(live), first_trust=[])
    args.status_summary = summary
    fingerprints = {}
    for vendor in wanted:
        cli = anchors[vendor]["cli"]
        facts = cli_provenance.facts(cli) if cli else {}
        entry = dict(vendor=vendor, cli=str(cli) if cli else None, codesign=facts, drifted=True)
        summary["vendors"][vendor] = entry
        if not cli:
            allowed, reason = False, "cli-not-installed"
        elif facts.get("kind") == "adhoc":
            allowed, reason = False, "origin-unapproved"
        else:
            # Use the normal first-use enrollment and gate on a staged policy only.
            # Existing vendor channels are never replaced by record_first_use.
            origin_policy.record_first_use(vendor, facts, str(cli), path=policy_candidate)
            allowed, reason = origin_policy.gate(vendor, None, None, facts, str(cli), path=policy_candidate)
        entry.update(origin_reason=reason, gate=dict(allowed=allowed, reason=reason))
        if not allowed:
            summary["held"].append(vendor)
            continue
        fingerprints[vendor] = (str(cli), resign.sha256(cli), facts)
        alive, detail = resign.canary(str(cli))
        if not alive:
            summary["sweeps"][vendor] = dict(outcome="canary-failed", detail=detail)
            summary["held"].append(vendor)
            continue
        result = probe_failure.run_sweep(vendor, root, log=log)
        summary["sweeps"][vendor] = result
        if result["outcome"] != "done":
            summary["held"].append(vendor)
    # A failed whole-vendor sweep must not publish any earlier passing records.
    # Keep them for diagnosis outside the builder's evidence directory.
    import probe_sweep
    for vendor in summary["held"]:
        for entry in probe_sweep.plan(vendor):
            for path in (root / "evidence").glob(entry["name"] + ".*"):
                destination = root / "held-evidence" / vendor
                destination.mkdir(parents=True, exist_ok=True)
                os.replace(path, destination / path.name)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            build_overlay.main(["--root", str(root), "--source", "omnilane resign --init / first trust"])
    except SystemExit as error:
        log(f"omnilane: initial probe evidence did not build ({error}); no installation")
        return resign.EXIT_ROLLED_BACK
    staged = root / "transport-contracts.local.json"
    overlay = json.loads(staged.read_text())
    counts = resign.verified(staged)
    summary["held"] = [vendor for vendor in wanted if vendor in summary["held"] or not counts[vendor]]
    successful = [vendor for vendor in wanted if counts[vendor] and vendor not in summary["held"]]
    if not successful:
        summary["held"] = list(wanted)
        log("omnilane: initialization verified no vendor; local.sh and the live overlay were not installed")
        return resign.EXIT_OPERATOR
    current = resign.current_anchors()
    for vendor in successful:
        cli = current[vendor]["cli"]
        if cli is None or (str(cli), resign.sha256(cli), cli_provenance.facts(cli)) != fingerprints[vendor]:
            log(f"omnilane: {vendor} changed during initialization; no installation")
            return resign.EXIT_ROLLED_BACK
    report = resign.detect(overlay, current)
    if resign.record_signers(staged, overlay, report, successful, log) != resign.EXIT_OK:
        return resign.EXIT_ROLLED_BACK
    counts = resign.verified(staged)
    for vendor in successful:
        ok, detail = resign.smoke(vendor, overlay, staged)
        summary.setdefault("smoke", {})[vendor] = dict(ok=ok, detail=detail)
        if not ok:
            log(f"omnilane: {vendor}: smoke failed ({detail}); no installation")
            return resign.EXIT_ROLLED_BACK
    # Vendor CLIs can update themselves during smoke, even beside the old path.
    current = resign.current_anchors()
    for vendor in successful:
        cli = current[vendor]["cli"]
        if cli is None or (str(cli), resign.sha256(cli), cli_provenance.facts(cli)) != fingerprints[vendor]:
            log(f"omnilane: {vendor} changed during smoke; no installation")
            return resign.EXIT_ROLLED_BACK
    # Do not enroll a held vendor merely because it was available during setup.
    policy = origin_policy.load(policy_candidate)
    for vendor in list(policy["vendors"]):
        if vendor not in old_policy["vendors"] and vendor not in successful:
            policy["vendors"].pop(vendor)
    aa_policy.atomic_json(policy_candidate, policy)
    first_trust = [v for v in successful if v not in old_policy["vendors"]]
    summary.update(still_unprobed={v: rows for v in successful if (rows := resign.unprobed(overlay, v))},
                   still_failed_environment={v: rows for v in successful
                                             if (rows := probe_failure.failed_probes(overlay, v))})
    from selector_recheck import deterministic_rows
    summary["deterministic_failures"] = {v: rows for v in successful if (rows := deterministic_rows(overlay, v))}
    new_local = resign.overlay_configuration((old_local or b"").decode("utf-8"), live).encode("utf-8")
    # Lock the origin policy for the short publish transaction; reject concurrent edits.
    with origin_policy._lock(origin_live):
        if local.is_symlink() or (local.read_bytes() if local.exists() else None) != old_local or (
                origin_live.read_bytes() if origin_live.exists() else None) != old_origin:
            raise ValueError("local configuration changed during initialization; retry after reviewing it")
        if live.exists() or live.is_symlink():
            raise ValueError("an overlay appeared during initialization")
        written = []
        try:
            written.append((live, None, "aborted-overlay.json"))
            aa_policy.atomic_bytes(live, staged.read_bytes())
            written.append((origin_live, old_origin, "aborted-origin-policy.json"))
            aa_policy.atomic_bytes(origin_live, policy_candidate.read_bytes())
            written.append((local, old_local, "aborted-local.sh"))
            aa_policy.atomic_bytes(local, new_local)
            local.chmod(old_local_mode)
            configured, _ = resign.load_host_configuration(inherit_overlay=False)
            if not configured or Path(configured).expanduser() != live:
                assignment = resign.overlay_configuration("", live).strip()
                raise ValueError("local.sh did not persist the installed overlay path with the inherited overlay unset; "
                                 "add this line before any early return: " + assignment)
            resign.verified(live)
        except BaseException:
            for path, old, name in reversed(written):
                if old is None:
                    if path.exists():
                        os.replace(path, root / name)
                else:
                    # An atomic-write failure before replacement needs no restore.
                    if not path.exists() or path.read_bytes() != old:
                        aa_policy.atomic_bytes(path, old)
                    if path == local:
                        path.chmod(old_local_mode)
            log(f"omnilane: initialization rolled back; backups and staged evidence: {root}")
            raise
    summary.update(installed=True, re_signed=successful, verified=dict(after=counts), first_trust=first_trust)
    log(f"omnilane: first overlay installed at {live}; local.sh backup and evidence: {root}")
    return (resign.EXIT_OPERATOR if summary["held"] or summary["still_unprobed"]
            or summary["still_failed_environment"] else resign.EXIT_OK)
