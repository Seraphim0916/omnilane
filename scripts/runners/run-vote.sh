#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: vote — built-in multi-model opinion panel (roundtable-style).
# Usage: run-vote.sh MODE WORKDIR VENDORS ROUNDS PROMPT_FILE OUTPUT_FILE
#
# VENDORS is a comma list (e.g. codex,claude,grok). Each installed voter is
# asked the same question read-only; answers are collected side by side.
# ROUNDS (the lane's effort field): "-"/1 = one round; 2 = a second round where
# every voter sees the whole panel and responds ONLY to disagreements.
# The MAIN LOOP is the chair: it reads the opinions and owns the final call.
# Costs one quota hit per voter PER ROUND — that is the point, and the price.

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

MODE="$1"; WORKDIR="$2"; VENDORS="$3"; ROUNDS="$4"; PROMPT_FILE="$5"; OUTPUT_FILE="$6"
: "$MODE" # voters always run read-only regardless of mode
[[ "$ROUNDS" == "2" ]] || ROUNDS=1

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

voter_spec() { # vendor -> "model<TAB>effort"
  case "$1" in
    codex)  printf 'gpt-5.6-sol\thigh' ;;
    claude) printf 'claude-opus-4-8\thigh' ;;
    gemini) printf 'Gemini 3.1 Pro (High)\t-' ;;
    grok)   printf 'grok-4.5\t-' ;;
    *)      return 1 ;;
  esac
}

run_voter() { # vendor, prompt_file, out_file -> rc
  local v="$1" pf="$2" out="$3" spec model effort rc
  spec="$(voter_spec "$v")" || return 3
  model="${spec%%$'\t'*}"; effort="${spec##*$'\t'}"
  [[ "$v" == "codex" ]] && acquire_cwd_lock codex "$WORKDIR"
  set +e
  "$RUNNER_DIR/run-$v.sh" advise "$WORKDIR" "$model" "$effort" "$pf" "$out"
  rc=$?
  set -e
  [[ "$v" == "codex" ]] && release_cwd_lock
  return "$rc"
}

truncate_payload "$PROMPT_FILE" 100000

: > "$OUTPUT_FILE"
printf '# Round 1 — independent opinions\n\n' >> "$OUTPUT_FILE"
ok=0; OK_VOTERS=()
IFS=',' read -ra LIST <<< "$VENDORS"
for v in "${LIST[@]}"; do
  v="${v// /}"
  [[ -n "$v" ]] || continue
  if [[ "$v" == "exec" || "$v" == "vote" ]] || ! vendor_available "$v"; then
    printf '## %s — not installed, skipped\n\n' "$v" >> "$OUTPUT_FILE"
    continue
  fi
  tmp_out="$(mktemp)"
  if run_voter "$v" "$PROMPT_FILE" "$tmp_out" && [[ -s "$tmp_out" ]]; then
    { printf '## %s\n\n' "$v"; cat "$tmp_out"; printf '\n\n'; } >> "$OUTPUT_FILE"
    ok=$((ok + 1)); OK_VOTERS+=("$v")
  else
    printf '## %s — FAILED\n\n' "$v" >> "$OUTPUT_FILE"
  fi
done

if [[ "$ok" -lt 2 ]]; then
  echo "omnilane: vote needs >=2 successful voters (got $ok)" >&2
  exit 5
fi

if [[ "$ROUNDS" == "2" ]]; then
  R2_PROMPT="$(mktemp)"
  {
    printf 'You are one voter on a multi-model panel. The original question and every panelist'\''s Round 1 opinion follow. Respond ONLY to points where you disagree with the other opinions — be brief, cite which opinion you are rebutting, and do not restate agreement.\n\n--- Original question ---\n'
    cat "$PROMPT_FILE"
    printf '\n\n--- Round 1 panel opinions ---\n'
    cat "$OUTPUT_FILE"
  } > "$R2_PROMPT"
  truncate_payload "$R2_PROMPT" 100000
  printf '# Round 2 — rebuttals (disagreements only)\n\n' >> "$OUTPUT_FILE"
  for v in "${OK_VOTERS[@]}"; do
    tmp_out="$(mktemp)"
    if run_voter "$v" "$R2_PROMPT" "$tmp_out" && [[ -s "$tmp_out" ]]; then
      { printf '## %s\n\n' "$v"; cat "$tmp_out"; printf '\n\n'; } >> "$OUTPUT_FILE"
    else
      printf '## %s — FAILED in round 2\n\n' "$v" >> "$OUTPUT_FILE"
    fi
  done
fi
exit 0
