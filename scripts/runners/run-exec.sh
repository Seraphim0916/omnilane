#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: exec — bring your own gate/aggregator.
# Usage: run-exec.sh MODE WORKDIR SCRIPT EFFORT PROMPT_FILE OUTPUT_FILE
#
# The routing "model" field is a path to YOUR executable, which receives:
#   $1 MODE ($2 WORKDIR $3 EFFORT $4 PROMPT_FILE $5 OUTPUT_FILE)
# Write the final answer to OUTPUT_FILE; exit non-zero on failure.
# Typical use: the arbitrate lane pointing at a multi-model vote script.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; SCRIPT="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

SCRIPT="${SCRIPT/#\~/$HOME}"
[[ -x "$SCRIPT" ]] || { echo "omnilane: exec gate not found or not executable: $SCRIPT" >&2; exit 2; }

truncate_payload "$PROMPT_FILE" 140000

set +e
OMNILANE_DEPTH=1 run_with_timeout "$RUN_TIMEOUT" \
  "$SCRIPT" "$MODE" "$WORKDIR" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE" \
  2> "${OUTPUT_FILE}.stderr.log"
RC=$?
set -e

[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
