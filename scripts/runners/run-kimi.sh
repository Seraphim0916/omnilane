#!/usr/bin/env bash
set -euo pipefail

# omnilane runner: Moonshot Kimi Code CLI
#
# Usage: run-kimi.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   MODE = advise (read-only, plan mode) | work (may edit files in WORKDIR)
#
# MODEL is a Kimi Code model alias from the user's config.toml (e.g. kimi-k3);
# an unknown alias fails at the CLI, not here. Kimi has no reasoning-effort
# knob, so EFFORT is accepted for interface parity only.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$EFFORT" # parity with the uniform runner interface

KIMI_BIN="${KIMI_BIN:-kimi}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

truncate_payload "$PROMPT_FILE" 140000

# --plan starts plan mode (read-only); -y auto-approves actions for work mode.
ARGS=(-p "$(cat "$PROMPT_FILE")" --output-format text)
[[ -n "$MODEL" && "$MODEL" != "-" ]] && ARGS+=(-m "$MODEL")
if [[ "$MODE" == "advise" ]]; then
  ARGS+=(--plan)
else
  ARGS+=(-y)
fi

set +e
(
  cd "$WORKDIR" || exit 127
  # Subscription-login path: strip API-key env so the CLI uses its own auth.
  run_with_timeout "$RUN_TIMEOUT" env \
    -u MOONSHOT_API_KEY -u KIMI_API_KEY \
    OMNILANE_DEPTH=1 \
    "$KIMI_BIN" "${ARGS[@]}" \
    > "${OUTPUT_FILE}.tmp" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

# Empty output is a failure, not a silent rc=0 success.
if ! grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null; then
  echo "omnilane: kimi produced no output" >> "${OUTPUT_FILE}.stderr.log"
  [[ "$RC" -eq 0 ]] && RC=1
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
