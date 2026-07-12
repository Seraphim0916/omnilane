#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Claude Code CLI
# Usage: run-claude.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

truncate_payload "$PROMPT_FILE" 102400

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
