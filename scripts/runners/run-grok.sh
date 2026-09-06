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

THREAD_MODE="${OMNILANE_THREAD_MODE:-}"
THREAD_ID="${OMNILANE_THREAD_ID:-}"
THREAD_ARGS=()
if [[ -n "$THREAD_MODE" || -n "$THREAD_ID" ]]; then
  [[ "$THREAD_ID" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$ ]] || {
    echo "omnilane: invalid Grok thread session id" >&2
    exit 2
  }
  case "$THREAD_MODE" in
    new) THREAD_ARGS=(--session-id "$THREAD_ID") ;;
    resume) THREAD_ARGS=(--resume "$THREAD_ID") ;;
    *) echo "omnilane: invalid Grok thread mode" >&2; exit 2 ;;
  esac
fi

# Subscription OAuth path: an exhausted API key in env causes 403s.
unset XAI_API_KEY 2>/dev/null || true

truncate_payload "$PROMPT_FILE" 140000

MODE_ARGS=()
case "$MODE" in
  advise)
    # Native tool allowlisting plus deny rules form the read-only boundary.
    # Keep Bash visible so a requested mutation produces an actual policy
    # denial rather than mere model avoidance; deny wins over allow. Avoid the
    # filesystem sandbox here because it rejects a legitimate symlinked user
    # hooks root before provider startup.
    MODE_ARGS=(
      --permission-mode dontAsk
      # Hosted search uses strict internal IDs, unlike client-tool aliases.
      # Keep permission-rule class names below separate from tool selection.
      --tools 'Bash,Read,Glob,Grep,web_search,web_fetch'
      --allow Read --allow Glob --allow Grep --allow WebSearch --allow WebFetch
      --deny Bash --deny Edit --deny MCPTool
    )
    ;;
  work)
    # Grok child-network restriction is Linux-only. macOS work therefore
    # fails before provider startup instead of silently weakening policy.
    if [[ "$(uname -s)" == "Darwin" ]]; then
      echo "omnilane: Grok work requires enforced agent-tool network isolation; Grok sandbox network isolation is unavailable on macOS (use explicit --mode sysops or another work-capable vendor)" >&2
      exit 2
    fi
    MODE_ARGS=(--permission-mode acceptEdits --sandbox strict --disable-web-search)
    ;;
  sysops)
    MODE_ARGS=(--always-approve --sandbox off)
    ;;
  *)
    echo "omnilane: invalid Grok mode '$MODE'" >&2
    exit 2
    ;;
esac

LIVE_INBOX="${OMNILANE_INBOX:-}"
if [[ "$MODE" != "sysops" && -n "$LIVE_INBOX" ]]; then
  echo "omnilane: Grok $MODE live is unavailable because ACP exposes no enforceable restricted-mode policy; use single-shot $MODE or explicit --mode sysops" >&2
  exit 2
fi
if [[ -n "$LIVE_INBOX" && -p "$LIVE_INBOX" ]]; then
  EVENTS_FILE="${OUTPUT_FILE}.events.jsonl"
  STDERR_FILE="${OUTPUT_FILE}.stderr.log"
  PROGRESS_FILE="${OUTPUT_FILE}.progress.log"
  SESSION_ID_FILE="${OUTPUT_FILE}.session-id"
  GROK_LIVE_RUNNER="$(dirname "${BASH_SOURCE[0]}")/run-grok-live.py"

  for path in "$EVENTS_FILE" "$STDERR_FILE" "$PROGRESS_FILE" "$SESSION_ID_FILE"; do
    if [[ -L "$path" || ( -e "$path" && ! -f "$path" ) ]]; then
      echo "omnilane: unsafe Grok live artifact path" >&2
      exit 125
    fi
  done
  command -v python3 >/dev/null 2>&1 || {
    echo "omnilane: Grok live mode requires python3" >&2
    exit 127
  }
  [[ -f "$GROK_LIVE_RUNNER" ]] || {
    echo "omnilane: Grok live runner missing" >&2
    exit 127
  }
  (umask 077; : > "$EVENTS_FILE"; : > "$STDERR_FILE")

  set +e
  (
    cd "$WORKDIR" || exit 127
    run_with_timeout "$RUN_TIMEOUT" env -u XAI_API_KEY \
      OMNILANE_DEPTH=1 \
      python3 "$GROK_LIVE_RUNNER" \
        --grok-bin "$GROK_BIN" \
        --cwd "$WORKDIR" \
        --model "$MODEL" \
        --mode "$MODE" \
        --inbox "$LIVE_INBOX" \
        --events "$EVENTS_FILE" \
        --output "$OUTPUT_FILE" \
        --progress "$PROGRESS_FILE" \
        --session-id-file "$SESSION_ID_FILE" \
        2> "$STDERR_FILE"
  )
  RC=$?
  set -e
  [[ -s "$STDERR_FILE" ]] || rm "$STDERR_FILE" 2>/dev/null || true
  exit "$RC"
fi

# Advise denies MCPTool, so another session's live context-mode MCP is not
# available to this agent. Give its readiness hook a private job scope instead
# of letting the hook redirect native WebFetch to an inaccessible MCP tool.
# This does not disable hooks: their security decisions still run normally.
if [[ "$MODE" == "advise" ]]; then
  if [[ -n "${CONTEXT_MODE_MCP_SENTINEL_DIR:-}" ]]; then
    echo "omnilane: Grok advise needs its own MCP readiness scope; conflicting CONTEXT_MODE_MCP_SENTINEL_DIR was left unchanged" >&2
    exit 2
  fi
  GROK_MCP_READINESS_DIR="$(umask 077; mktemp -d "${TMPDIR:-/tmp}/omnilane-grok-mcp.XXXXXX")" || {
    echo "omnilane: could not create private Grok MCP readiness scope" >&2
    exit 125
  }
  export CONTEXT_MODE_MCP_SENTINEL_DIR="$GROK_MCP_READINESS_DIR"
  # Remove only our empty leaf. Never remove foreign markers or a live server's
  # newly created state if a future Grok version starts an MCP there.
  trap 'rmdir "$GROK_MCP_READINESS_DIR" 2>/dev/null || echo "omnilane: retained nonempty Grok MCP readiness scope: $GROK_MCP_READINESS_DIR" >&2' EXIT
fi

ARGS=(--cwd "$WORKDIR" --model "$MODEL"
      --no-memory --no-subagents --no-plan --no-alt-screen
      --output-format plain --verbatim --prompt-file "$PROMPT_FILE")
[[ ${#THREAD_ARGS[@]} -eq 0 ]] || ARGS+=("${THREAD_ARGS[@]}")
ARGS+=("${MODE_ARGS[@]}")
# Web/X search stays ON by default for advise and sysops.
if [[ "$MODE" != "work" && "${OMNILANE_GROK_NO_WEB:-0}" == "1" ]]; then
  ARGS+=(--disable-web-search)
fi

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
