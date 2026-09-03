#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Google Antigravity CLI (agy)
# Usage: run-gemini.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
# MODEL is agy's native display string, e.g. "Gemini 3.1 Pro (High)" — the
# thinking level rides inside the model string, so EFFORT is parity-only.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$EFFORT" # parity with the uniform runner interface; effort rides in the model string

AGY_BIN="${AGY_BIN:-agy}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"
CAPACITY_PATTERN='MODEL_CAPACITY_EXHAUSTED|No capacity available for model|rateLimitExceeded|RESOURCE_EXHAUSTED'

THREAD_MODE="${OMNILANE_THREAD_MODE:-}"
THREAD_ID="${OMNILANE_THREAD_ID:-}"
THREAD_ARGS=()
if [[ -n "$THREAD_MODE" || -n "$THREAD_ID" ]]; then
  [[ "$THREAD_ID" =~ ^[A-Za-z0-9._:-]+$ && "${#THREAD_ID}" -le 256 ]] || {
    echo "omnilane: invalid Gemini thread session id" >&2
    exit 2
  }
  case "$THREAD_MODE" in
    new) ;;
    resume) THREAD_ARGS=(--conversation "$THREAD_ID") ;;
    *) echo "omnilane: invalid Gemini thread mode" >&2; exit 2 ;;
  esac
fi

truncate_payload "$PROMPT_FILE" 140000

# Both modes run inside the target WORKDIR so the worker can actually see the
# repo it is asked about. Tradeoff: repo-level agent personas may color advise
# answers; set OMNILANE_GEMINI_SCRATCH=1 to run advise in a neutral scratch dir.
if [[ "$MODE" == "advise" && "${OMNILANE_GEMINI_SCRATCH:-0}" == "1" ]]; then
  RUN_DIR="$OMNILANE_HOME/agy-scratch"
  mkdir -p "$RUN_DIR/.agents"; : > "$RUN_DIR/.agents/AGENTS.md"
else
  RUN_DIR="$WORKDIR"
fi

MODEL_ARGS=()
[[ -n "$MODEL" && "$MODEL" != "-" ]] && MODEL_ARGS=(--model "$MODEL")

# Without an execution mode, print mode denies tool calls outright:
# plan = read-only tools (advise), accept-edits = file edits allowed (work).
if [[ "$MODE" == "advise" ]]; then MODE_ARGS=(--mode plan); else MODE_ARGS=(--mode accept-edits); fi

LIVE_INBOX="${OMNILANE_INBOX:-}"
if [[ -n "$LIVE_INBOX" && -p "$LIVE_INBOX" ]]; then
  EVENTS_FILE="${OUTPUT_FILE}.events.jsonl"
  STDERR_FILE="${OUTPUT_FILE}.stderr.log"
  LIVE_CHILD_PID=""

  if [[ -L "$EVENTS_FILE" || ( -e "$EVENTS_FILE" && ! -f "$EVENTS_FILE" ) ]]; then
    echo "omnilane: unsafe Gemini live event path" >&2
    exit 125
  fi
  (umask 077; : > "$EVENTS_FILE"; : > "$STDERR_FILE")

  finalize_live_output() {
    local tmp="${OUTPUT_FILE}.tmp"
    if ! command -v python3 >/dev/null 2>&1; then
      echo "omnilane: cannot extract Gemini live result: python3 not found" >> "$STDERR_FILE"
      return 1
    fi
    if ! python3 - "$EVENTS_FILE" "$tmp" <<'PY'
import json
import pathlib
import sys

events_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
last_response = None

with events_path.open(encoding="utf-8") as events:
    for raw_line in events:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        result = event.get("result")
        if (
            event.get("event") == "result"
            and isinstance(result, dict)
            and result.get("status") == "SUCCESS"
            and isinstance(result.get("response"), str)
        ):
            last_response = result["response"]

if last_response is None:
    raise SystemExit(1)

output_path.write_text(last_response.rstrip("\n") + "\n", encoding="utf-8")
PY
    then
      echo "omnilane: Gemini live stream ended without a readable SUCCESS result" >> "$STDERR_FILE"
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
    cd "$RUN_DIR" || exit 127
    run_with_timeout "$RUN_TIMEOUT" env \
      -u GEMINI_API_KEY -u GOOGLE_API_KEY -u GOOGLE_AI_API_KEY \
      NO_BROWSER=1 OMNILANE_DEPTH=1 \
      "$AGY_BIN" --dangerously-skip-permissions --add-dir "$RUN_DIR" \
      "${MODE_ARGS[@]}" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
      --input-format stream-json --output-format stream-json -p "" \
      < "$LIVE_INBOX" > "$EVENTS_FILE" 2> "$STDERR_FILE"
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
  if grep -Eiq "$CAPACITY_PATTERN" "$EVENTS_FILE" "$STDERR_FILE" 2>/dev/null; then
    echo "omnilane: gemini capacity exhausted" >> "$STDERR_FILE"
    RC=126
  fi
  [[ -s "$STDERR_FILE" ]] || rm "$STDERR_FILE" 2>/dev/null || true
  exit "$RC"
fi

if [[ -n "$THREAD_MODE" ]]; then
  set +e
  (
    cd "$RUN_DIR" || exit 127
    env -u GEMINI_API_KEY -u GOOGLE_API_KEY -u GOOGLE_AI_API_KEY \
      NO_BROWSER=1 OMNILANE_DEPTH=1 \
      "$AGY_BIN" --dangerously-skip-permissions --add-dir "$RUN_DIR" \
      "${MODE_ARGS[@]}" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
      --print-timeout "${RUN_TIMEOUT}s" --output-format json \
      "${THREAD_ARGS[@]}" -p "$(cat "$PROMPT_FILE")" \
      > "${OUTPUT_FILE}.result.json" 2> "${OUTPUT_FILE}.stderr.log"
  )
  RC=$?
  set -e
  if [[ "$RC" -eq 0 ]]; then
    if ! python3 - "${OUTPUT_FILE}.result.json" "${OUTPUT_FILE}.tmp" <<'PY'
import json
import pathlib
import sys

result = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
response = result.get("response")
if result.get("status") != "SUCCESS" or not isinstance(response, str):
    raise SystemExit(1)
pathlib.Path(sys.argv[2]).write_text(response.rstrip("\n") + "\n", encoding="utf-8")
PY
    then
      echo "omnilane: Gemini thread result was not readable SUCCESS JSON" >> "${OUTPUT_FILE}.stderr.log"
      RC=1
    fi
  fi
else
set +e
(
  cd "$RUN_DIR" || exit 127
  # Headless cannot answer OAuth prompts; strip API keys to stay on CLI login.
  # --add-dir registers RUN_DIR as the active workspace; without it agy's
  # sandbox denies every tool call (run_command/view_file) in print mode.
  env -u GEMINI_API_KEY -u GOOGLE_API_KEY -u GOOGLE_AI_API_KEY \
    NO_BROWSER=1 OMNILANE_DEPTH=1 \
    "$AGY_BIN" --dangerously-skip-permissions --add-dir "$RUN_DIR" \
    "${MODE_ARGS[@]}" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    --print-timeout "${RUN_TIMEOUT}s" \
    --print "$(cat "$PROMPT_FILE")" \
    > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e
fi

if grep -Eiq "$CAPACITY_PATTERN" "${OUTPUT_FILE}.tmp" "${OUTPUT_FILE}.result.json" "${OUTPUT_FILE}.stderr.log" 2>/dev/null; then
  echo "omnilane: gemini capacity exhausted" >> "${OUTPUT_FILE}.stderr.log"
  RC=126
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
