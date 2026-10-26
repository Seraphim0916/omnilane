#!/usr/bin/env bash

# Only an explicit child invocation from an isolated parent keeps fixture state.
# A stale environment marker alone must never bypass standalone isolation.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT OMNILANE_LOCK_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-completion-idle.XXXXXX")"
REAL_SAMPLE="${OMNILANE_REAL_EVENTS_SAMPLE:-$HOME/.omnilane/jobs/20260903-095954-85706-7650/events.jsonl}"
PIDS_FILE="$TEST_ROOT/pids"
: > "$PIDS_FILE"
export COMPLETION_TEST_PIDS="$PIDS_FILE"
CHILD_PIDS=()

cleanup() {
  local rc=$? directory pid tries alive=0
  trap - EXIT INT TERM
  set +eu
  # Background dispatch detaches its worker. Cancel via its supervisor first,
  # which terminates and reaps the runner tree, then wait for recorded PIDs.
  # Scan only this test's job stores, including a dispatch interrupted before
  # track_job returned. The PID files are authoritative, not a ps snapshot.
  for directory in "$TEST_ROOT"/*/jobs/*; do
    [[ -f "$directory/pid" ]] || continue
    pid="$(cat "$directory/pid")"
    if ! grep -Fxq "$pid" "$PIDS_FILE"; then
      printf '%s\n' "$pid" >> "$PIDS_FILE"
    fi
    OMNILANE_HOME="${directory%/jobs/*}" "$ROOT/scripts/jobs.sh" cancel \
      "${directory##*/}" >/dev/null 2>&1
  done
  while IFS= read -r pid; do
    kill -TERM "$pid" 2>/dev/null
  done < "$PIDS_FILE"
  for pid in "${CHILD_PIDS[@]}"; do wait "$pid" 2>/dev/null; done
  tries=0
  while [[ "$tries" -lt 200 ]]; do
    alive=0
    while IFS= read -r pid; do
      if kill -0 "$pid" 2>/dev/null; then alive=$((alive + 1)); fi
    done < "$PIDS_FILE"
    [[ "$alive" -ne 0 ]] || break
    sleep 0.1
    tries=$((tries + 1))
  done
  if [[ "$alive" -ne 0 ]]; then
    printf 'not ok - cleanup left %s recorded processes; test directory deliberately retained: %s\n' "$alive" "$TEST_ROOT" >&2
    exit 1
  fi
  if [[ -n "${CLEANUP_AUDIT:-}" ]]; then
    cp "$PIDS_FILE" "$CLEANUP_AUDIT"
    printf '%s\n' "$TEST_ROOT" > "$CLEANUP_AUDIT.root"
  fi
  /bin/rm -rf -- "$TEST_ROOT"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

track_job() {
  local directory="$1"
  wait_for_file "$directory/pid" || fail "background worker PID missing"
  printf '%s\n' "$(cat "$directory/pid")" >> "$PIDS_FILE"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

wait_for_file() {
  local path="$1" limit="${2:-150}" tries=0
  while [[ ! -f "$path" && "$tries" -lt "$limit" ]]; do
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

# Execute the production event drain and idle decision with a fixed clock.
# Only clock references change; copying the decision into the test would mask
# a regression in job-worker.sh. Fail closed if the extraction anchors drift.
test_activity_refresh_with_fixed_clock() (
  trap - EXIT INT TERM
  local fragment="$TEST_ROOT/activity.sh" event_file="$TEST_ROOT/activity.jsonl"
  python3 -c '
import pathlib, sys
source = pathlib.Path(sys.argv[1]).read_text()
start = "  while IFS= read -r event <&5; do\n"
end = "\ndone\n\nif [[ \"$forward_spool_open\""
assert source.count(start) == source.count(end) == 1
fragment = source.split(start, 1)[1].split(end, 1)[0]
assert "closed by idle cap" in fragment
pathlib.Path(sys.argv[2]).write_text((start + fragment).replace("SECONDS", "test_now") + "\n")
' "$ROOT/scripts/lib/job-worker.sh" "$fragment"
  source "$ROOT/scripts/lib/live-protocol.sh"
  local VENDOR=claude IDLE_TIMEOUT=5 last_activity=0 test_now=0
  local close_requested=0 close_reason="" last_result_event="" event
  # Each valid event arrives at the idle boundary. Ten virtual seconds pass
  # without any input-side refresh, regardless of host scheduling delays.
  for test_now in 5 10; do
    printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[]}}' > "$event_file"
    source "$fragment" 5< "$event_file"
    [[ "$last_activity" -eq "$test_now" && "$close_requested" -eq 0 ]] \
      || fail "valid events did not refresh activity"
  done
  test_now=14
  printf '%s\n' 'not json' > "$event_file"
  source "$fragment" 5< "$event_file"
  [[ "$last_activity" -eq 10 && "$close_requested" -eq 0 ]] \
    || fail "invalid event refreshed activity or idle cap fired early"
  test_now=15
  source "$fragment" 5< "$event_file"
  [[ "$close_requested" -eq 1 && "$close_reason" == 'closed by idle cap after 5s' ]] \
    || fail "idle cap did not close an inactive stream at its boundary"
  printf 'ok - production activity refresh and idle boundary use a fixed clock\n'
)

make_slow_claude() {
  local path="$1"
  printf '%s\n' '#!/usr/bin/env bash
set -euo pipefail
printf "%s\n" "$$" >> "$COMPLETION_TEST_PIDS"
IFS= read -r _initial
printf "%s\n" "{\"type\":\"system\",\"subtype\":\"init\"}"
: > "$COMPLETION_READY"
deadline=$((SECONDS + 60))
while [[ ! -f "$COMPLETION_RELEASE" && "$SECONDS" -lt "$deadline" ]]; do
  sleep 0.1
done
[[ -f "$COMPLETION_RELEASE" ]] || exit 91
printf "%s\n" "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"claude normalized ok\"}]}}"
' > "$path"
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
    COMPLETION_READY="$home/ready" COMPLETION_RELEASE="$home/release" \
    "$ROOT/scripts/dispatch.sh" --background --live --timeout 120 \
    --idle-timeout 90 --vendor claude triage "use streamed activity")"
  job_dir="$home/jobs/$job"
  track_job "$job_dir"
  wait_for_file "$home/ready" 600 || fail "event producer never became ready"
  if [[ -n "${CLEANUP_PROBE:-}" ]]; then
    # Also exercise wait(1) for a direct child, alongside detached job trees.
    sleep 120 &
    CHILD_PIDS+=("$!")
    printf '%s\n' "$!" >> "$PIDS_FILE"
    case "$CLEANUP_PROBE" in
      fail) fail "forced failure with a live producer" ;;
      INT|TERM) kill -s "$CLEANUP_PROBE" "$$" ;;
    esac
  fi
  : > "$home/release"
  wait_for_file "$job_dir/exit" 600 || fail "slow active live job never finished"
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
  printf 'ok - marker-synchronized live stream gains canonical result\n'
}

make_stalling_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$$" >> "$COMPLETION_TEST_PIDS"
IFS= read -r _initial
printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"partial work before the watchdog"}]}}\n'
exec sleep 30
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
  track_job "$job_dir"
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
printf '%s\n' "$$" >> "$COMPLETION_TEST_PIDS"
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
  track_job "$job_dir"
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

test_background_worker_uses_readonly_snapshot() {
  local fixture="$TEST_ROOT/snapshot-fixture" home="$TEST_ROOT/snapshot-home"
  local workdir="$TEST_ROOT/snapshot-work" gate="$TEST_ROOT/snapshot-gate.sh"
  local ready="$TEST_ROOT/snapshot.ready" release="$TEST_ROOT/snapshot.release"
  local job job_dir source_hash
  mkdir -p "$fixture/scripts/lib" "$fixture/scripts/runners" "$home" "$workdir"
  cp "$ROOT/scripts/dispatch.sh" "$fixture/scripts/dispatch.sh"
  # The snapshot needs dispatch's entire runtime library, including new helpers.
  cp -R "$ROOT/scripts/lib/." "$fixture/scripts/lib/"
  mkdir -p "$fixture/config"
  cp "$ROOT/config/aa-model-policy.json" "$fixture/config/aa-model-policy.json"
  cp "$ROOT/scripts/runners/run-exec.sh" "$fixture/scripts/runners/run-exec.sh"
  chmod +x "$fixture/scripts/dispatch.sh" "$fixture/scripts/lib/job-worker.sh" \
    "$fixture/scripts/runners/run-exec.sh" "$fixture/scripts/lib/job-timeout.pl"
  source_hash="$(shasum -a 256 "$fixture/scripts/lib/job-worker.sh" | awk '{print $1}')"

  cat > "$gate" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
: > "${SNAPSHOT_READY:?}"
printf '%s\n' "$$" >> "$COMPLETION_TEST_PIDS"
# Wait for the parent to mutate the source, even when it is heavily delayed.
deadline=$((SECONDS + 300))
while [[ ! -f "${SNAPSHOT_RELEASE:?}" && "$SECONDS" -lt "$deadline" ]]; do
  sleep 0.05
done
[[ -f "$SNAPSHOT_RELEASE" ]] || exit 91
printf 'snapshot worker ok\n' > "$5"
EOF
  chmod +x "$gate"
  printf 'probe: exec "%s" -\n' "$gate" > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" SNAPSHOT_READY="$ready" SNAPSHOT_RELEASE="$release" \
    "$fixture/scripts/dispatch.sh" --background --workdir "$workdir" \
    probe "snapshot mutation probe")"
  job_dir="$home/jobs/$job"
  track_job "$job_dir"
  wait_for_file "$ready" || fail "snapshot worker did not reach runner"
  printf '# source changed after snapshot\nexit 97\n' > "$fixture/scripts/lib/job-worker.sh"
  : > "$release"
  wait_for_file "$job_dir/exit" || fail "snapshot worker never finished"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "source mutation changed running snapshot result"
  grep -Fxq 'snapshot worker ok' "$job_dir/out.txt" || fail "snapshot worker output missing"

  META="$job_dir/meta.json" SNAPSHOT="$job_dir/job-worker.sh" \
    SOURCE_HASH="$source_hash" python3 - <<'PY' \
    || fail "background worker snapshot provenance mismatch"
import hashlib
import json
import os
import stat
from pathlib import Path

meta = json.loads(Path(os.environ["META"]).read_text(encoding="utf-8"))
snapshot = Path(os.environ["SNAPSHOT"])
snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
assert meta["job_worker_source_sha256"] == os.environ["SOURCE_HASH"], meta
assert meta["job_worker_sha256"] == snapshot_hash == os.environ["SOURCE_HASH"], meta
assert Path(meta["job_worker_path"]).resolve() == snapshot.resolve(), meta
assert not snapshot.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), snapshot.stat()
PY
  printf 'ok - background dispatch executes its recorded read-only worker snapshot\n'
}

test_worker_snapshot_rejects_sha_mismatch() {
  local out rc=0
  out="$(OMNILANE_JOB_WORKER_REPO="$ROOT" \
    OMNILANE_JOB_WORKER_EXPECTED_SHA256="$(printf '0%.0s' {1..64})" \
    /bin/bash "$ROOT/scripts/lib/job-worker.sh" 2>&1)" || rc=$?
  [[ "$rc" -eq 2 ]] || fail "worker snapshot SHA mismatch returned $rc"
  [[ "$out" == *'job worker snapshot SHA changed before startup'* ]] \
    || fail "worker snapshot SHA mismatch lacked a fail-closed diagnostic"
  printf 'ok - worker snapshot rejects a startup SHA mismatch\n'
}

make_result_only_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$$" >> "$COMPLETION_TEST_PIDS"
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
  track_job "$job_dir"
  wait_for_file "$job_dir/exit" || fail "live diagnostic job never finished"
  grep -Fq 'idle cap remains 900s' "$stderr" || fail "live dispatch lost the idle cap diagnostic"
  grep -Fq -- '--idle-timeout' "$stderr" || fail "live diagnostic missing adjustment flag"
  printf 'ok - live dispatch still warns that the idle cap is independent of --timeout\n'
}

test_lock_timeout_hint() {
  local home="$TEST_ROOT/lock-home" workdir="$TEST_ROOT/lock-work"
  local ready="$TEST_ROOT/lock.ready" release="$TEST_ROOT/lock.release" out rc=0 holder
  mkdir -p "$home" "$workdir"
  OMNILANE_HOME="$home" /bin/bash -c \
    'source "$1"
     acquire_cwd_lock codex "$2" || exit $?
     : > "$3"
     deadline=$((SECONDS + 300))
     while [[ ! -f "$4" && "$SECONDS" -lt "$deadline" ]]; do sleep 0.05; done
     [[ -f "$4" ]] || exit 92' \
    _ "$ROOT/scripts/lib/common.sh" "$workdir" "$ready" "$release" &
  holder=$!
  CHILD_PIDS+=("$holder")
  printf '%s\n' "$holder" >> "$PIDS_FILE"
  wait_for_file "$ready" || fail "lock holder did not acquire lock"
  out="$(OMNILANE_HOME="$home" OMNILANE_LOCK_TIMEOUT=1 /bin/bash -c \
    'source "$1"; acquire_cwd_lock codex "$2"' \
    _ "$ROOT/scripts/lib/common.sh" "$workdir" 2>&1)" || rc=$?
  # Release only after observing the competitor's result, never on a timer.
  : > "$release"
  [[ "$rc" -eq 87 ]] || fail "lock waiter did not exit 87"
  [[ "$out" == *'advise/read-only'* && "$out" == *'different --workdir'* ]] \
    || fail "lock timeout lacks read-only workdir hint"
  wait "$holder" || fail "lock holder timed out before the competitor result was observed"
  printf 'ok - lock timeout keeps serialization and suggests another read-only workdir\n'
}

test_cleanup_after_forced_exit() {
  local mode rc audit pid directory count
  for mode in fail INT TERM; do
    audit="$TEST_ROOT/cleanup-$mode.pids"
    rc=0
    /bin/bash "$0" --omnilane-offline-child --cleanup-probe "$mode" "$audit" \
      > "$TEST_ROOT/cleanup-$mode.out" 2>&1 || rc=$?
    case "$mode" in
      fail)
        [[ "$rc" -eq 1 ]] || fail "forced failure returned $rc"
        grep -Fq 'not ok - forced failure with a live producer' "$TEST_ROOT/cleanup-$mode.out" \
          || fail "cleanup probe failed before the forced failure"
        ;;
      INT) [[ "$rc" -eq 130 ]] || fail "INT cleanup returned $rc" ;;
      TERM) [[ "$rc" -eq 143 ]] || fail "TERM cleanup returned $rc" ;;
    esac
    [[ -s "$audit" ]] || fail "cleanup did not publish its PID list ($mode)"
    count=0
    while IFS= read -r pid; do
      ! kill -0 "$pid" 2>/dev/null || fail "recorded PID $pid survived $mode"
      count=$((count + 1))
    done < "$audit"
    [[ "$count" -ge 3 ]] || fail "cleanup probe missed worker, producer or direct child PID"
    directory="$(cat "$audit.root")"
    [[ ! -e "$directory" ]] || fail "cleanup left its directory ($mode)"
    printf 'ok - cleanup %s: recorded=%s remaining=0 directory_removed=yes\n' "$mode" "$count"
  done
  printf 'ok - cleanup removal verified for fail/INT/TERM probes; if processes survive cleanup, their test directory is retained and its path reported\n'
}

if [[ "${1:-}" == --cleanup-probe ]]; then
  CLEANUP_PROBE="$2"
  CLEANUP_AUDIT="$3"
  test_live_activity_and_clean_exit_normalization
  fail "cleanup probe unexpectedly returned"
fi

test_activity_refresh_with_fixed_clock
test_cleanup_after_forced_exit
test_real_stream_activity_and_terminal_classification
test_live_activity_and_clean_exit_normalization
test_timed_out_live_run_still_normalizes
test_event_validator_without_strict_parser
test_operator_close_reports_aborted_turn
test_json_escape_and_round_trip_guard
test_timeout_diagnostic_and_worker_metadata
test_background_worker_uses_readonly_snapshot
test_worker_snapshot_rejects_sha_mismatch
test_timeout_diagnostic_on_live_dispatch
test_lock_timeout_hint
