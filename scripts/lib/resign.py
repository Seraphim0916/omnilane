#!/usr/bin/env python3
"""Re-sign this host's AA transport overlay after a vendor CLI changed.

Vendor CLIs update themselves, so the overlay's executable pins go stale as
routine traffic, and every lane of that vendor is then refused. This finds the
drift and, when it is safe to, repairs it without an operator:

    detect drift -> check the signer -> canary -> re-probe into a staging root
    -> build and load the staging overlay -> replace the live one atomically
    -> one real dispatch per re-probed vendor -> restore the old one on failure

A changed executable is re-probed unattended only when it still carries the
signer the live overlay recorded and still sits in the same install location.
A configuration the probe plan lists but this host never probed (a new model,
or a sweep that could not reach a login) is drift too; with the executable
unchanged, only those rows are probed. A probe that answered and failed is not
re-probed. Anything else stops at a notification; `--approve VENDOR` is the operator saying
they looked. `--trust-adhoc VENDOR` is the operator saying an adhoc signature in
that install location is their own local step, so those updates count as
same-signer.

Exit codes: 0 nothing to do, or re-signed and verified; 10 drift found (--check);
20 drift needs an operator; 30 attempted and rolled back; 40 host configuration
failed (EXIT_HOST_CONFIG); 50 another resign is running on this host (EXIT_BUSY);
2 no overlay configured (also argparse usage errors).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_policy  # noqa: E402
import build_overlay  # noqa: E402
import cli_provenance  # noqa: E402
import probe_sweep  # noqa: E402

EXIT_OK, EXIT_DRIFT, EXIT_OPERATOR, EXIT_ROLLED_BACK, EXIT_UNCONFIGURED = 0, 10, 20, 30, 2
EXIT_HOST_CONFIG = 40
EXIT_BUSY = 50
VENDORS = probe_sweep.VENDORS


def sha256(path: Path) -> str:
    return build_overlay.sha256(path)


def current_anchors() -> dict[str, dict[str, Path]]:
    """What the runners would execute right now, per vendor."""
    anchors = {}
    for vendor in VENDORS:
        found = shutil.which(build_overlay.cli_command(build_overlay.CLI_NAMES[vendor]))
        anchors[vendor] = {
            "cli": Path(found).resolve() if found else None,
            "runner": build_overlay.REPO / "scripts/runners" / build_overlay.RUNNERS[vendor],
        }
    return anchors


def detect(overlay: dict, anchors: dict[str, dict[str, Path]]) -> dict[str, dict]:
    """Per vendor: what no longer matches the live overlay, and the signer it recorded."""
    report = {}
    registry_snapshot = build_overlay.REGISTRY["snapshot"]["id"]
    for vendor in VENDORS:
        recorded = [entry for entry in overlay.get("evidence", []) if entry.get("vendor") == vendor]
        runner_name = build_overlay.RUNNERS[vendor]
        recorded_cli = next((e for e in recorded if Path(e["path"]).name != runner_name), None)
        recorded_runner = next((e for e in recorded if Path(e["path"]).name == runner_name), None)
        reasons = []
        cli, runner = anchors[vendor]["cli"], anchors[vendor]["runner"]
        if cli is None:
            reasons.append(f"{build_overlay.cli_command(build_overlay.CLI_NAMES[vendor])} is not on PATH")
        elif recorded_cli is None:
            reasons.append("the overlay pins no executable for this vendor")
        elif str(cli) != recorded_cli["path"]:
            # doctor hashes the recorded path, which an update leaves behind untouched.
            reasons.append(f"runs {cli}, overlay pins {recorded_cli['path']}")
        elif sha256(cli) != recorded_cli["sha256"]:
            reasons.append(f"{cli} changed")
        if recorded_runner is None or not runner.is_file():
            reasons.append(f"{runner_name} is not pinned")
        elif sha256(runner) != recorded_runner["sha256"]:
            reasons.append(f"{runner_name} changed")
        cli_changed = any(runner_name not in reason for reason in reasons)
        executables_unchanged = not reasons
        # The overlay is bound to one registry snapshot and dispatch refuses any
        # other, so a re-scored registry strands a host whose CLIs never moved.
        snapshot_only = executables_unchanged and overlay.get("snapshot_id") != registry_snapshot
        if overlay.get("snapshot_id") != registry_snapshot:
            reasons.append(f"the score registry moved from {overlay.get('snapshot_id')} "
                           f"to {registry_snapshot}")
        missing = unprobed(overlay, vendor)
        if missing:
            reasons.append(f"{len(missing)} configuration(s) never probed on this host: "
                           + ", ".join(missing))
        if any(m.get("pending_recheck") and m["identity"]["vendor"] == vendor
               for m in overlay.get("mappings", [])):
            reasons.append("pending re-check")
        report[vendor] = {
            "drifted": bool(reasons),
            "reasons": reasons,
            "snapshot_only": snapshot_only,
            # Unchanged executables mean what they already answered still stands,
            # so only the rows with no evidence need a probe.
            "only_missing": executables_unchanged and bool(snapshot_only or missing),
            "unprobed": missing,
            "cli": str(cli) if cli else None,
            "cli_changed": cli_changed,
            "recorded_cli_path": recorded_cli["path"] if recorded_cli else None,
            "recorded_codesign": recorded_signer(recorded_cli),
        }
    return report


NOT_PROBED = "not probed on this host"


def unprobed(overlay: dict, vendor: str) -> list[str]:
    """Configurations this vendor could run here that no probe ever answered.

    A row the probe plan gained after the last sweep either appears in unproven[]
    as not probed, or, from an overlay built before the plan listed it, nowhere.
    A probe that answered and failed is a finding, not a gap, so it is not here:
    re-probing it on every run would only repeat the failure. A vendor with no
    verified mapping was never set up on this host, which is not drift either.
    """
    if not any(build_overlay.ROWS[m["config_id"]]["vendor"] == vendor
               for m in overlay.get("mappings", []) if m.get("config_id") in build_overlay.ROWS):
        return []
    mapped = {m.get("config_id") for m in overlay.get("mappings", [])}
    reasons = {u.get("config_id"): u.get("verdict_reason") for u in overlay.get("unproven", [])}
    return sorted(cid for cid in build_overlay.PROVEN
                  if cid in build_overlay.ROWS
                  and build_overlay.ROWS[cid]["vendor"] == vendor and cid not in mapped
                  and reasons.get(cid, NOT_PROBED) == NOT_PROBED)


def recorded_signer(entry: dict | None) -> dict | None:
    """The signer the overlay recorded for a CLI, with any waiver the operator attached."""
    if not entry:
        return None
    signer = dict(entry.get("codesign") or {})
    if entry.get("operator_trust"):
        signer["operator_trust"] = entry["operator_trust"]
    return signer or None


def gate(vendor_report: dict, approved: bool) -> tuple[bool, str]:
    if not vendor_report["cli"]:
        return False, "the CLI is not installed"
    if vendor_report.get("snapshot_only"):
        return True, "only the score registry changed; existing probe evidence is reused"
    if vendor_report.get("only_missing"):
        return True, "the executable is unchanged; probing only the configurations never probed here"
    if not vendor_report["cli_changed"]:
        return True, "only omnilane's own runner script changed"
    current = cli_provenance.facts(vendor_report["cli"])
    vendor_report["codesign"] = current
    allowed, reason = cli_provenance.verdict(
        vendor_report["recorded_codesign"], vendor_report["recorded_cli_path"],
        current, vendor_report["cli"])
    if not allowed and approved:
        return True, f"approved by the operator ({reason})"
    return allowed, reason


def canary(cli: str, runner=subprocess.run) -> tuple[bool, str]:
    try:
        result = runner([cli, "--version"], capture_output=True, text=True, timeout=30,
                        stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"--version did not run: {error.__class__.__name__}"
    text = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not text:
        return False, f"--version exited {result.returncode}"
    return True, text[0][:80]


def previous_root(overlay: dict) -> Path | None:
    for entry in overlay.get("evidence", []):
        if "vendor" not in entry and Path(entry["path"]).name == "probe-manifest.json":
            return Path(entry["path"]).parent
    return None


def verified(overlay_path: Path) -> dict[str, int]:
    """Load an overlay the way dispatch does and count what it verifies per vendor."""
    saved = {key: os.environ.get(key) for key in
             ("OMNILANE_AA_TRANSPORT_OVERLAY", "OMNILANE_AA_OVERLAY_SHA256")}
    os.environ["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(overlay_path)
    os.environ.pop("OMNILANE_AA_OVERLAY_SHA256", None)
    try:
        registry, _ = aa_policy.load_registry(build_overlay.REPO / "config/aa-model-policy.json")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    counts = {vendor: 0 for vendor in VENDORS}
    stale = set(registry.get("_stale_transport_vendors", []))
    for row in registry["scored_configs"]:
        if row["vendor"] in counts and row["vendor"] not in stale \
                and row["transport_mapping"].get("runtime_verified") is True:
            counts[row["vendor"]] += 1
    return counts


def smoke(vendor: str, overlay: dict, overlay_path: Path, timeout: int = 300,
          runner=subprocess.run) -> tuple[bool, str]:
    """One real advise dispatch on the vendor's cheapest verified selector.

    The human assertion skips the ceiling, not the transport: the job still goes
    through dispatch.sh, the runner and the CLI. The mapping gate itself was
    already exercised by verified().
    """
    rows = [(build_overlay.ROWS[m["config_id"]], m) for m in overlay["mappings"]
            if build_overlay.ROWS[m["config_id"]]["vendor"] == vendor
            and (m["runtime_effort"] or m["selector_type"] == "model_id_encoded_effort"
                 or vendor == "claude")]
    if not rows:
        return False, "no verified mapping to dispatch"
    row, mapping = min(rows, key=lambda pair: (pair[0]["score"], pair[0]["id"]))
    # Asking for a verbatim string reads as prompt injection to small models
    # (claude-haiku-4-5 refused it on 2026-10-03); a fresh sum still proves a live answer.
    left, right = 100 + secrets.randbelow(900), 100 + secrets.randbelow(900)
    expected = str(left + right)
    command = ["bash", str(build_overlay.REPO / "scripts/dispatch.sh"), "--operator-asserted-human",
               "--background", "--single-shot", "--timeout", str(timeout), "--vendor", vendor,
               "--model", mapping["runtime_model"]]
    if mapping["selector_type"] != "model_id_encoded_effort" and mapping["runtime_effort"]:
        command += ["--effort", mapping["runtime_effort"]]
    command += ["consult", f"What is {left} plus {right}? Answer with the number only. Do not use tools."]
    env = dict(os.environ, OMNILANE_AA_TRANSPORT_OVERLAY=str(overlay_path))
    env.pop("OMNILANE_AA_OVERLAY_SHA256", None)
    try:
        started = runner(command, capture_output=True, text=True, timeout=120, env=env,
                         stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"dispatch did not start: {error.__class__.__name__}"
    job = started.stdout.strip().splitlines()[-1] if started.stdout.strip() else ""
    if started.returncode != 0 or not job:
        return False, f"dispatch exited {started.returncode}: {started.stderr.strip()[-200:]}"
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
    directory = home / "jobs" / job
    deadline = time.time() + timeout + 60
    while time.time() < deadline and not (directory / "exit").exists():
        time.sleep(3)
    try:
        code = (directory / "exit").read_text().strip()
        answer = (directory / "out.txt").read_text(errors="replace")
    except OSError:
        return False, f"job {job} did not finish"
    if code != "0" or expected not in answer:
        return False, f"job {job} exited {code} without the expected answer {expected}"
    return True, f"job {job} on {row['id']} answered"


def record_signers(live: Path, overlay: dict, report: dict, wanted: list[str], log) -> int:
    """Operator action: adopt the signer of every executable the overlay already pins.

    An overlay signed before 0.43.0 recorded no signer, so its first drift would
    always stop for an operator. This is that operator decision made ahead of
    time, and only for an executable whose path and hash still match the pin.
    """
    changed = []
    for entry in overlay.get("evidence", []):
        vendor = entry.get("vendor")
        if vendor not in wanted or Path(entry["path"]).name == build_overlay.RUNNERS[vendor]:
            continue
        if report[vendor]["cli_changed"]:
            log(f"omnilane: {vendor}: drifted, so its signer is not adopted; re-sign it instead")
            continue
        facts = cli_provenance.facts(entry["path"])
        if entry.get("codesign") != facts:
            entry["codesign"] = facts
            changed.append(f"{vendor}={facts['signer']}")
    if not changed:
        log("omnilane: every pinned executable already has its signer recorded")
        return EXIT_OK
    backup = live.with_name(live.name + ".before-record-signers-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(live, backup)
    aa_policy.atomic_bytes(live, (json.dumps(overlay, indent=2, ensure_ascii=False) + "\n").encode())
    try:
        verified(live)
    except (aa_policy.PolicyError, OSError, ValueError) as error:
        aa_policy.atomic_bytes(live, backup.read_bytes())
        log(f"omnilane: the overlay stopped loading ({error}); restored {backup}")
        return EXIT_ROLLED_BACK
    log(f"omnilane: recorded signers {', '.join(changed)} (backup {backup})")
    return EXIT_OK


def trust_adhoc(live: Path, overlay: dict, report: dict, wanted: list[str], log) -> int:
    """Operator action: an adhoc executable of this vendor, in its current install
    location, may be re-probed unattended from now on.

    The operator is saying a local step re-signs this CLI on every update, so the
    vendor's team will never be on it. The waiver is bound to the install location
    the overlay pins; a new directory or an unsigned executable still stops.
    """
    changed = []
    for entry in overlay.get("evidence", []):
        vendor = entry.get("vendor")
        if vendor not in wanted or Path(entry["path"]).name == build_overlay.RUNNERS[vendor]:
            continue
        if entry.get("operator_trust") == cli_provenance.TRUST_ADHOC:
            continue
        if not entry.get("codesign"):
            log(f"omnilane: {vendor} has no recorded signer to bind the trust to; "
                "run omnilane resign --record-signers first")
            continue
        entry["operator_trust"] = cli_provenance.TRUST_ADHOC
        entry["operator_trust_recorded_at"] = datetime.now(timezone.utc).isoformat()
        changed.append(f"{vendor} in {cli_provenance.family(entry['path'])}")
    if not changed:
        log("omnilane: nothing to record; that vendor is already trusted adhoc, or the overlay pins no executable for it")
        return EXIT_OK
    backup = live.with_name(live.name + ".before-trust-adhoc-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(live, backup)
    aa_policy.atomic_bytes(live, (json.dumps(overlay, indent=2, ensure_ascii=False) + "\n").encode())
    try:
        verified(live)
    except (aa_policy.PolicyError, OSError, ValueError) as error:
        aa_policy.atomic_bytes(live, backup.read_bytes())
        log(f"omnilane: the overlay stopped loading ({error}); restored {backup}")
        return EXIT_ROLLED_BACK
    log(f"omnilane: trusting adhoc {', '.join(changed)} (backup {backup})")
    return EXIT_OK


def load_host_configuration() -> tuple[str, dict]:
    """Load dispatch's shell configuration and resolve binaries before reading an overlay."""
    common = Path(build_overlay.__file__).with_name("common.sh")
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
    try:
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c",
             'source "$1" >&2; printf "%s" "${OMNILANE_AA_TRANSPORT_OVERLAY:-}"',
             "omnilane-resign-config", str(common)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
        )
        return result.stdout, current_anchors() if result.stdout else {}
    except (OSError, subprocess.CalledProcessError) as error:
        detail = (error.stderr or f"shell exited {error.returncode}") \
            if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise RuntimeError(f"cannot load host configuration {common} ({home / 'local.sh'}): "
                           + " ".join(detail.splitlines())) from None


def retain_held(staged: dict, overlay: dict, keep: list[str],
                held: dict[str, str], checked_at: str) -> dict[str, dict]:
    """Carry verified contracts forward; only repeated CLI absence retires them."""
    status = {}

    def row_vendor(entry: dict) -> str | None:
        # A row the registry dropped since the overlay was signed has no vendor
        # to file it under and nothing left to dispatch to.
        return build_overlay.ROWS.get(entry.get("config_id"), {}).get("vendor")

    for vendor in keep:
        old = [m for m in overlay.get("mappings", [])
               if m["identity"]["vendor"] == vendor and m.get("config_id") in build_overlay.ROWS]
        mark = None
        removed = False
        if vendor in held and old:
            previous = old[0].get("pending_recheck", {})
            mark = {"reason": held[vendor], "at": checked_at,
                    "consecutive_runs": previous.get("consecutive_runs", 0) + 1}
            removed = (mark["consecutive_runs"] >= 2
                       and mark["reason"] == previous.get("reason") == "CLI not found")
            status[vendor] = {"removed": removed, **mark}
        saved = [] if removed else [
            {**m, "pending_recheck": mark} if mark else m for m in old]
        staged["mappings"] = [m for m in staged["mappings"]
                              if m["identity"]["vendor"] != vendor] + saved
        staged["evidence"] = [e for e in staged["evidence"]
                              if e.get("vendor") != vendor]
        if not removed:
            staged["evidence"] += [e for e in overlay["evidence"]
                                   if e.get("vendor") == vendor]
        staged["unproven"] = [u for u in staged.get("unproven", [])
                              if row_vendor(u) != vendor]
        staged["unproven"] += [u for u in overlay.get("unproven", [])
                               if row_vendor(u) == vendor]
        if removed:
            staged["unproven"] += [
                {"config_id": m["config_id"], "verdict_reason": "vendor CLI not installed",
                 "observed_model": None, "probed_at": None} for m in old]
    return status


def install_pending(live: Path, overlay: dict, held: dict[str, str],
                    root: Path, checked_at: str, log) -> tuple[int, dict]:
    """Metadata-only transaction when no vendor could finish; never rebuild probes."""
    staged = json.loads(json.dumps(overlay))
    status = retain_held(staged, overlay, list(held), held, checked_at)
    # A metadata-only run preserves existing array order and all evidence bytes.
    by_id = {m["config_id"]: m for m in staged["mappings"]}
    staged["mappings"] = [by_id[m["config_id"]] for m in overlay["mappings"]
                          if m["config_id"] in by_id]
    removed = {v for v, mark in status.items() if mark["removed"]}
    staged["evidence"] = [e for e in overlay["evidence"] if e.get("vendor") not in removed]
    known = {u["config_id"] for u in overlay.get("unproven", [])}
    new = [u for u in staged["unproven"] if u["config_id"] not in known]
    if "unproven" in overlay or new:
        staged["unproven"] = overlay.get("unproven", []) + new
    else:
        staged.pop("unproven", None)
    if staged == overlay:
        return EXIT_OK, status
    root.mkdir(parents=True, exist_ok=True)
    path = root / "transport-contracts.local.json"
    path.write_text(json.dumps(staged, indent=2, ensure_ascii=False) + "\n")
    try:
        verified(path)
    except (aa_policy.PolicyError, OSError, ValueError) as error:
        log(f"omnilane: pending overlay does not load ({error}); live overlay untouched")
        return EXIT_ROLLED_BACK, {}
    backup = live.with_name(live.name + f".before-{root.name}")
    shutil.copy2(live, backup)
    aa_policy.atomic_bytes(live, path.read_bytes())
    log(f"omnilane: updated pending mappings only (backup {backup}); no probe rebuild")
    return EXIT_OK, status


@contextlib.contextmanager
def resign_lock(live: Path):
    """Yield None when this run holds the host's resign lock, else the holder's pid text.

    Each run builds its staged overlay from the live one it read at the start and
    installs it minutes later, so a concurrent run would overwrite the other's result.
    """
    fd = os.open(live.with_name(live.name + ".resign.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield os.pread(fd, 32, 0).decode(errors="replace").strip() or "unknown"
            return
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        yield None
    finally:
        os.close(fd)


def resign(args, log=print) -> int:
    try:
        overlay_env, anchors = load_host_configuration()
    except RuntimeError as error:
        log(f"omnilane: {error}")
        return EXIT_HOST_CONFIG
    if not overlay_env:
        log("omnilane: no transport overlay is configured (OMNILANE_AA_TRANSPORT_OVERLAY); "
            "there is nothing to re-sign. See the README, 'Let your AI assistant drive omnilane', Step 2.")
        return EXIT_UNCONFIGURED
    live = Path(overlay_env).expanduser()
    if args.check:
        return _resign(args, live, anchors, log)
    # An unreadable overlay is a host error that must leave nothing behind, so
    # check it before creating the lock file next to it; _resign reads it again
    # under the lock.
    try:
        json.loads(live.read_text())
    except (OSError, ValueError) as error:
        log(f"omnilane: cannot read the live overlay {live}: {error}")
        return EXIT_HOST_CONFIG
    with resign_lock(live) as holder:
        if holder is not None:
            log(f"omnilane: another omnilane resign is already running on this host (pid {holder}); "
                "run this one after it finishes, so neither install overwrites the other")
            return EXIT_BUSY
        return _resign(args, live, anchors, log)


def _resign(args, live: Path, anchors, log) -> int:
    try:
        overlay = json.loads(live.read_text())
    except (OSError, ValueError) as error:
        log(f"omnilane: cannot read the live overlay {live}: {error}")
        return EXIT_HOST_CONFIG
    report = detect(overlay, anchors)
    wanted = args.vendor or list(VENDORS)
    if args.record_signers:
        return record_signers(live, overlay, report, wanted, log)
    if args.trust_adhoc:
        return trust_adhoc(live, overlay, report, args.trust_adhoc, log)
    drifted = [vendor for vendor in wanted if report[vendor]["drifted"]]
    summary = {"host": overlay.get("host"), "checked_at": datetime.now(timezone.utc).isoformat(),
               "live_overlay": str(live), "live_sha256": sha256(live), "vendors": report}
    if not drifted:
        log("omnilane: transport overlay matches what the runners execute; nothing to re-sign")
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return EXIT_OK
    for vendor in drifted:
        log(f"omnilane: {vendor} drifted: " + "; ".join(report[vendor]["reasons"]))
    if args.check:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        log("omnilane: run `omnilane resign` to re-probe and re-sign")
        return EXIT_DRIFT

    proceed, held = [], []
    hold_reasons, pending = {}, {}
    installed = False
    for vendor in drifted:
        allowed, reason = gate(report[vendor], vendor in (args.approve or []))
        report[vendor]["gate"] = {"allowed": allowed, "reason": reason}
        if allowed and report[vendor]["cli"]:
            alive, detail = canary(report[vendor]["cli"])
            report[vendor]["canary"] = {"ok": alive, "detail": detail}
            allowed, reason = (allowed, reason) if alive else (False, f"canary failed: {detail}")
        (proceed if allowed else held).append(vendor)
        if not allowed:
            hold_reasons[vendor] = ("CLI not found" if not report[vendor]["cli"] else
                                    "canary failed" if report[vendor].get("canary", {}).get("ok") is False
                                    else "signer gate refused")
        log(f"omnilane: {vendor}: {'re-probing' if allowed else 'NOT re-signing'} - {reason}")

    sweep_id = "resign-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
    if (home / "transport-evidence" / sweep_id).exists():
        sweep_id += "-" + os.urandom(2).hex()  # two runs in one second
    root = home / "transport-evidence" / sweep_id
    outcome = EXIT_OK
    if proceed:
        source = previous_root(overlay)
        if source is None or not (source / "evidence").is_dir():
            log("omnilane: the live overlay's probe evidence is gone; a full sweep is needed "
                "(scripts/lib/probe_sweep.py --root NEW, then build_overlay.py --root NEW)")
            return EXIT_OPERATOR
        root.mkdir(parents=True)
        shutil.copytree(source / "evidence", root / "evidence")
        shutil.copy2(live, root / "live-overlay.BEFORE.json")
        sweeps = {vendor: probe_sweep.sweep(vendor, root, log=log,
                                            only_missing=report[vendor]["only_missing"])
                  for vendor in proceed}
        summary["sweeps"] = sweeps
        unfinished = [vendor for vendor, result in sweeps.items() if result["outcome"] != "done"]
        for vendor in unfinished:
            log(f"omnilane: {vendor}: {sweeps[vendor]['outcome']} - {sweeps[vendor]['detail']}")
        # A selector the live overlay verifies and this sweep could not is more
        # likely a provider having a bad hour than a selector that stopped working.
        # Installing that would trade a stale pin for a smaller overlay.
        was_verified = {mapping["config_id"] for mapping in overlay.get("mappings", [])}
        for vendor, result in sweeps.items():
            lost = sorted(was_verified & set(result["failed"]))
            if lost and vendor not in unfinished and not args.allow_shrink:
                result["regressed"] = lost
                unfinished.append(vendor)
                log(f"omnilane: {vendor}: {len(lost)} selector(s) the live overlay verifies failed "
                    f"this time ({', '.join(lost)}); keeping the old pin. Retry later, or pass "
                    "--allow-shrink if they are really gone")
        if unfinished:
            held += unfinished
            hold_reasons.update({vendor: "regressed" if sweeps[vendor].get("regressed")
                                 else "sweep unfinished" for vendor in unfinished})
            proceed = [vendor for vendor in proceed if vendor not in unfinished]
            # An unfinished vendor keeps its old evidence so the rebuild stays honest about it.
            for vendor in unfinished:
                for entry in probe_sweep.plan(vendor):
                    for old in (source / "evidence").glob(entry["name"] + ".*"):
                        shutil.copy2(old, root / "evidence" / old.name)
    if proceed:
        with contextlib.redirect_stdout(sys.stderr):  # stdout is reserved for --json
            build_overlay.main(["--root", str(root), "--source",
                                f"{overlay.get('host')} / omnilane resign {sweep_id} / "
                                f"re-probed: {', '.join(proceed)}"])
        staged_path = root / "transport-contracts.local.json"
        staged = json.loads(staged_path.read_text())
        # Only a re-probed vendor gets a new pin; every other drifted one keeps the
        # pin it had, because its new executable was never probed.
        pins_kept = [vendor for vendor in VENDORS if report[vendor]["drifted"] and vendor not in proceed]
        for index, entry in enumerate(staged["evidence"]):
            if entry.get("codesign") is not None:
                # build_overlay anchors every vendor afresh, so a trust recorded on an
                # untouched vendor would vanish with a re-sign of another one.
                old = next((e for e in overlay["evidence"] if e.get("vendor") == entry["vendor"]
                            and e.get("operator_trust")), None)
                if old is not None:
                    entry["operator_trust"] = old["operator_trust"]
                    entry["operator_trust_recorded_at"] = old.get("operator_trust_recorded_at")
        pending = retain_held(staged, overlay, pins_kept, hold_reasons, summary["checked_at"])
        staged_path.write_text(json.dumps(staged, indent=2, ensure_ascii=False) + "\n")
        try:
            if overlay.get("snapshot_id") == staged["snapshot_id"]:
                before = verified(live)
            else:
                # Bound to another registry snapshot, the live overlay does not load at all.
                before = {vendor: 0 for vendor in VENDORS}
            after = verified(staged_path)
        except (aa_policy.PolicyError, OSError, ValueError) as error:
            log(f"omnilane: the staged overlay does not load ({error}); live overlay untouched")
            return EXIT_ROLLED_BACK
        summary["verified"] = {"before": before, "after": after}
        empty = [vendor for vendor in proceed if after[vendor] == 0]
        if empty:
            log(f"omnilane: staged overlay verifies nothing for {', '.join(empty)}; "
                "live overlay untouched")
            return EXIT_ROLLED_BACK
        for vendor in proceed:
            if after[vendor] < max(before[vendor], len(sweeps[vendor]["passed"])):
                log(f"omnilane: {vendor}: fewer selectors verified than expected "
                    f"({before[vendor]} -> {after[vendor]}); see {root}/evidence")
        backup = live.with_name(live.name + f".before-{sweep_id}")
        shutil.copy2(live, backup)
        aa_policy.atomic_bytes(live, staged_path.read_bytes())
        installed = True
        log(f"omnilane: installed {staged_path} over {live} (backup {backup})")
        failures = []
        if not args.no_smoke:
            for vendor in proceed:
                ok, detail = smoke(vendor, staged, live)
                summary.setdefault("smoke", {})[vendor] = {"ok": ok, "detail": detail}
                log(f"omnilane: {vendor}: smoke {'passed' if ok else 'FAILED'} - {detail}")
                if not ok:
                    failures.append(vendor)
        if failures:
            aa_policy.atomic_bytes(live, backup.read_bytes())
            log(f"omnilane: smoke failed for {', '.join(failures)}; restored {backup}")
            outcome = EXIT_ROLLED_BACK
            installed = False
        else:
            gaps = {vendor: unprobed(staged, vendor) for vendor in VENDORS}
            gaps = {vendor: rows for vendor, rows in gaps.items() if rows}
            summary["still_unprobed"] = gaps
            for vendor, rows in gaps.items():
                # Dispatch refuses these rows until a probe answers them; the next
                # resign picks them up as drift, from a session that can probe them.
                log(f"omnilane: WARNING {vendor}: {len(rows)} configuration(s) are installed "
                    f"unprobed and will be refused: {', '.join(rows)}. Run omnilane resign "
                    f"--vendor {vendor} from a session that can reach its login"
                    + (" (this run also skipped the smoke dispatch)" if args.no_smoke else ""))
    if held and not proceed and outcome == EXIT_OK:
        outcome, pending = install_pending(live, overlay, hold_reasons, root,
                                           summary["checked_at"], log)
        installed = outcome == EXIT_OK
    if held and outcome == EXIT_OK:
        outcome = EXIT_OPERATOR
    for vendor in held:
        mark = pending.get(vendor, {})
        if not installed:
            log(f"omnilane: {vendor} was not re-signed; live mappings unchanged "
                "(pending update was not installed)")
            continue
        if mark.get("removed"):
            log(f"omnilane: {vendor} mappings removed after two consecutive runs: CLI not found")
            continue
        if not mark:
            log(f"omnilane: {vendor} was not re-signed; no verified mappings to retain "
                f"({hold_reasons[vendor]}).")
            continue
        log(f"omnilane: {vendor} was not re-signed; mappings kept and marked pending re-check "
            f"({hold_reasons[vendor]}; consecutive runs {mark.get('consecutive_runs', 0)}).")
        sweep = summary.get("sweeps", {}).get(vendor, {})
        if sweep.get("outcome") == "unprobeable":
            log(f"omnilane: {vendor}: {sweep['detail']}: omnilane resign --vendor {vendor}")
        elif report[vendor].get("gate", {}).get("allowed"):
            log(f"omnilane: {vendor}: Retry later: omnilane resign --vendor {vendor}")
        else:
            log(f"omnilane: {vendor} still needs an operator. After checking the executable: "
                f"omnilane resign --vendor {vendor} --approve {vendor}")
    summary["pending_recheck"] = pending
    summary.update(re_signed=proceed if outcome != EXIT_ROLLED_BACK else [], held=held,
                   exit_code=outcome, sweep_root=str(root) if root.exists() else None)
    if root.exists():
        (root / "resign-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                                            default=str) + "\n")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     epilog=__doc__.split("Exit codes:")[1].strip())
    parser.add_argument("--check", action="store_true", help="report drift and change nothing")
    parser.add_argument("--vendor", action="append", choices=VENDORS,
                        help="limit to this vendor; may repeat")
    parser.add_argument("--approve", action="append", choices=VENDORS,
                        help="operator approval to re-probe this vendor despite its signer check")
    parser.add_argument("--trust-adhoc", action="append", choices=VENDORS, metavar="VENDOR",
                        help="operator action: this vendor's executable is re-signed adhoc by a local "
                             "step, so an adhoc update in the same install location may be re-probed "
                             "unattended; may repeat")
    parser.add_argument("--record-signers", action="store_true",
                        help="operator action: adopt the signers of the executables already pinned")
    parser.add_argument("--allow-shrink", action="store_true",
                        help="install even when a selector the live overlay verifies failed this time")
    parser.add_argument("--no-smoke", action="store_true",
                        help="skip the real dispatch after installing (not for unattended use)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    return resign(args, log=lambda text: print(text, file=sys.stderr))


if __name__ == "__main__":
    raise SystemExit(main())
