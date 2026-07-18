#!/usr/bin/env bash
set -euo pipefail

# omnilane runner: any OpenAI-compatible /chat/completions endpoint.
#
# Usage: run-openai-compat.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   Driven by run-<vendor>.sh wrappers that export OMNILANE_OAI_VENDOR. Like the
#   openrouter runner this is pure inference over HTTPS — no agentic loop, so
#   work mode is a hard error, not a silent read-only downgrade.
#
# Base URL, API-key env var, and a model hint come from vendor_api_spec. The
# base URL is overridable per vendor via <VENDOR>_BASE_URL. EFFORT is accepted
# for interface parity only. Needs curl and the vendor's API key; python3
# builds/parses the JSON so prompt content never needs shell escaping.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$WORKDIR" "$EFFORT" # parity with the uniform runner interface

fail() { echo "omnilane: $1" > "${OUTPUT_FILE}.stderr.log"; exit 2; }

VENDOR="${OMNILANE_OAI_VENDOR:?run-openai-compat.sh must be invoked via a run-<vendor>.sh wrapper}"
SPEC="$(vendor_api_spec "$VENDOR")"
[[ -n "$SPEC" ]] || fail "unknown OpenAI-compatible vendor '$VENDOR'"
BASE_DEFAULT="${SPEC%%|*}"
MODEL_HINT="${SPEC##*|}"
KEY_ENV="${SPEC#*|}"; KEY_ENV="${KEY_ENV%%|*}"

VENDOR_UPPER="$(printf '%s' "$VENDOR" | tr '[:lower:]' '[:upper:]')"
BASE_OVERRIDE_VAR="${VENDOR_UPPER}_BASE_URL"
BASE_URL="${!BASE_OVERRIDE_VAR:-$BASE_DEFAULT}"
API_KEY="${!KEY_ENV:-}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

[[ "$MODE" == "advise" ]] || fail "$VENDOR is inference-only: use --mode advise (work mode needs an agentic CLI vendor like opencode)"
[[ -n "$API_KEY" ]] || fail "$KEY_ENV is not set"
[[ -n "$MODEL" && "$MODEL" != "-" ]] || fail "$VENDOR needs an explicit model slug (e.g. $MODEL_HINT)"
command -v curl >/dev/null 2>&1 || fail "curl not found"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"

truncate_payload "$PROMPT_FILE" 140000

REQUEST_FILE="${OUTPUT_FILE}.request.json"
RESPONSE_FILE="${OUTPUT_FILE}.response.json"
# Keep the API key out of the process argument list (ps-visible): curl reads the
# Authorization header from a 0600 file, not a -H command-line flag.
HEADER_FILE="${OUTPUT_FILE}.headers"
cleanup() { rm "$REQUEST_FILE" "$RESPONSE_FILE" "$HEADER_FILE" 2>/dev/null || true; }
trap cleanup EXIT
( umask 077; printf 'Authorization: Bearer %s\n' "$API_KEY" > "$HEADER_FILE" )

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
  -H "@$HEADER_FILE" \
  -H "Content-Type: application/json" \
  -X POST "$BASE_URL/chat/completions" \
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
python3 - "$VENDOR" "$RESPONSE_FILE" > "${OUTPUT_FILE}.tmp" 2>> "${OUTPUT_FILE}.stderr.log" <<'PY'
import json, sys
vendor = sys.argv[1]
with open(sys.argv[2], encoding="utf-8", errors="replace") as f:
    data = json.load(f)
if "error" in data:
    print(f"omnilane: {vendor} API error: {data['error']}", file=sys.stderr)
    sys.exit(1)
content = data["choices"][0]["message"]["content"]
sys.stdout.write(content if isinstance(content, str) else json.dumps(content))
PY
RC=$?
set -e

# Empty output is a failure, not a silent rc=0 success.
if ! grep -q '[^[:space:]]' "${OUTPUT_FILE}.tmp" 2>/dev/null; then
  echo "omnilane: $VENDOR produced no output" >> "${OUTPUT_FILE}.stderr.log"
  if [[ "$RC" -eq 0 ]]; then RC=1; fi
fi

[[ -f "${OUTPUT_FILE}.tmp" ]] && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
