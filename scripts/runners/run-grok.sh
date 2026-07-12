#!/usr/bin/env bash
set -euo pipefail
# omniroute runner: Grok Build CLI
# Usage: run-grok.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
# EFFORT is accepted for interface parity; Grok has no reasoning-effort knob.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

GROK_BIN="${GROK_BIN:-grok}"
TIMEOUT_CMD="$(resolve_timeout_cmd)"
RUN_TIMEOUT="${OMNIROUTE_TIMEOUT:-600}"
MAX_ATTEMPTS="${OMNIROUTE_GROK_MAX_ATTEMPTS:-5}"

# Subscription OAuth path: an exhausted API key in env causes 403s.
unset XAI_API_KEY 2>/dev/null || true

truncate_payload "$PROMPT_FILE" 140000

ARGS=(--cwd "$WORKDIR" --model "$MODEL"
      --no-memory --no-subagents --no-plan --no-alt-screen
      --output-format plain --verbatim --prompt-file "$PROMPT_FILE")
[[ "$MODE" == "advise" ]] && ARGS+=(--permission-mode plan)
# Web/X search stays ON by default — it is this vendor's signature lane.
[[ "${OMNIROUTE_GROK_NO_WEB:-0}" == "1" ]] && ARGS+=(--disable-web-search)

# Grok intermittently emits empty output on large inputs; retry until it speaks.
RC=0; attempt=1
while [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; do
  set +e
  OMNIROUTE_DEPTH=1 ${TIMEOUT_CMD:+$TIMEOUT_CMD $RUN_TIMEOUT} \
    "$GROK_BIN" "${ARGS[@]}" > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
  RC=$?
  set -e
  grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null && break
  # Usage-limit / auth errors will not heal on retry — surface them immediately.
  if grep -Eiq 'usage limit|rate limit|401|403|SuperGrok' "${OUTPUT_FILE}.stderr.log" 2>/dev/null; then
    break
  fi
  attempt=$((attempt + 1))
done

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
