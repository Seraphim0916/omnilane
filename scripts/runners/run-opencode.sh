#!/usr/bin/env bash
set -euo pipefail

# omnilane runner: OpenCode CLI (multi-provider aggregator)
#
# Usage: run-opencode.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   MODE = advise (built-in read-only plan agent) | work (may edit files)
#
# MODEL is OpenCode's provider/model form (e.g. openrouter/moonshotai/kimi-k2);
# "-" lets OpenCode use its own configured default. OpenCode's --variant flag
# is provider-specific, so EFFORT maps onto it only when set.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

OPENCODE_BIN="${OPENCODE_BIN:-opencode}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

truncate_payload "$PROMPT_FILE" 140000

# --agent plan is OpenCode's built-in restricted agent (edits/bash gated);
# --auto only in work mode, and only for permissions not explicitly denied.
ARGS=(run "$(cat "$PROMPT_FILE")" --format default)
[[ -n "$MODEL" && "$MODEL" != "-" ]] && ARGS+=(-m "$MODEL")
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && ARGS+=(--variant "$EFFORT")
if [[ "$MODE" == "advise" ]]; then
  ARGS+=(--agent plan)
else
  ARGS+=(--auto)
fi

set +e
(
  cd "$WORKDIR" || exit 127
  OMNILANE_DEPTH=1 run_with_timeout "$RUN_TIMEOUT" \
    "$OPENCODE_BIN" "${ARGS[@]}" \
    > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

# Empty output is a failure, not a silent rc=0 success.
if ! grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null; then
  echo "omnilane: opencode produced no output" >> "${OUTPUT_FILE}.stderr.log"
  [[ "$RC" -eq 0 ]] && RC=1
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
