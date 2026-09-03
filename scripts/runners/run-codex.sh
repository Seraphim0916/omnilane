#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: OpenAI Codex CLI
# Usage: run-codex.sh MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE
#   MODE = advise (read-only, ephemeral) | work (may edit files in WORKDIR)
#          | sysops (work minus the Seatbelt sandbox: service operations like
#            launchctl are denied under workspace-write, so sysops runs with
#            -s danger-full-access; explicit per-dispatch opt-in only)

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; MODEL="$3"; EFFORT="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"

CODEX_BIN="${CODEX_BIN:-codex}"
RUN_TIMEOUT="${OMNILANE_TIMEOUT:-600}"

THREAD_MODE="${OMNILANE_THREAD_MODE:-}"
THREAD_ID="${OMNILANE_THREAD_ID:-}"
if [[ -n "$THREAD_MODE" || -n "$THREAD_ID" ]]; then
  [[ "$THREAD_ID" =~ ^[A-Za-z0-9._:-]+$ && "${#THREAD_ID}" -le 256 ]] || {
    echo "omnilane: invalid Codex thread session id" >&2
    exit 2
  }
  case "$THREAD_MODE" in
    new) ;;
    resume) ;;
    *) echo "omnilane: invalid Codex thread mode" >&2; exit 2 ;;
  esac
fi

# --skip-git-repo-check: the operator chose WORKDIR explicitly; codex would
# otherwise refuse any directory that is not a trusted git repo.
# --json: without it codex writes nothing until it exits, so a watchdog kill
# leaves an empty progress log that looks identical to a run that never started.
if [[ "$THREAD_MODE" == "resume" ]]; then
  ARGS=(exec resume --json -m "$MODEL" -o "${OUTPUT_FILE}.tmp" --skip-git-repo-check)
else
  ARGS=(exec --json -m "$MODEL" -o "${OUTPUT_FILE}.tmp" --skip-git-repo-check)
fi
[[ -n "$EFFORT" && "$EFFORT" != "-" ]] && ARGS+=(-c "model_reasoning_effort=\"$EFFORT\"")
if [[ "$MODE" == "advise" ]]; then
  [[ -z "$THREAD_MODE" ]] && ARGS+=(--ephemeral)
  SANDBOX=read-only
elif [[ "$MODE" == "sysops" ]]; then
  SANDBOX=danger-full-access
else
  SANDBOX=workspace-write
fi
# `codex exec resume` has no -s/--sandbox flag (rejects it with exit 2); the
# same policy is only reachable there through the sandbox_mode config override.
if [[ "$THREAD_MODE" == "resume" ]]; then
  ARGS+=(-c "sandbox_mode=\"$SANDBOX\"")
else
  ARGS+=(-s "$SANDBOX")
fi
[[ "$THREAD_MODE" == "resume" ]] && ARGS+=("$THREAD_ID" -)

truncate_payload "$PROMPT_FILE" 140000

set +e
(
  cd "$WORKDIR" || exit 127
  # Subscription-login path: strip API-key env so the CLI uses its own auth.
  run_with_timeout "$RUN_TIMEOUT" env \
    -u OPENAI_API_KEY -u OPENAI_ORG_ID -u OPENAI_ORGANIZATION -u OPENAI_PROJECT -u OPENAI_API_BASE \
    OMNILANE_DEPTH=1 \
    "$CODEX_BIN" "${ARGS[@]}" < "$PROMPT_FILE" \
    > "${OUTPUT_FILE}.progress.log" 2> "${OUTPUT_FILE}.stderr.log"
)
RC=$?
set -e

if [[ "$RC" -eq 142 || "$RC" -eq 124 ]]; then
  {
    echo "omnilane: codex timed out after ${RUN_TIMEOUT}s. This does NOT identify the cause."
    echo "omnilane: an empty progress log is NOT evidence of a stall — check, in order:"
    echo "  1. the streamed events in ${OUTPUT_FILE}.progress.log — the last event says how far the run got"
    echo "  2. the rollout file below — if it stops at task_started, the request never left the CLI"
    echo "  3. your provider proxy's log for the run window: rate-limit responses, and whether a"
    echo "     request was ever sent upstream at all"
    # The rollout holds the full turn history a killed run never got to report;
    # thread_id is the only handle onto it once the process is gone.
    thread_id="$(head -1 "${OUTPUT_FILE}.progress.log" 2>/dev/null |
      sed -n 's/.*"thread_id":"\([^"]*\)".*/\1/p')"
    if [[ -n "$thread_id" ]]; then
      rollout="$(find "${CODEX_HOME:-$HOME/.codex}/sessions" -name "rollout-*-${thread_id}.jsonl" 2>/dev/null | head -1)"
      echo "omnilane: rollout: ${rollout:-not found (thread ${thread_id})}"
    else
      echo "omnilane: rollout: unknown — no thread_id in the progress log, so codex died before its first event"
    fi
  } >> "${OUTPUT_FILE}.stderr.log"
fi

if [[ -f "${OUTPUT_FILE}.tmp" ]]; then
  strip_ansi "${OUTPUT_FILE}.tmp"
  mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"
fi
# no -f: force-flag rm is blocked by some environments' destructive guards
[[ -s "${OUTPUT_FILE}.stderr.log" ]] || rm "${OUTPUT_FILE}.stderr.log" 2>/dev/null || true
exit "$RC"
