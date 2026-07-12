#!/usr/bin/env bash
set -euo pipefail
# omniroute runner: Claude Code CLI
# Usage: run-claude.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
TIMEOUT_CMD="$(resolve_timeout_cmd)"
RUN_TIMEOUT="${OMNIROUTE_TIMEOUT:-600}"

truncate_payload "$PROMPT_FILE" 102400

ARGS=(--disable-slash-commands --model "$MODEL" --output-format text)
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && ARGS+=(--effort "$EFFORT")
if [[ "$MODE" == "advise" ]]; then
  ARGS+=(--tools "")            # pure inference, no tool surface
else
  ARGS+=(--permission-mode acceptEdits)
fi
ARGS+=(-p "$(cat "$PROMPT_FILE")")

set +e
(
  cd "$WORKDIR" || exit 127
  ${TIMEOUT_CMD:+$TIMEOUT_CMD $RUN_TIMEOUT} env \
    OMNIROUTE_DEPTH=1 \
    "$CLAUDE_BIN" "${ARGS[@]}" > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
