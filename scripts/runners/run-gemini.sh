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

set +e
(
  cd "$RUN_DIR" || exit 127
  # Headless cannot answer OAuth prompts; strip API keys to stay on CLI login.
  env -u GEMINI_API_KEY -u GOOGLE_API_KEY -u GOOGLE_AI_API_KEY \
    NO_BROWSER=1 OMNILANE_DEPTH=1 \
    "$AGY_BIN" --dangerously-skip-permissions ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    --print-timeout "${RUN_TIMEOUT}s" \
    --print "$(cat "$PROMPT_FILE")" \
    > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

if grep -Eiq "$CAPACITY_PATTERN" "${OUTPUT_FILE}.tmp" "${OUTPUT_FILE}.stderr.log" 2>/dev/null; then
  echo "omnilane: gemini capacity exhausted" >> "${OUTPUT_FILE}.stderr.log"
  RC=126
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
