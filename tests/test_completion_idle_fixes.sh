#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT OMNILANE_LOCK_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-completion-idle.XXXXXX")"
REAL_SAMPLE="${OMNILANE_REAL_EVENTS_SAMPLE:-$HOME/.omnilane/jobs/20260903-095954-85706-7650/events.jsonl}"

cleanup() {
  /bin/rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

wait_for_file() {
  local path="$1" tries=0
  while [[ ! -f "$path" && "$tries" -lt 150 ]]; do
    sleep 0.1
    tries=$((tries + 1))
  done
  [[ -f "$path" ]]
}

test_real_stream_activity_and_terminal_classification() {
  [[ -f "$REAL_SAMPLE" ]] || {
    printf 'ok - real Claude stream sample unavailable; portable integration still runs\n'
    return 0
  }

  SAMPLE="$REAL_SAMPLE" python3 - <<'PY' || fail "real event sample activity simulation"
import datetime as dt
import json
import os
from pathlib import Path

path = Path(os.environ["SAMPLE"])
events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
assert events and all(isinstance(event, dict) for event in events)
assert not any(event.get("type") == "result" for event in events)
timestamps = []
for event in events:
    value = event.get("timestamp")
    if isinstance(value, str):
        timestamps.append(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
assert timestamps
gaps = [(right - left).total_seconds() for left, right in zip(timestamps, timestamps[1:])]
assert max(gaps, default=0) < 900
last = events[-1]
message = last.get("message") if isinstance(last.get("message"), dict) else {}
content = message.get("content") if isinstance(message.get("content"), list) else []
content_types = [item.get("type") for item in content if isinstance(item, dict)]
assert last.get("type") == "user" and "tool_result" in content_types
print(
    "sample_activity valid=%d result=0 max_gap=%.3fs idle_kills=0 terminal=incomplete"
    % (len(events), max(gaps, default=0))
)
PY
  printf 'ok - real Claude stream refreshes activity but has no successful terminal event\n'
}

make_slow_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
IFS= read -r _initial
printf '{"type":"system","subtype":"init"}\n'
sleep 1
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"working"}]}}\n'
sleep 1
printf '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"step"}]}}\n'
sleep 1
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"still working"}]}}\n'
sleep 1
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"claude normalized ok"}]}}\n'
EOF
  chmod +x "$path"
}

test_live_activity_and_clean_exit_normalization() {
  local home="$TEST_ROOT/live" bin="$TEST_ROOT/live/bin" fake
  local job job_dir
  fake="$bin/claude"
  mkdir -p "$home" "$bin"
  make_slow_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --timeout 30 \
    --idle-timeout 2 --vendor claude triage "use streamed activity")"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/exit" || fail "slow active live job never finished"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "slow active live job was idle-killed or failed"
  ! grep -Fq 'closed by idle cap' "$job_dir/out.txt" || fail "valid events did not refresh activity"
  grep -Fq 'claude normalized ok' "$job_dir/out.txt" || fail "clean Claude stream was not normalized"
  EVENTS="$job_dir/events.jsonl" python3 - <<'PY' || fail "canonical Claude result event missing"
import json
import os
from pathlib import Path

events = [json.loads(line) for line in Path(os.environ["EVENTS"]).read_text().splitlines()]
results = [event for event in events if event.get("type") == "result"]
assert len(results) == 1
assert results[0].get("is_error") is False
assert results[0].get("result") == "claude normalized ok"
assert results[0].get("normalized_by") == "omnilane"
print("normalized_result count=1 success=true")
PY
  printf 'ok - live activity survives idle cap and clean exit gains canonical result\n'
}

test_json_escape_and_round_trip_guard() {
  local valid="$TEST_ROOT/valid.json" bad="$TEST_ROOT/bad.json" err="$TEST_ROOT/bad.err"
  local payload
  # shellcheck disable=SC1091
  source "$ROOT/scripts/lib/common.sh"
  payload='sandbox_mode=\"read-only\" path=C:\tmp\one'
  printf '{"value":"%s"}\n' "$(json_escape "$payload")" > "$valid"
  python3 -m json.tool "$valid" >/dev/null || fail "json_escape output did not parse"

  printf '{"value":"sandbox_mode="broken"}"\n' > "$bad"
  if json_file_round_trip_valid "$bad" 2> "$err"; then
    fail "invalid JSON passed producer round-trip guard"
  fi
  grep -Fq 'JSON round-trip validation failed' "$err" || fail "invalid JSON guard had no diagnostic"
  printf 'ok - backslash escaping parses and invalid JSON fails at producer guard\n'
}

make_gate() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'gate ok\n' > "$5"
EOF
  chmod +x "$path"
}

test_timeout_diagnostic_and_worker_metadata() {
  local home="$TEST_ROOT/meta" workdir="$TEST_ROOT/meta-work" gate="$TEST_ROOT/meta-gate.sh"
  local stderr="$TEST_ROOT/meta.stderr" job_dir
  mkdir -p "$home" "$workdir"
  make_gate "$gate"
  printf 'probe: exec "%s" -\n' "$gate" > "$home/routing.local.yaml"

  OMNILANE_HOME="$home" "$ROOT/scripts/dispatch.sh" --timeout 1200 \
    --idle-timeout 900 --workdir "$workdir" probe "echo hi" \
    > "$TEST_ROOT/meta.stdout" 2> "$stderr" || fail "metadata probe dispatch failed"
  grep -Fq 'idle cap remains 900s' "$stderr" || fail "timeout/idle diagnostic missing cap"
  grep -Fq -- '--idle-timeout' "$stderr" || fail "timeout/idle diagnostic missing adjustment flag"
  job_dir="$(find "$home/jobs" -mindepth 1 -maxdepth 1 -type d -print | head -1)"
  [[ -n "$job_dir" ]] || fail "metadata probe job missing"

  META="$job_dir/meta.json" WORKER="$ROOT/scripts/lib/job-worker.sh" python3 - <<'PY' \
    || fail "worker interpreter metadata mismatch"
import hashlib
import json
import os
import subprocess
from pathlib import Path

meta = json.loads(Path(os.environ["META"]).read_text(encoding="utf-8"))
assert meta["worker_interpreter_path"] == "/bin/bash"
expected_version = subprocess.check_output(["/bin/bash", "-c", 'printf %s "$BASH_VERSION"'], text=True)
assert meta["worker_interpreter_version"] == expected_version
expected_hash = hashlib.sha256(Path(os.environ["WORKER"]).read_bytes()).hexdigest()
assert meta["job_worker_sha256"] == expected_hash
print(
    "worker_interpreter_path=%s worker_interpreter_version=%s job_worker_sha256=%s"
    % (meta["worker_interpreter_path"], meta["worker_interpreter_version"], meta["job_worker_sha256"])
)
PY
  printf 'ok - timeout diagnostic and worker provenance metadata\n'
}

test_lock_timeout_hint() {
  local home="$TEST_ROOT/lock-home" workdir="$TEST_ROOT/lock-work"
  local ready="$TEST_ROOT/lock.ready" out rc=0 holder
  mkdir -p "$home" "$workdir"
  OMNILANE_HOME="$home" /bin/bash -c \
    'source "$1"; acquire_cwd_lock codex "$2"; : > "$3"; sleep 4' \
    _ "$ROOT/scripts/lib/common.sh" "$workdir" "$ready" &
  holder=$!
  wait_for_file "$ready" || fail "lock holder did not acquire lock"
  out="$(OMNILANE_HOME="$home" OMNILANE_LOCK_TIMEOUT=1 /bin/bash -c \
    'source "$1"; acquire_cwd_lock codex "$2"' \
    _ "$ROOT/scripts/lib/common.sh" "$workdir" 2>&1)" || rc=$?
  [[ "$rc" -eq 87 ]] || fail "lock waiter did not exit 87"
  [[ "$out" == *'advise/read-only'* && "$out" == *'different --workdir'* ]] \
    || fail "lock timeout lacks read-only workdir hint"
  wait "$holder"
  printf 'ok - lock timeout keeps serialization and suggests another read-only workdir\n'
}

test_real_stream_activity_and_terminal_classification
test_live_activity_and_clean_exit_normalization
test_json_escape_and_round_trip_guard
test_timeout_diagnostic_and_worker_metadata
test_lock_timeout_hint
