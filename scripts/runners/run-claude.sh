#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Claude Code CLI
# Usage: run-claude.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE

# Runtime-relative shared library.
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

truncate_payload "$PROMPT_FILE" 102400
WORKDIR="$(cd -- "$WORKDIR" && pwd -P)" || {
  echo "omnilane: Claude workdir is not accessible" >&2
  exit 2
}

MODE_ENV=(OMNILANE_DEPTH=1)
if [[ "$MODE" == "work" ]]; then
  CLAUDE_TMP_BASE="$WORKDIR/.omnilane-claude-tmp"
  if [[ -L "$CLAUDE_TMP_BASE" || ( -e "$CLAUDE_TMP_BASE" && ! -d "$CLAUDE_TMP_BASE" ) ]]; then
    echo "omnilane: unsafe Claude work temp path" >&2
    exit 125
  fi
  mkdir -p "$CLAUDE_TMP_BASE"
  chmod 700 "$CLAUDE_TMP_BASE"
  CLAUDE_TMP_BASE="$(cd -- "$CLAUDE_TMP_BASE" && pwd -P)"
  case "$CLAUDE_TMP_BASE/" in
    "$WORKDIR/"*) ;;
    *) echo "omnilane: Claude work temp escaped workdir" >&2; exit 125 ;;
  esac
  MODE_ENV+=("CLAUDE_CODE_TMPDIR=$CLAUDE_TMP_BASE")
fi

RESTRICTED_SETTINGS='{"sandbox":{"enabled":true,"failIfUnavailable":true,"allowUnsandboxedCommands":false,"autoAllowBashIfSandboxed":true,"excludedCommands":[],"filesystem":{"disabled":false,"allowRead":[],"allowWrite":[]},"network":{"allowedDomains":[]}}}'
MODE_ARGS=()
case "$MODE" in
  advise)
    MODE_ARGS=(
      --safe-mode --restricted --setting-sources ""
      --strict-mcp-config --mcp-config '{"mcpServers":{}}'
      --settings "$RESTRICTED_SETTINGS"
      --permission-prompts none --permission-mode plan
      --tools 'Bash,Read,Glob,Grep,WebSearch,WebFetch'
    )
    ;;
  work)
    MODE_ARGS=(
      --safe-mode --restricted --setting-sources ""
      --strict-mcp-config --mcp-config '{"mcpServers":{}}'
      --settings "$RESTRICTED_SETTINGS"
      --permission-prompts none --permission-mode acceptEdits
      --tools 'Bash,Read,Glob,Grep,Edit,Write,NotebookEdit'
    )
    ;;
  sysops)
    MODE_ARGS=(
      --settings '{"sandbox":{"enabled":false}}'
      --permission-mode bypassPermissions --dangerously-skip-permissions
    )
    ;;
  *)
    echo "omnilane: invalid Claude mode '$MODE'" >&2
    exit 2
    ;;
esac

THREAD_MODE="${OMNILANE_THREAD_MODE:-}"
THREAD_ID="${OMNILANE_THREAD_ID:-}"
THREAD_ARGS=()
if [[ -n "$THREAD_MODE" || -n "$THREAD_ID" ]]; then
  [[ "$THREAD_ID" =~ ^[A-Za-z0-9._:-]+$ && "${#THREAD_ID}" -le 256 ]] || {
    echo "omnilane: invalid Claude thread session id" >&2
    exit 2
  }
  case "$THREAD_MODE" in
    new) THREAD_ARGS=(--session-id "$THREAD_ID") ;;
    resume) THREAD_ARGS=(--resume "$THREAD_ID") ;;
    *) echo "omnilane: invalid Claude thread mode" >&2; exit 2 ;;
  esac
fi

LIVE_INBOX="${OMNILANE_INBOX:-}"
if [[ -n "$LIVE_INBOX" && -p "$LIVE_INBOX" ]]; then
  EVENTS_FILE="${OUTPUT_FILE}.events.jsonl"
  STDERR_FILE="${OUTPUT_FILE}.stderr.log"
  LIVE_CHILD_PID=""

  if [[ -L "$EVENTS_FILE" || ( -e "$EVENTS_FILE" && ! -f "$EVENTS_FILE" ) ]]; then
    echo "omnilane: unsafe Claude live event path" >&2
    exit 125
  fi
  (umask 077; : > "$EVENTS_FILE"; : > "$STDERR_FILE")

  LIVE_ARGS=(--disable-slash-commands --model "$MODEL")
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && LIVE_ARGS+=(--effort "$EFFORT")
LIVE_ARGS+=("${MODE_ARGS[@]}")
LIVE_ARGS+=(-p --verbose --input-format stream-json --output-format stream-json)

finalize_live_output() {
  local tmp="${OUTPUT_FILE}.tmp"
  if command -v python3 >/dev/null 2>&1; then
    if python3 "$OMNILANE_REPO/scripts/lib/normalize-claude-stream.py" \
      "$EVENTS_FILE" "$tmp"; then
      mv "$tmp" "$OUTPUT_FILE"
      return 0
    fi
    echo "omnilane: Claude live stream ended without readable successful result or top-level assistant text" >> "$STDERR_FILE"
    return 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
      echo "omnilane: cannot extract Claude live result: python3 not found" >> "$STDERR_FILE"
      return 1
    fi
  }

  # Invoked by signal traps below.
  # shellcheck disable=SC2329
  stop_live_child() {
    local signal_rc="$1" waited=0
    trap - TERM HUP INT
    set +e
    if [[ -n "$LIVE_CHILD_PID" ]] && kill -0 "$LIVE_CHILD_PID" 2>/dev/null; then
      kill -TERM "-$LIVE_CHILD_PID" 2>/dev/null || kill -TERM "$LIVE_CHILD_PID" 2>/dev/null || true
      while kill -0 "$LIVE_CHILD_PID" 2>/dev/null && [[ "$waited" -lt 50 ]]; do
        sleep 0.1
        waited=$((waited + 1))
      done
      if kill -0 "$LIVE_CHILD_PID" 2>/dev/null; then
        kill -KILL "-$LIVE_CHILD_PID" 2>/dev/null || kill -KILL "$LIVE_CHILD_PID" 2>/dev/null || true
      fi
      wait "$LIVE_CHILD_PID" 2>/dev/null
    fi
    # Recover the transcript unconditionally so an aborted turn still leaves
    # its work in out.txt; whether that recovery counts as success is decided
    # by the job worker, which alone knows why the session was closed.
    finalize_live_output || true
    [[ -s "$STDERR_FILE" ]] || rm "$STDERR_FILE" 2>/dev/null || true
    exit "$signal_rc"
  }

  set -m
  (
    cd "$WORKDIR" || exit 127
    run_with_timeout "$RUN_TIMEOUT" env \
      "${MODE_ENV[@]}" \
      "$CLAUDE_BIN" "${LIVE_ARGS[@]}" < "$LIVE_INBOX" > "$EVENTS_FILE" 2> "$STDERR_FILE"
  ) &
  LIVE_CHILD_PID=$!
  set +m
  trap 'stop_live_child 143' TERM
  trap 'stop_live_child 129' HUP
  trap 'stop_live_child 130' INT

  set +e
  wait "$LIVE_CHILD_PID"
  RC=$?
  set -e
  trap - TERM HUP INT
  if ! finalize_live_output && [[ "$RC" -eq 0 ]]; then
    RC=1
  fi
  [[ -s "$STDERR_FILE" ]] || rm "$STDERR_FILE" 2>/dev/null || true
  exit "$RC"
fi

ARGS=(--disable-slash-commands --model "$MODEL")
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && ARGS+=(--effort "$EFFORT")
ARGS+=("${MODE_ARGS[@]}")
if [[ -n "$THREAD_MODE" ]]; then
  ARGS+=(--verbose --output-format stream-json)
  ARGS+=("${THREAD_ARGS[@]}")
else
  ARGS+=(--output-format text)
fi
ARGS+=(-p "$(cat "$PROMPT_FILE")")

set +e
(
  cd "$WORKDIR" || exit 127
  if [[ -n "$THREAD_MODE" ]]; then
    run_with_timeout "$RUN_TIMEOUT" env \
      "${MODE_ENV[@]}" \
      "$CLAUDE_BIN" "${ARGS[@]}" > "${OUTPUT_FILE}.events.jsonl" 2> "${OUTPUT_FILE}.stderr.log"
  else
    run_with_timeout "$RUN_TIMEOUT" env \
      "${MODE_ENV[@]}" \
      "$CLAUDE_BIN" "${ARGS[@]}" > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
  fi
)
RC=$?
set -e

if [[ -n "$THREAD_MODE" ]]; then
  if [[ "$RC" -eq 0 ]] && ! python3 "$OMNILANE_REPO/scripts/lib/normalize-claude-stream.py" \
    "$OUTPUT_FILE.events.jsonl" "${OUTPUT_FILE}.tmp"; then
    echo "omnilane: Claude thread stream without successful result or top-level assistant text" >> "${OUTPUT_FILE}.stderr.log"
    RC=1
  fi
  if [[ "$RC" -eq 0 ]]; then
    if ! python3 - "$OUTPUT_FILE.events.jsonl" "${OUTPUT_FILE}.tmp" <<'PY'
import json
import pathlib
import sys

events_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
last_result = None
with events_path.open(encoding="utf-8") as events:
    for raw_line in events:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            last_result = event["result"]
if last_result is None:
    raise SystemExit(1)
output_path.write_text(last_result.rstrip("\n") + "\n", encoding="utf-8")
PY
    then
      echo "omnilane: Claude thread stream ended without readable result event" >> "${OUTPUT_FILE}.stderr.log"
      RC=1
    fi
  fi
  if [[ "$RC" -ne 0 && -s "${OUTPUT_FILE}.stderr.log" ]]; then
    cat "${OUTPUT_FILE}.stderr.log" >&2
    cat "${OUTPUT_FILE}.stderr.log" > "${OUTPUT_FILE}.tmp"
  fi
fi
[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
