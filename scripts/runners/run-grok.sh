#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Grok Build CLI
# Usage: run-grok.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
# EFFORT is accepted for interface parity; Grok has no reasoning-effort knob.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$EFFORT" # parity with the uniform runner interface; Grok has no effort knob

GROK_BIN="${GROK_BIN:-grok}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"
MAX_ATTEMPTS="${OMNILANE_GROK_MAX_ATTEMPTS:-5}"
[[ "$MAX_ATTEMPTS" =~ ^([1-9]|1[0-9]|20)$ ]] || {
  echo "omnilane: invalid OMNILANE_GROK_MAX_ATTEMPTS (want 1..20)" >&2
  exit 2
}

# Subscription OAuth path: an exhausted API key in env causes 403s.
unset XAI_API_KEY 2>/dev/null || true

truncate_payload "$PROMPT_FILE" 140000

ARGS=(--cwd "$WORKDIR" --model "$MODEL"
      --no-memory --no-subagents --no-plan --no-alt-screen
      --output-format plain --verbatim --prompt-file "$PROMPT_FILE")
[[ "$MODE" == "advise" ]] && ARGS+=(--permission-mode plan)
# Web/X search stays ON by default — it is this vendor's signature lane.
[[ "${OMNILANE_GROK_NO_WEB:-0}" == "1" ]] && ARGS+=(--disable-web-search)

# Grok intermittently emits empty output on large inputs; retry until it speaks.
RC=0; attempt=1
while [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; do
  set +e
  OMNILANE_DEPTH=1 run_with_timeout "$RUN_TIMEOUT" \
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

# Empty output after all retries is a failure, not a silent rc=0 success.
if ! grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null; then
  echo "omnilane: grok produced no output after $MAX_ATTEMPTS attempts" >> "${OUTPUT_FILE}.stderr.log"
  [[ "$RC" -eq 0 ]] && RC=1
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
