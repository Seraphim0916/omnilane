#!/usr/bin/env python3
"""Probe every selector build_overlay.py knows, one vendor at a time.

probe.py runs a single command; this derives the whole set from PROVEN and the
frozen registry, so a sweep is reproducible on any host without carrying a list
of commands from the last one. Each vendor is probed independently and reports
one of three outcomes: done, unprobeable (nobody is logged in, or the session
cannot reach the keychain), or failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_overlay  # noqa: E402
import probe as probe_module  # noqa: E402

VENDORS = ("claude", "codex", "gemini", "grok")
TOKENS = {"claude": "CLAUDE_SELECTOR_OK", "codex": "CODEX_SELECTOR_OK",
          "gemini": "GEMINI_SELECTOR_OK", "grok": "GROK_SELECTOR_OK"}
# The keychain-backed CLIs cannot authenticate from a launchd Background session
# (an ssh login is one); probing there records "not logged in" as if it were a
# finding about the selector.
KEYCHAIN_VENDORS = ("claude", "gemini", "grok")
NOT_AUTHENTICATED = re.compile(
    r"not logged in|not signed in|authentication required|please run /login|grok login"
    r"|failed to authenticate|oauth (session|token) (has )?expired|could not be refreshed"
    r"|invalid api key|unauthorized|\b401\b",
    re.IGNORECASE)
TRANSIENT = re.compile(r"\b(403|429|500|502|503|529)\b|permission-denied|overloaded|timed? ?out",
                       re.IGNORECASE)


def prompt(vendor: str) -> str:
    return f"Reply exactly {TOKENS[vendor]}. Do not use tools or delegate."


def command(vendor: str, model: str, effort: str | None, app_data: str | None = None) -> list[str]:
    text = prompt(vendor)
    if vendor == "claude":
        selector = ["--model", model] + (["--effort", effort] if effort else [])
        return ["claude", "--disable-slash-commands", *selector, "--permission-mode", "dontAsk",
                "--tools", "", "--output-format", "json", "-p", text]
    if vendor == "codex":
        return ["codex", "exec", "--json", "--skip-git-repo-check", "-m", model,
                "-c", f'model_reasoning_effort="{effort or "none"}"',
                "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"', text]
    if vendor == "grok":
        return ["grok", "--no-memory", "--no-subagents", "--no-plan", "--no-alt-screen",
                "--output-format", "json", "--verbatim", "--permission-mode", "dontAsk",
                "--tools", "Read", "--deny", "Bash", "--deny", "Edit", "--disable-web-search",
                "-m", model, "--reasoning-effort", effort or "high", "-p", text]
    if vendor == "gemini":
        return ["agy", f"--app_data_dir={app_data}", "--model", model, "-p", text]
    raise ValueError(f"unsupported vendor: {vendor}")


def plan(vendor: str) -> list[dict]:
    """One entry per evidence file this vendor's PROVEN rows point at."""
    entries: dict[str, dict] = {}
    for config_id, (_, runtime_model, evidence) in sorted(build_overlay.PROVEN.items()):
        row = build_overlay.ROWS[config_id]
        if row["vendor"] != vendor or evidence in entries:
            continue
        entries[evidence] = {"name": evidence, "config_id": config_id, "model": runtime_model,
                             "effort": None if vendor == "gemini" else row["effort"]}
    return list(entries.values())


def session_manager(runner=subprocess.run) -> str:
    try:
        return runner(["launchctl", "managername"], capture_output=True, text=True,
                      timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _text(record: dict) -> str:
    parts = [str(record.get("verdict_reason") or "")]
    for key in ("stdout", "stderr"):
        try:
            parts.append(Path(record[key]).read_text(errors="replace")[:4000])
        except (KeyError, OSError):
            pass
    return "\n".join(parts)


def sweep(vendor: str, root: Path, *, repo: Path = build_overlay.REPO, home: Path | None = None,
          run_probe=probe_module.probe, manager=session_manager, log=print,
          only_missing: bool = False) -> dict:
    """Probe one vendor into root/evidence. Never raises for a probe that merely failed.

    only_missing probes just the rows root/evidence has no record for: the
    executable is unchanged, so what it already answered still stands.
    """
    home = home or Path.home()
    entries = plan(vendor)
    report = {"vendor": vendor, "outcome": "done", "passed": [], "failed": [], "detail": ""}
    if only_missing:
        entries = [entry for entry in entries
                   if not (root / "evidence" / f"{entry['name']}.json").is_file()]
        if not entries:
            report["detail"] = "every row already has probe evidence"
            return report
    if (vendor in KEYCHAIN_VENDORS and sys.platform == "darwin"
            and os.environ.get("OMNILANE_PROBE_ANY_SESSION") != "1"):
        name = manager()
        if name and name != "Aqua":
            report.update(outcome="unprobeable",
                          detail=(f"this is a launchd {name} session (an ssh login is one); "
                                  f"{vendor}'s credentials are in the login keychain, which only an "
                                  "Aqua (GUI) session can read"))
            return report
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    cli = "agy" if vendor == "gemini" else vendor
    for index, entry in enumerate(entries):
        app_root = None
        app_data = None
        if vendor == "gemini":
            # prepare-agy-mode binds its private root to one workdir, so it is per sweep.
            app_root = home / ".omnilane" / "agy-app" / f"probe-{root.name}-{entry['name']}"
            prepared = subprocess.run(
                [sys.executable, str(repo / "scripts/lib/prepare-agy-mode.py"), "--mode", "advise",
                 "--workdir", str(work), "--app-root", str(app_root),
                 "--gemini-dir", str(home / ".gemini")],
                capture_output=True, text=True)
            if prepared.returncode != 0:
                report.update(outcome="failed",
                              detail=f"prepare-agy-mode failed: {prepared.stderr.strip()[-200:]}")
                return report
            app_data = prepared.stdout.strip()
        argv = command(vendor, entry["model"], entry["effort"], app_data)
        record = {}
        for attempt in (1, 2):
            record = run_probe(entry["name"], argv, root=root, vendor=cli,
                               expected_token=TOKENS[vendor], app_root=app_root)
            if record.get("verdict") == "pass" or attempt == 2 or not TRANSIENT.search(_text(record)):
                break
            log(f"{entry['name']}: transient failure, retrying once")
        if record.get("verdict") == "pass":
            report["passed"].append(entry["config_id"])
            log(f"{entry['name']} pass {record.get('evidence_tier')}")
            continue
        if NOT_AUTHENTICATED.search(_text(record)):
            # Not a finding about the selector: keep it out of the evidence.
            for suffix in ("json", "stdout", "stderr", "rollout", "cli_log"):
                (root / "evidence" / f"{entry['name']}.{suffix}").unlink(missing_ok=True)
            report.update(outcome="unprobeable",
                          detail=f"{cli} is not logged in on this host; log in, then re-run")
            if index:
                report["detail"] += f" ({index} earlier probe(s) had already answered)"
            return report
        report["failed"].append(entry["config_id"])
        log(f"{entry['name']} fail {str(record.get('verdict_reason'))[:120]}")
    # With only_missing the rows not re-probed still hold, so a new row that
    # fails is a finding about that row, not about the vendor.
    if not report["passed"] and not only_missing:
        report.update(outcome="failed", detail="no selector of this vendor passed")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="sweep root to write evidence into")
    parser.add_argument("--vendor", action="append", choices=VENDORS,
                        help="vendor to probe; may repeat (default: all four)")
    parser.add_argument("--plan", action="store_true", help="print the commands and probe nothing")
    args = parser.parse_args(argv)
    vendors = args.vendor or list(VENDORS)
    if args.plan:
        for vendor in vendors:
            for entry in plan(vendor):
                print(json.dumps({"name": entry["name"], "argv": command(
                    vendor, entry["model"], entry["effort"], "<app-data>")}, ensure_ascii=False))
        return 0
    reports = [sweep(vendor, args.root.expanduser()) for vendor in vendors]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(report["outcome"] == "done" for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
