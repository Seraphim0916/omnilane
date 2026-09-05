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

wait_for_text() {
  local path="$1" needle="$2" tries=0
  while [[ "$tries" -lt 200 ]]; do
    if [[ -f "$path" ]] && grep -Fq "$needle" "$path"; then
      return 0
    fi
    sleep 0.1
    tries=$((tries + 1))
  done
  return 1
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

make_stalling_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
IFS= read -r _initial
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"partial work before the watchdog"}]}}\n'
sleep 30
EOF
  chmod +x "$path"
}

test_timed_out_live_run_still_normalizes() {
  local home="$TEST_ROOT/watchdog" bin="$TEST_ROOT/watchdog/bin" fake
  local job job_dir recorded_exit
  fake="$bin/claude"
  mkdir -p "$home" "$bin"
  make_stalling_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --timeout 3 \
    --idle-timeout 30 --vendor claude triage "stall past the watchdog")"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/exit" || fail "watchdog-killed live job never finished"
  recorded_exit="$(cat "$job_dir/exit")"
  # The watchdog code depends on which implementation ran (124 from timeout(1),
  # 142 from the perl alarm fallback); the finding is that the transcript
  # survives either way, because no trap fires on this path.
  [[ "$recorded_exit" != "0" ]] || fail "watchdog-killed live job reported success"
  [[ -f "$job_dir/out.txt" ]] || fail "watchdog-killed live job produced no out.txt"
  grep -Fq 'partial work before the watchdog' "$job_dir/out.txt" \
    || fail "watchdog-killed live job lost its stream transcript"
  printf 'ok - watchdog timeout still normalizes the live stream into out.txt\n'
}

make_validator_probe() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/bin/bash
# shellcheck disable=SC1090
source "$1"
status=0
live_event_is_valid '{"type":"assistant","message":{"role":"assistant"}}' || status=3
live_event_is_valid '   {"type":"user"}   ' || status=4
if live_event_is_valid 'not json at all'; then status=5; fi
if live_event_is_valid '{"unterminated": 1'; then status=6; fi
if live_event_is_valid ''; then status=7; fi
exit "$status"
EOF
  chmod +x "$path"
}

test_event_validator_without_strict_parser() {
  local probe="$TEST_ROOT/validator-probe.sh" out rc=0
  make_validator_probe "$probe"
  # An empty PATH removes perl and every other helper: a validator that shells
  # out would judge live events invalid here and let the idle cap kill healthy
  # jobs. /bin/bash also runs the check under bash 3.2, the worker interpreter.
  out="$(PATH=/nonexistent /bin/bash "$probe" "$ROOT/scripts/lib/live-protocol.sh" 2>&1)" || rc=$?
  [[ "$rc" -eq 0 ]] \
    || fail "event validator misjudged events without a strict parser (rc=$rc: $out)"
  printf 'ok - event validator reports activity with no external parser on PATH\n'
}

make_stdin_bound_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
IFS= read -r _initial
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"let me read the file first"}]}}\n'
while IFS= read -r _line; do :; done
EOF
  chmod +x "$path"
}

test_operator_close_reports_aborted_turn() {
  local home="$TEST_ROOT/abort" bin="$TEST_ROOT/abort/bin" fake
  local job job_dir close_rc=0
  fake="$bin/claude"
  mkdir -p "$home" "$bin"
  make_stdin_bound_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --timeout 45 \
    --idle-timeout 45 --vendor claude triage "abort me mid-turn")"
  job_dir="$home/jobs/$job"
  wait_for_text "$job_dir/events.jsonl" 'let me read the file first' \
    || fail "live job never streamed its partial transcript"
  OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" >/dev/null 2>&1 || close_rc=$?
  [[ "$close_rc" -ne 0 ]] || fail "operator close reported success for an aborted turn"
  wait_for_file "$job_dir/exit" || fail "closed live job never recorded an exit"
  [[ "$(cat "$job_dir/exit")" != "0" ]] || fail "aborted turn recorded a zero exit"
  grep -Fq 'let me read the file first' "$job_dir/out.txt" \
    || fail "operator close discarded the recovered transcript"
  ! grep -Fq 'closed by idle cap' "$job_dir/out.txt" \
    || fail "operator close was mislabelled as an idle cap close"
  printf 'ok - operator close keeps the transcript but still reports the abort\n'
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
  # The exec vendor cannot run live, so this dispatch is single-shot and the
  # idle cap never applies to it.
  ! grep -Fq 'idle cap remains' "$stderr" \
    || fail "single-shot dispatch printed the live-only idle cap diagnostic"
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
  printf 'ok - single-shot dispatch omits the idle diagnostic and records worker provenance\n'
}

make_result_only_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
IFS= read -r _initial
printf '{"type":"result","subtype":"success","is_error":false,"result":"live diagnostic ok"}\n'
EOF
  chmod +x "$path"
}

test_timeout_diagnostic_on_live_dispatch() {
  local home="$TEST_ROOT/live-diagnostic" bin="$TEST_ROOT/live-diagnostic/bin" fake
  local stderr="$TEST_ROOT/live-diagnostic.stderr" job job_dir
  fake="$bin/claude"
  mkdir -p "$home" "$bin"
  make_result_only_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --timeout 1200 \
    --idle-timeout 900 --vendor claude triage "diagnostic surface" 2> "$stderr")"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/exit" || fail "live diagnostic job never finished"
  grep -Fq 'idle cap remains 900s' "$stderr" || fail "live dispatch lost the idle cap diagnostic"
  grep -Fq -- '--idle-timeout' "$stderr" || fail "live diagnostic missing adjustment flag"
  printf 'ok - live dispatch still warns that the idle cap is independent of --timeout\n'
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
test_timed_out_live_run_still_normalizes
test_event_validator_without_strict_parser
test_operator_close_reports_aborted_turn
test_json_escape_and_round_trip_guard
test_timeout_diagnostic_and_worker_metadata
test_timeout_diagnostic_on_live_dispatch
test_lock_timeout_hint
