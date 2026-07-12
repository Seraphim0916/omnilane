#!/usr/bin/env bash
set -euo pipefail
# omniroute runner: OpenAI Codex CLI
# Usage: run-codex.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   MODE = advise (read-only, ephemeral) | work (may edit files in WORKDIR)

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

CODEX_BIN="${CODEX_BIN:-codex}"
TIMEOUT_CMD="$(resolve_timeout_cmd)"
RUN_TIMEOUT="${OMNIROUTE_TIMEOUT:-600}"

ARGS=(exec -m "$MODEL" -o "${OUTPUT_FILE}.tmp")
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && ARGS+=(-c "model_reasoning_effort=\"$EFFORT\"")
if [[ "$MODE" == "advise" ]]; then
  ARGS+=(--ephemeral --skip-git-repo-check -s read-only)
fi

truncate_payload "$PROMPT_FILE" 140000

set +e
(
  cd "$WORKDIR" || exit 127
  # Subscription-login path: strip API-key env so the CLI uses its own auth.
  ${TIMEOUT_CMD:+$TIMEOUT_CMD $RUN_TIMEOUT} env \
    -u OPENAI_API_KEY -u OPENAI_ORG_ID -u OPENAI_ORGANIZATION -u OPENAI_PROJECT -u OPENAI_API_BASE \
    OMNIROUTE_DEPTH=1 \
    "$CODEX_BIN" "${ARGS[@]}" < "$PROMPT_FILE" \
    > "${OUTPUT_FILE}.progress.log" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

if [[ -f "${OUTPUT_FILE}.tmp" ]]; then
  strip_ansi "${OUTPUT_FILE}.tmp"
  mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
fi
# no -f: force-flag rm is blocked by some environments' destructive guards
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
