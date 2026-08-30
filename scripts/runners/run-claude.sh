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
  if [[ "$MODE" == "advise" ]]; then
    LIVE_ARGS+=(--tools Read Glob Grep)
  else
    LIVE_ARGS+=(--permission-mode acceptEdits)
  fi
  LIVE_ARGS+=(-p --verbose --input-format stream-json --output-format stream-json)

  finalize_live_output() {
    local tmp="${OUTPUT_FILE}.tmp"
    if ! command -v python3 >/dev/null 2>&1; then
      echo "omnilane: cannot extract Claude live result: python3 not found" >> "$STDERR_FILE"
      return 1
    fi
    if ! python3 - "$EVENTS_FILE" "$tmp" <<'PY'
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
      echo "omnilane: Claude live stream ended without a readable result event" >> "$STDERR_FILE"
      return 1
    fi
    mv "$tmp" "$OUTPUT_FILE"
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
    finalize_live_output || true
    [[ -s "$STDERR_FILE" ]] || rm "$STDERR_FILE" 2>/dev/null || true
    exit "$signal_rc"
  }

  set -m
  (
    cd "$WORKDIR" || exit 127
    run_with_timeout "$RUN_TIMEOUT" env \
      OMNILANE_DEPTH=1 \
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

ARGS=(--disable-slash-commands --model "$MODEL" --output-format text)
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && ARGS+=(--effort "$EFFORT")
if [[ "$MODE" == "advise" ]]; then
  # Read-only surface: the worker can inspect the repo but not change or run anything.
  ARGS+=(--tools Read Glob Grep)
else
  ARGS+=(--permission-mode acceptEdits)
fi
ARGS+=(-p "$(cat "$PROMPT_FILE")")

set +e
(
  cd "$WORKDIR" || exit 127
  run_with_timeout "$RUN_TIMEOUT" env \
    OMNILANE_DEPTH=1 \
    "$CLAUDE_BIN" "${ARGS[@]}" > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
