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
Anything else stops at a notification; `--approve VENDOR` is the operator saying
they looked.

Exit codes: 0 nothing to do, or re-signed and verified; 10 drift found (--check);
20 drift needs an operator; 30 attempted and rolled back; 2 not configured.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
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
VENDORS = probe_sweep.VENDORS


def sha256(path: Path) -> str:
    return build_overlay.sha256(path)


def current_anchors() -> dict[str, dict[str, Path]]:
    """What the runners would execute right now, per vendor."""
    anchors = {}
    for vendor in VENDORS:
        found = shutil.which(build_overlay.CLI_NAMES[vendor])
        anchors[vendor] = {
            "cli": Path(found).resolve() if found else None,
            "runner": build_overlay.REPO / "scripts/runners" / build_overlay.RUNNERS[vendor],
        }
    return anchors


def detect(overlay: dict, anchors: dict[str, dict[str, Path]]) -> dict[str, dict]:
    """Per vendor: what no longer matches the live overlay, and the signer it recorded."""
    report = {}
    for vendor in VENDORS:
        recorded = [entry for entry in overlay.get("evidence", []) if entry.get("vendor") == vendor]
        runner_name = build_overlay.RUNNERS[vendor]
        recorded_cli = next((e for e in recorded if Path(e["path"]).name != runner_name), None)
        recorded_runner = next((e for e in recorded if Path(e["path"]).name == runner_name), None)
        reasons = []
        cli, runner = anchors[vendor]["cli"], anchors[vendor]["runner"]
        if cli is None:
            reasons.append(f"{build_overlay.CLI_NAMES[vendor]} is not on PATH")
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
        report[vendor] = {
            "drifted": bool(reasons),
            "reasons": reasons,
            "cli": str(cli) if cli else None,
            "cli_changed": any(runner_name not in reason for reason in reasons),
            "recorded_cli_path": recorded_cli["path"] if recorded_cli else None,
            "recorded_codesign": recorded_cli.get("codesign") if recorded_cli else None,
        }
    return report


def gate(vendor_report: dict, approved: bool) -> tuple[bool, str]:
    if not vendor_report["cli"]:
        return False, "the CLI is not installed"
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
    token = f"OMNILANE_RESIGN_SMOKE_{vendor.upper()}"
    command = ["bash", str(build_overlay.REPO / "scripts/dispatch.sh"), "--operator-asserted-human",
               "--background", "--single-shot", "--timeout", str(timeout), "--vendor", vendor,
               "--model", mapping["runtime_model"]]
    if mapping["selector_type"] != "model_id_encoded_effort" and mapping["runtime_effort"]:
        command += ["--effort", mapping["runtime_effort"]]
    command += ["consult", f"Reply with exactly the text {token} and nothing else. Do not use tools."]
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
    if code != "0" or token not in answer:
        return False, f"job {job} exited {code} without the token"
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


def resign(args, log=print) -> int:
    overlay_env = os.environ.get("OMNILANE_AA_TRANSPORT_OVERLAY")
    if not overlay_env:
        log("omnilane: no transport overlay is configured (OMNILANE_AA_TRANSPORT_OVERLAY); "
            "there is nothing to re-sign. See the README, 'Let your AI assistant drive omnilane', Step 2.")
        return EXIT_UNCONFIGURED
    live = Path(overlay_env).expanduser()
    try:
        overlay = json.loads(live.read_text())
    except (OSError, ValueError) as error:
        log(f"omnilane: cannot read the live overlay {live}: {error}")
        return EXIT_UNCONFIGURED
    report = detect(overlay, current_anchors())
    wanted = args.vendor or list(VENDORS)
    if args.record_signers:
        return record_signers(live, overlay, report, wanted, log)
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
    for vendor in drifted:
        allowed, reason = gate(report[vendor], vendor in (args.approve or []))
        report[vendor]["gate"] = {"allowed": allowed, "reason": reason}
        if allowed and report[vendor]["cli"]:
            alive, detail = canary(report[vendor]["cli"])
            report[vendor]["canary"] = {"ok": alive, "detail": detail}
            allowed, reason = (allowed, reason) if alive else (False, f"canary failed: {detail}")
        (proceed if allowed else held).append(vendor)
        log(f"omnilane: {vendor}: {'re-probing' if allowed else 'NOT re-signing'} - {reason}")

    sweep_id = "resign-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    home = Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane")
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
        sweeps = {vendor: probe_sweep.sweep(vendor, root, log=log) for vendor in proceed}
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
        unprobed = [vendor for vendor in VENDORS if report[vendor]["drifted"] and vendor not in proceed]
        for index, entry in enumerate(staged["evidence"]):
            if entry.get("vendor") in unprobed:
                name = Path(entry["path"]).name
                is_runner = name == build_overlay.RUNNERS[entry["vendor"]]
                old = next((e for e in overlay["evidence"] if e.get("vendor") == entry["vendor"]
                            and (Path(e["path"]).name == build_overlay.RUNNERS[e["vendor"]]) == is_runner),
                           None)
                if old is not None:
                    staged["evidence"][index] = old
        staged_path.write_text(json.dumps(staged, indent=2, ensure_ascii=False) + "\n")
        try:
            before, after = verified(live), verified(staged_path)
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
    if held and outcome == EXIT_OK:
        outcome = EXIT_OPERATOR
        for vendor in held:
            sweep = summary.get("sweeps", {}).get(vendor, {})
            if sweep.get("outcome") == "unprobeable":
                # Waiting changes nothing either: the CLI has to be logged in first.
                log(f"omnilane: {vendor} was not re-signed; its old pin stays. {sweep['detail']}: "
                    f"omnilane resign --vendor {vendor}")
            elif report[vendor].get("gate", {}).get("allowed"):
                # The signer was fine; the probes were not. Approval would change nothing.
                log(f"omnilane: {vendor} was not re-signed; its old pin stays. Retry later: "
                    f"omnilane resign --vendor {vendor}")
            else:
                log(f"omnilane: {vendor} still needs an operator. After checking the executable: "
                    f"omnilane resign --vendor {vendor} --approve {vendor}")
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
