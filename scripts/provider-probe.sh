#!/usr/bin/env bash
set -euo pipefail

# Explicit, bounded live-inference probe for exactly one configured provider.
# No provider is contacted unless --vendor is present. Provider output and
# stderr stay inside a private temporary directory and are never relayed.

SELF_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${OMNILANE_PROBE_REPO:-$SELF_REPO}"
DISPATCH="$REPO/scripts/dispatch.sh"
JSON_MODE=0
VENDOR=""
TIMEOUT=30

usage() {
  echo "usage: provider-probe.sh --vendor V [--timeout SEC] [--json]" >&2
  exit 2
}

json_escape() {
  local s="$1" out="" ch escaped code i
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:i:1}"
    case "$ch" in
      '"') out="$out\\\"" ;;
      \\) out="$out\\\\" ;;
      $'\b') out="$out\\b" ;;
      $'\f') out="$out\\f" ;;
      $'\n') out="$out\\n" ;;
      $'\r') out="$out\\r" ;;
      $'\t') out="$out\\t" ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'$ch"
        if [[ "$code" -ge 0 && "$code" -lt 32 ]]; then
          printf -v escaped '\\u%04x' "$code"
          out="$out$escaped"
        else
          out="$out$ch"
        fi
        ;;
    esac
  done
  printf '%s' "$out"
}

emit_result() {
  local ok="$1" status="$2" invoked="$3" reason="$4"
  local duration="$5" response_bytes="$6"
  if [[ "$JSON_MODE" -eq 1 ]]; then
    printf '{"schema_version":1,"command":"provider-probe","ok":%s,"vendor":"%s","model":"%s","status":"%s","provider_invoked":%s,"timeout":%s,"duration_seconds":%s,"response_bytes":%s,"reason":"%s"}\n' \
      "$ok" "$(json_escape "$VENDOR")" "$(json_escape "${MODEL:-}")" \
      "$(json_escape "$status")" "$invoked" "$TIMEOUT" "$duration" \
      "$response_bytes" "$(json_escape "$reason")"
  else
    printf 'provider_probe vendor=%s model=%s status=%s provider_invoked=%s timeout=%ss duration=%ss response_bytes=%s reason=%s\n' \
      "$VENDOR" "${MODEL:-unknown}" "$status" "$invoked" "$TIMEOUT" \
      "$duration" "$response_bytes" "$reason"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vendor)
      [[ $# -ge 2 ]] || usage
      VENDOR="$2"; shift 2 ;;
    --timeout)
      [[ $# -ge 2 ]] || usage
      TIMEOUT="$2"; shift 2 ;;
    --json)
      JSON_MODE=1; shift ;;
    *) usage ;;
  esac
done

[[ "$VENDOR" =~ ^(codex|claude|grok|gemini|kimi|qwen|opencode|openrouter|deepseek|zai|mistral|groq|cerebras)$ ]] || {
  echo "omnilane: invalid vendor for provider probe" >&2
  exit 2
}
[[ "$TIMEOUT" =~ ^[1-9][0-9]{0,2}$ && "$TIMEOUT" -le 300 ]] || {
  echo "omnilane: invalid probe timeout (want 1..300 seconds)" >&2
  exit 2
}
[[ -x "$DISPATCH" ]] || {
  emit_result false unavailable false "dispatch is unavailable" 0 0
  exit 1
}

PLAN=""
while IFS= read -r lane; do
  [[ "$lane" =~ ^[a-z][a-z0-9-]*$ ]] || continue
  set +e
  candidate="$(bash "$DISPATCH" --dry-run --mode advise --vendor "$VENDOR" \
    --timeout "$TIMEOUT" "$lane" "OMNILANE_PROVIDER_PROBE" 2>/dev/null)"
  candidate_rc=$?
  set -e
  if [[ "$candidate_rc" -eq 0 && "$candidate" == *"provider_invoked=no"* ]]; then
    PLAN="$candidate"
    break
  fi
done < <(bash "$DISPATCH" --list 2>/dev/null | sed -n 's/^\([a-z][a-z0-9-]*\):.*/\1/p')

if [[ -z "$PLAN" ]]; then
  emit_result false unavailable false "vendor is not both configured and available" 0 0
  exit 4
fi

field_value() {
  local key="$1"
  printf '%s\n' "$PLAN" | sed -n "s/^${key}=//p" | sed -n '1p'
}

decode_plan_value() {
  local encoded="$1" decoded
  [[ ${#encoded} -le 4096 && "$encoded" != *$'\n'* ]] || return 1
  # dispatch.sh emits values with Bash printf %q. read without -r reverses
  # its backslash quoting without evaluating command substitutions or code.
  IFS= read decoded <<< "$encoded" || return 1
  printf '%s' "$decoded"
}

RAW_MODEL="$(field_value model)"
RAW_EFFORT="$(field_value effort)"
if ! MODEL="$(decode_plan_value "$RAW_MODEL")" ||
   ! EFFORT="$(decode_plan_value "$RAW_EFFORT")"; then
  emit_result false unavailable false "invalid dry-run field encoding" 0 0
  exit 4
fi
RUNNER="$REPO/scripts/runners/run-$VENDOR.sh"
[[ -n "$MODEL" && -x "$RUNNER" ]] || {
  emit_result false unavailable false "resolved runner or model is unavailable" 0 0
  exit 4
}

TMP_BASE="${TMPDIR:-/tmp}"
TMP_ROOT="$(mktemp -d "$TMP_BASE/omnilane-provider-probe.XXXXXX")"
cleanup() {
  local path
  case "${TMP_ROOT:-}" in
    "$TMP_BASE"/omnilane-provider-probe.*)
      if [[ -d "$TMP_ROOT" && ! -L "$TMP_ROOT" ]]; then
        for path in "$TMP_ROOT"/* "$TMP_ROOT"/.[!.]*; do
          [[ -f "$path" || -L "$path" ]] && rm -- "$path"
        done
        rmdir "$TMP_ROOT" 2>/dev/null || true
      fi
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM
PROMPT_FILE="$TMP_ROOT/prompt.txt"
OUTPUT_FILE="$TMP_ROOT/output.txt"
(umask 077; printf '%s\n' \
  'Reply with exactly OMNILANE_PROVIDER_PROBE_OK. Do not use tools or inspect files.' \
  > "$PROMPT_FILE")

started="$(date +%s)"
set +e
OMNILANE_TIMEOUT="$TIMEOUT" "$RUNNER" advise \
  "${OMNILANE_PROBE_WORKDIR:-$PWD}" "$MODEL" "$EFFORT" \
  "$PROMPT_FILE" "$OUTPUT_FILE" >/dev/null 2>&1
runner_rc=$?
set -e
ended="$(date +%s)"
duration=$((ended - started))
response_bytes=0
if [[ -f "$OUTPUT_FILE" && ! -L "$OUTPUT_FILE" ]]; then
  response_bytes="$(wc -c < "$OUTPUT_FILE" | tr -d '[:space:]')"
  [[ "$response_bytes" =~ ^[0-9]+$ ]] || response_bytes=0
fi

if [[ "$runner_rc" -eq 0 && "$response_bytes" -gt 0 ]]; then
  emit_result true usable true "bounded live inference returned output" "$duration" "$response_bytes"
  exit 0
fi
if [[ "$runner_rc" -eq 124 || "$runner_rc" -eq 142 ]]; then
  emit_result false timeout true "bounded live inference timed out" "$duration" "$response_bytes"
else
  emit_result false failed true "runner exited with status $runner_rc" "$duration" "$response_bytes"
fi
exit 1
