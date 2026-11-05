#!/usr/bin/env bash

# Use the same offline boundary as the existing thread integration suite.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export CLAUDE_CODE_SESSION_ID="c2-pinning-test-foreman"

# Python owns disposable fixtures; the exercised entry point is the real CLI.
# No provider executable or operator configuration survives offline_env.py.
python3 -c '
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])


def check(condition, message):
    if not condition:
        raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix="c2 thread pinning ") as directory:
    base = Path(directory).resolve()
    home = base / "omnilane home"
    binaries = base / "fake bin"
    physical = base / "physical workspace"
    other = base / "different workspace"
    alias = base / "workspace alias"
    other_alias = base / "different alias"
    for path in (home, binaries, physical, other):
        path.mkdir()
    alias.symlink_to(physical, target_is_directory=True)
    other_alias.symlink_to(other, target_is_directory=True)
    (home / "routing.local.yaml").write_text(
        "consult: claude fake-pinned-model low | codex fake-pinned-model low\n"
    )
    calls = base / "provider calls.jsonl"
    fake = binaries / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ[\"PINNING_CALLS\"], \"a\") as out:\n"
        "    out.write(json.dumps({\"argv\": args, \"cwd\": os.getcwd()}) + \"\\n\")\n"
        "flag = \"--resume\" if \"--resume\" in args else \"--session-id\"\n"
        "session = args[args.index(flag) + 1]\n"
        "print(json.dumps({\"type\": \"result\", \"result\": \"pinning answer\", \"session_id\": session}))\n"
    )
    fake.chmod(0o755)
    # A vendor mismatch must refuse even though the alternate CLI is available.
    (binaries / "codex").symlink_to(fake)
    env = dict(os.environ, OMNILANE_HOME=str(home), PINNING_CALLS=str(calls))
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]

    def dispatch(name, workdir, vendor="claude", model="fake-pinned-model", effort="low"):
        return subprocess.run(
            [str(root / "scripts/dispatch.sh"), "--thread", name,
             "--vendor", vendor, "--model", model, "--effort", effort,
             "--workdir", str(workdir), "--single-shot", "--timeout", "30",
             "consult", "exercise thread pinning"],
            env=env, text=True, capture_output=True, timeout=60,
        )

    def state(name):
        return json.loads((home / "threads" / (name + ".json")).read_text())

    def invocations():
        return [json.loads(line) for line in calls.read_text().splitlines()]

    def threads_snapshot():
        return {p.name: p.read_bytes() for p in (home / "threads").iterdir()}

    def success(name, workdir, turn, session=None):
        before_count = len(invocations()) if calls.exists() else 0
        result = dispatch(name, workdir)
        check(result.returncode == 0, f"turn {turn} failed: {result.stdout}\n{result.stderr}")
        saved = state(name)
        check(saved["workdir"] == str(physical), "state did not pin physical workdir")
        check((saved["vendor"], saved["model"], saved["effort"], saved["turns"]) ==
              ("claude", "fake-pinned-model", "low", turn), "binding/turn state changed")
        records = invocations()
        check(len(records) == before_count + 1, "success did not invoke exactly one executor")
        check(records[-1]["cwd"] == str(physical), "executor cwd was not physical workspace")
        args = records[-1]["argv"]
        mode = "resume" if session else "new"
        flag = "--resume" if session else "--session-id"
        check(flag in args, f"executor omitted {flag}")
        check(args[args.index(flag) + 1] == saved["session_id"], "executor session differs")
        check(("--session-id" if session else "--resume") not in args, "wrong session mode")
        if session:
            check(saved["session_id"] == session, "alias silently started another session")
        saved_session = saved["session_id"]
        check(f"thread {name} turn {turn} (claude session {saved_session}, {mode})" in
              result.stdout + result.stderr, "missing thread mode visibility")
        return saved["session_id"]

    session = success("matrix", physical, 1)
    # Each case changes exactly one binding, with no --dry-run shortcut.
    cases = [
        ("vendor", "claude", "codex", physical, {"vendor": "codex"}),
        ("model", "fake-pinned-model", "fake-other-model", physical,
         {"model": "fake-other-model"}),
        ("effort", "low", "high", physical, {"effort": "high"}),
        ("workdir", str(physical), str(other), other, {}),
        ("workdir", str(physical), str(other), other_alias, {}),
    ]
    for field, pinned, resolved, workdir, overrides in cases:
        before_state = threads_snapshot()
        before_calls = calls.read_bytes()
        result = dispatch("matrix", workdir, **overrides)
        check(result.returncode == 2, f"{field}: expected exit 2, got {result.returncode}")
        qualifier = "effective" if field == "workdir" else "resolved"
        quote = chr(39)
        expected = (f"pinned {field} {quote}{pinned}{quote} but "
                    f"{qualifier} {field} is {quote}{resolved}{quote}")
        for stream in (result.stdout, result.stderr):
            check(expected in stream, f"{field}: both values not identifiable: {stream!r}")
        check(threads_snapshot() == before_state, f"{field}: refusal changed thread state")
        check(calls.read_bytes() == before_calls, f"{field}: refusal launched an executor")
        print(f"ok - {field} mismatch: exit 2, both values, unchanged state, no executor", flush=True)

    success("matrix", alias, 2, session)
    success("matrix", physical, 3, session)
    print("ok - physical -> spaced symlink -> physical resumes the same session", flush=True)
    alias_session = success("alias-first", alias, 1)
    success("alias-first", physical, 2, alias_session)
    print("ok - spaced symlink creation pins physical workdir and resumes via physical path", flush=True)

print("ok - C2-B04 four bindings and physical-path aliases")
' "$ROOT"
