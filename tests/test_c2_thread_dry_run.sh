#!/usr/bin/env bash
# Exercise the real dispatch CLI with isolated tools and no provider access.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

python3 -c '
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def snapshot(root):
    # Include empty directories, file bytes, permissions and symlink targets.
    # Do not include access times, which reads themselves can change.
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        mode = path.lstat().st_mode
        content = (os.readlink(path) if stat.S_ISLNK(mode) else
                   path.read_bytes() if stat.S_ISREG(mode) else None)
        result[str(path.relative_to(root))] = (mode, content)
    return result


repo = Path(sys.argv[1])
with tempfile.TemporaryDirectory(prefix="c2-thread-dry-run-") as temporary:
    root = Path(temporary)
    home, work, binaries = [root / name for name in ("state", "work", "bin")]
    for directory in (home, work, binaries):
        directory.mkdir()
    (work / "existing.txt").write_text("workdir sentinel\n")
    (home / "routing.local.yaml").write_text("consult: claude fake-thread-model low\n")
    calls = root / "executor.calls"
    calls.write_text("")
    fake = binaries / "claude"
    fake.write_text("#!/bin/sh\nprintf x >> \"$C2_EXECUTOR_CALLS\"\nexit 91\n")
    fake.chmod(0o755)
    environment = dict(os.environ, OMNILANE_HOME=str(home),
                       PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                       C2_EXECUTOR_CALLS=str(calls))
    # Positive control proves that a call to the selected fake is observable.
    probe = subprocess.run([str(fake)], env=environment, check=False)
    require(probe.returncode == 91 and calls.read_bytes() == b"x",
            "executor call counter positive control failed")
    calls.write_text("")

    def preview(label, thread, model="fake-thread-model", refusal=False):
        before = snapshot(home), snapshot(work)
        count = len(calls.read_bytes())
        run = subprocess.run([
            str(repo / "scripts/dispatch.sh"), "--dry-run", "--thread", thread,
            "--vendor", "claude", "--model", model, "--effort", "low",
            "--workdir", str(work), "--single-shot", "consult", "preview only",
        ], env=environment, cwd=work, capture_output=True, text=True, timeout=60)
        require((snapshot(home), snapshot(work)) == before,
                label + ": state/work directory listing or file content changed")
        require(len(calls.read_bytes()) == count == 0,
                label + ": fake executor was invoked")
        details = run.stdout + run.stderr
        if refusal:
            require(run.returncode != 0 and "pinned model" in details,
                    label + ": expected binding refusal: " + details)
        else:
            require(run.returncode == 0, label + ": dispatch failed: " + details)
        print("ok - " + label + ": unchanged state/work snapshots; executor calls=0")
        return run.stdout.splitlines()

    output = preview("new thread", "new-preview")
    for field in ("thread=new-preview", "thread_mode=new", "thread_turn=1"):
        require(field in output, "new thread: missing plan field " + field)
    require(not (home / "threads").exists(), "new thread store was created")
    require(not (home / "jobs").exists(), "new job store was created")

    threads = home / "threads"
    threads.mkdir(mode=0o700)
    state = threads / "existing.json"
    session = "11111111-2222-4333-8444-555555555555"
    state.write_text(json.dumps(dict(
        name="existing", vendor="claude", model="fake-thread-model", effort="low",
        workdir=str(work.resolve()), session_id=session, turns=7,
        last_job_id="20260101-000000-123-456", created="2026-01-01T00:00:00Z",
        updated="2026-01-02T00:00:00Z")) + "\n")
    state.chmod(0o600)
    previous = home / "jobs" / "20260101-000000-123-456"
    previous.mkdir(parents=True)
    (previous / "sentinel.txt").write_text("existing job must survive unchanged\n")

    output = preview("existing thread", "existing")
    for field in ("thread=existing", "thread_mode=resume", "thread_turn=8",
                  "thread_session=" + session):
        require(field in output, "existing thread: missing plan field " + field)
    preview("binding mismatch", "existing", model="other-model", refusal=True)
    print("PASS - C2-B05: all three dry-run thread contracts verified")
' "$ROOT"
