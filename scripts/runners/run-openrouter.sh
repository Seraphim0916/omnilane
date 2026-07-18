#!/usr/bin/env bash
set -euo pipefail

# omnilane runner: OpenRouter direct API (no CLI dependency)
#
# Usage: run-openrouter.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   MODE = advise only. This vendor is pure inference over HTTPS — it has no
#   agentic loop and cannot edit files, so work mode is a hard error rather
#   than a silent read-only downgrade.
#
# MODEL is an OpenRouter model slug (e.g. anthropic/claude-sonnet-4). EFFORT
# is accepted for interface parity only. Needs OPENROUTER_API_KEY and curl;
# python3 builds/parses the JSON so prompt content never needs shell escaping.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$WORKDIR" "$EFFORT" # parity with the uniform runner interface

OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

fail() { echo "omnilane: $1" > "${OUTPUT_FILE}.stderr.log"; exit 2; }

[[ "$MODE" == "advise" ]] || fail "openrouter is inference-only: use --mode advise (work mode needs an agentic CLI vendor like opencode)"
[[ -n "${OPENROUTER_API_KEY:-}" ]] || fail "OPENROUTER_API_KEY is not set"
[[ -n "$MODEL" && "$MODEL" != "-" ]] || fail "openrouter needs an explicit model slug (e.g. anthropic/claude-sonnet-4)"
command -v curl >/dev/null 2>&1 || fail "curl not found"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"

truncate_payload "$PROMPT_FILE" 140000

REQUEST_FILE="${OUTPUT_FILE}.request.json"
RESPONSE_FILE="${OUTPUT_FILE}.response.json"
cleanup() { rm "$REQUEST_FILE" "$RESPONSE_FILE" 2>/dev/null || true; }
trap cleanup EXIT

python3 - "$MODEL" "$PROMPT_FILE" > "$REQUEST_FILE" <<'PY'
import json, sys
model, prompt_file = sys.argv[1], sys.argv[2]
with open(prompt_file, encoding="utf-8", errors="replace") as f:
    prompt = f.read()
json.dump({"model": model, "messages": [{"role": "user", "content": prompt}]},
          sys.stdout)
PY

set +e
run_with_timeout "$RUN_TIMEOUT" curl -sS --fail-with-body \
  --max-time "$RUN_TIMEOUT" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST "$OPENROUTER_BASE_URL/chat/completions" \
  --data-binary "@$REQUEST_FILE" \
  > "$RESPONSE_FILE" 2> "${OUTPUT_FILE}.stderr.log"
RC=$?
set -e

if [[ "$RC" -ne 0 ]]; then
  # Surface the API error body (never the key) alongside curl's own stderr.
  [[ -s "$RESPONSE_FILE" ]] && cat "$RESPONSE_FILE" >> "${OUTPUT_FILE}.stderr.log"
  exit "$RC"
fi

set +e
python3 - "$RESPONSE_FILE" > "${OUTPUT_FILE}.tmp" 2>> "${OUTPUT_FILE}.stderr.log" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    data = json.load(f)
if "error" in data:
    print(f"omnilane: openrouter API error: {data['error']}", file=sys.stderr)
    sys.exit(1)
content = data["choices"][0]["message"]["content"]
sys.stdout.write(content if isinstance(content, str) else json.dumps(content))
PY
RC=$?
set -e

# Empty output is a failure, not a silent rc=0 success.
if ! grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null; then
  echo "omnilane: openrouter produced no output" >> "${OUTPUT_FILE}.stderr.log"
  [[ "$RC" -eq 0 ]] && RC=1
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
