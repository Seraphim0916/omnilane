#!/usr/bin/env python3
"""Dry-run compatibility matrix; fake CLIs never call a provider."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check_direct_grok_worker_auto(base, home, env):
    """The internal worker must resolve Grok auto before probing ACP."""
    direct = base / "direct-grok-worker"
    direct.mkdir()
    prompt = direct / "prompt.txt"
    output = direct / "out.txt"
    args_log = direct / "grok-args.txt"
    acp_marker = direct / "unexpected-acp-probe"
    fake_grok = direct / "grok"
    prompt.write_text("fake direct worker prompt\n")
    fake_grok.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" >> '{args_log}'\n"
        "if [ \"${1:-}\" = agent ]; then\n"
        f"  : > '{acp_marker}'\n"
        "  exit 91\n"
        "fi\n"
        "printf 'fake-grok-ok\n'\n"
    )
    fake_grok.chmod(0o755)

    worker_env = env.copy()
    worker_env.update(
        OMNILANE_HOME=str(home),
        OMNILANE_REPO=str(ROOT),
        OMNILANE_SESSION_MODE="auto",
        OMNILANE_GROK_MAX_ATTEMPTS="1",
        OMNILANE_TIMEOUT="10",
        GROK_BIN=str(fake_grok),
    )
    process = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/lib/job-worker.sh"),
            "grok",
            "advise",
            str(direct),
            "grok-4.6",
            "-",
            str(prompt),
            str(output),
        ],
        cwd=ROOT,
        env=worker_env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    failures = []
    if process.returncode != 0:
        failures.append(
            "direct grok/advise/auto failed: "
            f"rc={process.returncode}; stdout={process.stdout.strip()}; "
            f"stderr={process.stderr.strip()}"
        )
    if acp_marker.exists():
        failures.append("direct grok/advise/auto probed ACP")
    if not output.exists() or output.read_text().strip() != "fake-grok-ok":
        failures.append("direct grok/advise/auto did not run single-shot")
    return failures

def main():
    with tempfile.TemporaryDirectory(prefix="omnilane-auto-policy-") as tmp:
        base = Path(tmp)
        home, bins = base / "home", base / "bin"
        home.mkdir()
        bins.mkdir()
        vendors = {"codex": "gpt-5.6-sol", "grok": "grok-4.6",
                   "claude": "claude-sonnet-5", "gemini": "gemini-3.7-flash-medium"}
        (home / "routing.local.yaml").write_text("".join(
            f"policy-{vendor}: {vendor} {model} -\n" for vendor, model in vendors.items()))
        marker = base / "unexpected-provider-call"
        for name in ["codex", "grok", "claude", "agy", "gemini"]:
            path = bins / name
            path.write_text(f"#!/bin/sh\nprintf invoked >> '{marker}'\nexit 91\n")
            path.chmod(0o755)
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("OMNILANE_") or key in ["CODEX_BIN", "GROK_BIN", "CLAUDE_BIN", "GEMINI_BIN", "AGY_BIN"]:
                env.pop(key)
        env.update(OMNILANE_HOME=str(home), PATH=str(bins) + os.pathsep + env["PATH"],
                   CODEX_BIN=str(bins / "codex"), GROK_BIN=str(bins / "grok"),
                   CLAUDE_BIN=str(bins / "claude"), GEMINI_BIN=str(bins / "agy"))
        failures = []
        count = 0
        for vendor in vendors:
            for mode in ["work", "advise"]:
                for requested in ["auto", "live", "single-shot"]:
                    args = ["bash", str(ROOT / "scripts/dispatch.sh"), "--dry-run",
                            "--background", "--mode", mode, "--workdir", str(ROOT), "--vendor", vendor]
                    if requested != "auto":
                        args.append("--" + requested)
                    args += ["policy-" + vendor, "Never call a provider."]
                    process = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True)
                    reject = (
                        (vendor == "grok" and mode == "work")
                        or (vendor == "grok" and mode == "advise" and requested == "live")
                    )
                    expected = "single-shot" if requested == "single-shot" or (requested == "auto" and vendor in ["codex", "grok"]) else "live"
                    label = f"{vendor}/{mode}/{requested}"
                    if reject:
                        if mode == "work":
                            marker_text = "not enforced on macOS"
                        else:
                            marker_text = "only explicit --mode sysops"
                        ok = process.returncode == 2 and marker_text in process.stderr
                    else:
                        result = dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)
                        ok = (process.returncode == 0
                              and result.get("session_mode") == expected
                              and result.get("vendor") == vendor
                              and result.get("mode") == mode)
                    if not ok:
                        failures.append(f"{label}: expected {'reject' if reject else expected}; rc={process.returncode}; stdout={process.stdout.strip()}; stderr={process.stderr.strip()}")
                    count += 1
        if marker.exists():
            failures.append("dry-run invoked a fake provider CLI")
        if (home / "jobs").exists() and any((home / "jobs").iterdir()):
            failures.append("dry-run created job state")
        failures.extend(check_direct_grok_worker_auto(base, home, env))
        count += 1
        if failures:
            print("\n".join(failures))
            print(f"FAIL: {len(failures)} failures across {count} policy cases")
            return 1
        print(f"PASS: {count} policy cases; no provider invocation; no job state")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
