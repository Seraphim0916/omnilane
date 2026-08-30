#!/usr/bin/env bash
set -euo pipefail

# Internal worker boundary for one dispatch. An optional whole-job supervisor
# wraps this process so lock wait, retries, and vote rounds share one budget.

# Runtime-relative shared library.
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
# shellcheck disable=SC1091
source "$OMNILANE_REPO/scripts/lib/live-protocol.sh"

[[ $# -eq 7 ]] || { echo "omnilane: internal job worker received invalid arguments" >&2; exit 2; }
VENDOR="$1"; MODE="$2"; WORKDIR="$3"; MODEL="$4"; EFFORT="$5"
PROMPT_FILE="$6"; OUTPUT_FILE="$7"

[[ "$VENDOR" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid worker vendor" >&2; exit 2; }
RUNNER="$OMNILANE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omnilane: no runner for vendor '$VENDOR'" >&2; exit 2; }

# Two concurrent codex execs in one target dir corrupt its job index — serialize.
[[ "$VENDOR" == "codex" ]] && acquire_cwd_lock codex "$WORKDIR"

# The backslash case pattern is intentional.
# shellcheck disable=SC1003
json_escape() {
  local s="$1" out="" ch escaped code i
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:i:1}"
    case "$ch" in
      '"') out="$out\\\"" ;;
      '\\') out="$out\\\\" ;;
      $'\b') out="$out\\b" ;;
      $'\f') out="$out\\f" ;;
      $'\n') out="$out\\n" ;;
      $'\r') out="$out\\r" ;;
      $'\t') out="$out\\t" ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'$ch"
        if [[ "$code" -ge 0 && "$code" -lt 32 ]]; then
          printf -v escaped '\\u%04x' "$code"
          out="$out$escaped"
        else
          out="$out$ch"
        fi
        ;;
    esac
  done
  printf '%s' "$out"
}

emit_mode_notice() {
  local notice="$1" notice_file="${OUTPUT_FILE%/*}/mode-notice.txt"
  printf '%s\n' "$notice" >&2
  if [[ -L "$notice_file" || ( -e "$notice_file" && ! -f "$notice_file" ) ]]; then
    echo "omnilane: unsafe mode notice path" >&2
    return 1
  fi
  (umask 077; printf '%s\n' "$notice" > "$notice_file")
}

run_single_shot() {
  local notice="$1" rc
  set +e
  (
    unset OMNILANE_INBOX
    "$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE"
  )
  rc=$?
  set -e
  emit_mode_notice "$notice"
  return "$rc"
}

SESSION_MODE="${OMNILANE_SESSION_MODE:-auto}"
IDLE_TIMEOUT="${OMNILANE_IDLE_TIMEOUT:-900}"
LIVE_REQUIRED="${OMNILANE_LIVE_REQUIRED:-0}"
[[ "$SESSION_MODE" == "auto" || "$SESSION_MODE" == "live" || "$SESSION_MODE" == "single-shot" ]] || {
  echo "omnilane: invalid worker session mode" >&2; exit 2
}
[[ "$IDLE_TIMEOUT" =~ ^(0|[1-9][0-9]*)$ ]] || {
  echo "omnilane: invalid worker idle timeout" >&2; exit 2
}
[[ "$LIVE_REQUIRED" == "0" || "$LIVE_REQUIRED" == "1" ]] || {
  echo "omnilane: invalid worker live requirement" >&2; exit 2
}
if [[ "$SESSION_MODE" == "auto" ]]; then
  if live_vendor_capable "$VENDOR"; then SESSION_MODE="live"; else SESSION_MODE="single-shot"; fi
fi

if [[ "$SESSION_MODE" == "single-shot" ]]; then
  set +e
  if live_vendor_capable "$VENDOR"; then
    run_single_shot "omnilane: vendor '$VENDOR' was resolved to single-shot mode"
  else
    run_single_shot "omnilane: vendor '$VENDOR' is not live-capable; ran in single-shot mode"
  fi
  rc=$?
  set -e
  exit "$rc"
fi

if ! live_vendor_capable "$VENDOR"; then
  echo "omnilane: vendor '$VENDOR' cannot run required live session; live-capable vendors: $(live_capable_vendors)" >&2
  exit 2
fi

JOB_DIR="${OUTPUT_FILE%/*}"
JOB_ID="${JOB_DIR##*/}"
INBOX_FIFO="$JOB_DIR/inbox.fifo"
RUNNER_INBOX_FIFO="$JOB_DIR/runner-inbox.fifo"
HOLDER_PID_FILE="$JOB_DIR/inbox.holder.pid"
READY_FILE="$JOB_DIR/inbox.ready"
EVENTS_FILE="${OUTPUT_FILE}.events.jsonl"
EVENTS_ALIAS="$JOB_DIR/events.jsonl"
close_requested=0
close_reason=""
runner_writer_open=0
inbox_open=0
events_reader_open=0
runner_pid=""

# Invoked by the EXIT trap below.
# shellcheck disable=SC2329
cleanup_live_mailbox() {
  if [[ "$runner_writer_open" -eq 1 ]]; then exec 3>&-; runner_writer_open=0; fi
  if [[ "$inbox_open" -eq 1 ]]; then exec 4>&-; inbox_open=0; fi
  if [[ "$events_reader_open" -eq 1 ]]; then exec 5<&-; events_reader_open=0; fi
  rm "$READY_FILE" "$HOLDER_PID_FILE" "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" 2>/dev/null || true
}

prepare_live_mailbox() {
  local path old_umask rc=0
  for path in "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" "$HOLDER_PID_FILE" \
    "$READY_FILE" "$EVENTS_FILE" "$EVENTS_ALIAS"; do
    [[ ! -e "$path" && ! -L "$path" ]] || return 1
  done
  old_umask="$(umask)"
  umask 077
  mkfifo "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    : > "$EVENTS_FILE" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    ln "$EVENTS_FILE" "$EVENTS_ALIAS" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    chmod 600 "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" "$EVENTS_FILE" "$EVENTS_ALIAS" || rc=$?
  fi
  umask "$old_umask"
  if [[ "$rc" -ne 0 ]]; then
    rm "$EVENTS_ALIAS" "$EVENTS_FILE" "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" 2>/dev/null || true
  fi
  return "$rc"
}

last_result_status() {
  local event last_result=""
  while IFS= read -r event || [[ -n "$event" ]]; do
    if live_event_is_result "$VENDOR" "$event"; then last_result="$event"; fi
  done < "$EVENTS_FILE"
  [[ -n "$last_result" ]] || return 1
  live_event_is_success "$VENDOR" "$last_result"
}

if ! prepare_live_mailbox; then
  if [[ "$LIVE_REQUIRED" -eq 1 ]]; then
    echo "omnilane: required $VENDOR live mailbox unavailable because FIFO setup failed" >&2
    exit 1
  fi
  set +e
  run_single_shot "omnilane: $VENDOR live mailbox unavailable because FIFO setup failed; ran in single-shot mode"
  rc=$?
  set -e
  exit "$rc"
fi

trap 'close_requested=1' USR1
trap 'close_requested=1' PIPE
trap cleanup_live_mailbox EXIT
truncate_payload "$PROMPT_FILE" 102400
INITIAL_TEXT="$(cat "$PROMPT_FILE")"
if [[ "${FOREMAN_SESSION+x}" == "x" ]]; then
  FOREMAN_SESSION_VALUE="$FOREMAN_SESSION"
else
  FOREMAN_SESSION_VALUE="${foreman_session-}"
fi

write_current_pid_file "$HOLDER_PID_FILE"
export OMNILANE_INBOX="$RUNNER_INBOX_FIFO"
"$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE" &
runner_pid=$!

# Open the runner writer only after its FIFO reader starts.
exec 3> "$RUNNER_INBOX_FIFO"
runner_writer_open=1
exec 4<> "$INBOX_FIFO"
inbox_open=1
exec 5< "$EVENTS_FILE"
events_reader_open=1
initial_payload="$(live_encode_message "$VENDOR" "$JOB_ID" "$FOREMAN_SESSION_VALUE" "$INITIAL_TEXT")"
if ! printf '%s\n' "$initial_payload" >&3; then close_requested=1; fi
(umask 077; : > "$READY_FILE")

last_activity=$SECONDS
while kill -0 "$runner_pid" 2>/dev/null; do
  [[ "$close_requested" -eq 0 ]] || break
  incoming=""
  if IFS= read -r -t 1 incoming <&4; then
    if ! printf '%s\n' "$incoming" >&3; then close_requested=1; fi
    last_activity=$SECONDS
  fi
  while IFS= read -r event <&5; do
    if live_event_is_result "$VENDOR" "$event"; then
      last_activity=$SECONDS
    fi
  done
  if [[ "$IDLE_TIMEOUT" -gt 0 && $((SECONDS - last_activity)) -ge "$IDLE_TIMEOUT" ]]; then
    close_reason="closed by idle cap after ${IDLE_TIMEOUT}s"
    close_requested=1
  fi
done

if [[ "$runner_writer_open" -eq 1 ]]; then exec 3>&-; runner_writer_open=0; fi
if [[ "$inbox_open" -eq 1 ]]; then exec 4>&-; inbox_open=0; fi
if [[ "$events_reader_open" -eq 1 ]]; then exec 5<&-; events_reader_open=0; fi

set +e
if [[ "$close_requested" -eq 1 ]]; then
  kill -TERM "$runner_pid" 2>/dev/null || true
fi
wait "$runner_pid" 2>/dev/null
runner_rc=$?
set -e

if [[ -n "$close_reason" ]]; then
  printf '\n%s\n' "$close_reason" >> "$OUTPUT_FILE"
  emit_mode_notice "$close_reason" || true
fi

if [[ "$close_requested" -eq 1 ]]; then
  if last_result_status; then
    rc=0
  else
    rc=1
    emit_mode_notice "omnilane: $VENDOR live mailbox closed without a successful result event" || true
  fi
else
  rc="$runner_rc"
fi

trap - USR1 PIPE
exit "$rc"
