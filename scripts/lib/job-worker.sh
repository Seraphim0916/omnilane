#!/usr/bin/env bash
set -euo pipefail

# Internal worker boundary for one dispatch. An optional whole-job supervisor
# wraps this process so lock wait, retries, and vote rounds share one budget.

# Runtime-relative shared library.
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

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

if [[ "$VENDOR" != "claude" ]]; then
  set +e
  run_single_shot "omnilane: vendor '$VENDOR' is not live-capable; ran in single-shot mode"
  rc=$?
  set -e
  exit "$rc"
fi

JOB_DIR="${OUTPUT_FILE%/*}"
JOB_ID="${JOB_DIR##*/}"
INBOX_FIFO="$JOB_DIR/inbox.fifo"
HOLDER_PID_FILE="$JOB_DIR/inbox.holder.pid"
READY_FILE="$JOB_DIR/inbox.ready"
EVENTS_FILE="${OUTPUT_FILE}.events.jsonl"
EVENTS_ALIAS="$JOB_DIR/events.jsonl"
close_requested=0
inbox_holder_open=0
runner_pid=""

# Invoked by the EXIT trap below.
# shellcheck disable=SC2329
cleanup_live_mailbox() {
  if [[ "$inbox_holder_open" -eq 1 ]]; then
    exec 3>&-
    inbox_holder_open=0
  fi
  rm "$READY_FILE" "$HOLDER_PID_FILE" "$INBOX_FIFO" 2>/dev/null || true
}

prepare_live_mailbox() {
  local path old_umask rc=0
  for path in "$INBOX_FIFO" "$HOLDER_PID_FILE" "$READY_FILE" "$EVENTS_FILE" "$EVENTS_ALIAS"; do
    [[ ! -e "$path" && ! -L "$path" ]] || return 1
  done
  old_umask="$(umask)"
  umask 077
  mkfifo "$INBOX_FIFO" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    : > "$EVENTS_FILE" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    ln "$EVENTS_FILE" "$EVENTS_ALIAS" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    chmod 600 "$INBOX_FIFO" "$EVENTS_FILE" "$EVENTS_ALIAS" || rc=$?
  fi
  umask "$old_umask"
  if [[ "$rc" -ne 0 ]]; then
    rm "$EVENTS_ALIAS" "$EVENTS_FILE" "$INBOX_FIFO" 2>/dev/null || true
  fi
  return "$rc"
}

last_result_status() {
  local last_result
  last_result="$(grep -E '"type"[[:space:]]*:[[:space:]]*"result"' "$EVENTS_FILE" 2>/dev/null | tail -n 1 || true)"
  [[ -n "$last_result" ]] || return 1
  [[ ! "$last_result" =~ "is_error"[[:space:]]*:[[:space:]]*true ]]
}

if ! prepare_live_mailbox; then
  set +e
  run_single_shot "omnilane: Claude live mailbox unavailable because FIFO setup failed; ran in single-shot mode"
  rc=$?
  set -e
  exit "$rc"
fi

trap 'close_requested=1' USR1
trap cleanup_live_mailbox EXIT
truncate_payload "$PROMPT_FILE" 102400
INITIAL_TEXT="$(cat "$PROMPT_FILE")"
if [[ "${FOREMAN_SESSION+x}" == "x" ]]; then
  FOREMAN_SESSION_VALUE="$FOREMAN_SESSION"
else
  FOREMAN_SESSION_VALUE="${foreman_session-}"
fi

write_current_pid_file "$HOLDER_PID_FILE"
export OMNILANE_INBOX="$INBOX_FIFO"
"$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE" &
runner_pid=$!

# Open only after the reader starts. The runner was launched first, so it cannot
# inherit this write descriptor. FD 3 is the named holder for the conversation.
exec 3> "$INBOX_FIFO"
inbox_holder_open=1
if [[ "$close_requested" -eq 0 ]]; then
  printf '{"type":"user","omnilane_job_id":"%s","foreman_session":"%s","message":{"role":"user","content":[{"type":"text","text":"%s"}]}}\n' \
    "$(json_escape "$JOB_ID")" "$(json_escape "$FOREMAN_SESSION_VALUE")" \
    "$(json_escape "$INITIAL_TEXT")" >&3
fi
(umask 077; : > "$READY_FILE")

set +e
wait "$runner_pid"
runner_rc=$?
set -e

if [[ "$close_requested" -eq 1 ]]; then
  exec 3>&-
  inbox_holder_open=0
  set +e
  kill -TERM "$runner_pid" 2>/dev/null || true
  wait "$runner_pid" 2>/dev/null
  set -e
  if last_result_status; then
    rc=0
  else
    rc=1
    emit_mode_notice "omnilane: Claude live mailbox closed without a successful result event"
  fi
else
  exec 3>&-
  inbox_holder_open=0
  rc="$runner_rc"
fi

trap - USR1
exit "$rc"
