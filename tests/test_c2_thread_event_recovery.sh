#!/usr/bin/env bash

# Exercise dispatch and state persistence with synthetic provider output only.
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
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
failures = []
fake = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
with open(os.environ["EVENT_ARGV"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")
if "-o" in args:
    Path(args[args.index("-o") + 1]).write_text("synthetic answer\\n")
else:
    # Satisfy answer rendering independently of resumable-id extraction.
    print(json.dumps({"type": "result", "result": "synthetic answer"}))
sys.stdout.write(Path(os.environ["EVENT_INPUT"]).read_text())
"""

with tempfile.TemporaryDirectory(prefix="c2-event-recovery-") as temporary:
    base = Path(temporary)
    binary = base / "bin"
    binary.mkdir()
    for vendor in ("claude", "codex"):
        executable = binary / vendor
        executable.write_text(fake)
        executable.chmod(0o700)

    for vendor in ("claude", "codex"):
        home = base / vendor
        home.mkdir()
        (home / "routing.local.yaml").write_text(
            f"consult: {vendor} synthetic-model low\n")
        events = home / "input.jsonl"
        argv = home / "argv.jsonl"
        state = home / "threads" / "recovery.json"
        env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                   OMNILANE_HOME=str(home), EVENT_INPUT=str(events),
                   EVENT_ARGV=str(argv), CLAUDE_CODE_SESSION_ID="synthetic-foreman")
        key = "session_id" if vendor == "claude" else "thread_id"
        event_type = "result" if vendor == "claude" else "thread.started"

        def event(value, kind=event_type):
            return json.dumps({"type": kind, key: value}) + "\n"

        # Preserve the existing identifier grammar, including its length limits.
        invalid = "provider noise\n{broken json}\n"
        nonobjects = "\n".join(json.dumps(x) for x in
                                [None, True, 42, "text", [], ["objectless"]]) + "\n"
        invalid += "{}\n"
        for value in (None, True, [], {}, "", "bad id", "bad/id", "bad\nid",
                      "引號", "x" * 257):
            invalid += event(value)
        truncated = "{\"type\":\"" + event_type + "\",\"" + key + "\":\"unfinished"
        first = "a"
        selected = "Z._:-" + "b" * 251  # Exactly 256 permitted characters.
        next_id = "synthetic-next:2"
        mixed = invalid + event(first)
        if vendor == "claude":
            mixed += event(selected)
            mixed += event("must-ignore-non-result", "system")
            mixed += json.dumps({key: "must-ignore-missing-type"}) + "\n"
        else:
            # Codex intentionally has no event-type filter in the current contract.
            mixed += event("intermediate", "other.event")
            mixed += json.dumps({key: selected}) + "\n"
        mixed += invalid + truncated

        def run(payload, success, expected_resume=None):
            events.write_text(payload)
            result = subprocess.run(
                [str(root / "scripts" / "dispatch.sh"), "--thread", "recovery",
                 "--vendor", vendor, "--model", "synthetic-model", "--effort", "low",
                 "--workdir", str(root), "--single-shot", "consult", "synthetic turn"],
                env=env, cwd=root, text=True, capture_output=True, timeout=60)
            visible = result.stdout + result.stderr
            assert (result.returncode == 0) == success, (
                vendor, result.returncode, visible[-2000:])
            if not success:
                assert f"did not return a resumable {vendor} session id" in visible, visible
            calls = [json.loads(line) for line in argv.read_text().splitlines()]
            args = calls[-1]
            if expected_resume is not None:
                if vendor == "claude":
                    assert args[args.index("--resume") + 1] == expected_resume, args
                else:
                    assert args[:2] == ["exec", "resume"], args
                    assert expected_resume in args, args
            return calls

        run(invalid + truncated, False)
        assert not state.exists(), "invalid initial events created thread state"
        print(f"ok - {vendor}: no legal initial id fails visibly without state")

        run(mixed, True)
        saved = json.loads(state.read_text())
        assert saved["session_id"] == selected and saved["turns"] == 1, saved
        calls = run(event(next_id) + invalid + truncated, True, selected)
        assert len(calls) == 3, calls
        saved = json.loads(state.read_text())
        assert saved["session_id"] == next_id and saved["turns"] == 2, saved
        print(f"ok - {vendor}: last legal id selected and received by second turn")

        previous = state.read_bytes()
        for label, payload in (("mixed invalid", invalid + truncated),
                               ("truncated only", truncated), ("empty", "")):
            run(payload, False, next_id)
            assert state.read_bytes() == previous, f"{vendor}: {label} changed prior state"
            print(f"ok - {vendor}: {label} fails visibly and preserves prior state")

        run(event(first), True, next_id)
        saved = json.loads(state.read_text())
        assert saved["session_id"] == first and saved["turns"] == 3, saved
        assert saved["vendor"] == vendor and saved["workdir"] == str(root), saved
        print(f"ok - {vendor}: recovery resumes preserved id and advances one turn")

        # Keep this oracle after the other cases so an upstream runner defect
        # does not hide coverage of id selection, failures, or the other vendor.
        previous = state.read_bytes()
        try:
            run(nonobjects + mixed, True, first)
            saved = json.loads(state.read_text())
            assert saved["session_id"] == selected and saved["turns"] == 4, saved
            run(event(next_id), True, selected)
            saved = json.loads(state.read_text())
            assert saved["session_id"] == next_id and saved["turns"] == 5, saved
            print(f"ok - {vendor}: nonobjects ignored and selected id resumed")
        except AssertionError as error:
            failures.append(vendor)
            print(f"not ok - {vendor}: nonobject event recovery: {error}", flush=True)
            assert state.read_bytes() == previous, "failed recovery corrupted state"

if failures:
    print("not ok - C2-B06 failed vendors: " + ", ".join(failures), flush=True)
    raise SystemExit(1)
print("ok - C2-B06 synthetic thread event recovery")
' "$ROOT"
