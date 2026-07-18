#!/usr/bin/env bash
set -euo pipefail

# omnilane runner: Alibaba Qwen Code CLI
#
# Usage: run-qwen.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   MODE = advise (default approvals: write tools blocked non-interactively)
#        | work  (--approval-mode yolo: may edit files in WORKDIR)
#
# Qwen Code has no reasoning-effort knob, so EFFORT is parity-only.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$EFFORT" # parity with the uniform runner interface

QWEN_BIN="${QWEN_BIN:-qwen}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

truncate_payload "$PROMPT_FILE" 140000

ARGS=(-p "$(cat "$PROMPT_FILE")" --output-format text)
[[ -n "$MODEL" && "$MODEL" != "-" ]] && ARGS+=(-m "$MODEL")
[[ "$MODE" == "work" ]] && ARGS+=(--approval-mode yolo)

set +e
(
  cd "$WORKDIR" || exit 127
  # Subscription-login path: strip OpenAI-compatible env overrides so the CLI
  # uses its own Qwen OAuth instead of a hijacked endpoint.
  run_with_timeout "$RUN_TIMEOUT" env \
    -u OPENAI_API_KEY -u OPENAI_BASE_URL -u DASHSCOPE_API_KEY \
    OMNILANE_DEPTH=1 \
    "$QWEN_BIN" "${ARGS[@]}" \
    > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

# Empty output is a failure, not a silent rc=0 success.
if ! grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null; then
  echo "omnilane: qwen produced no output" >> "${OUTPUT_FILE}.stderr.log"
  [[ "$RC" -eq 0 ]] && RC=1
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
