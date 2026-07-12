#!/usr/bin/env bash
set -euo pipefail
# omnilane interactive lane configurator — writes ~/.omnilane/routing.local.yaml.
# Pure bash + a tty, no extra dependencies. Prefer editing routing.local.yaml
# by hand for scripted/non-interactive setups.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/i18n.sh"

LOCAL_FILE="$OMNILANE_HOME/routing.local.yaml"

# Curated suggestions only — "c" always allows free text so new models work.
CODEX_MODELS=("gpt-5.6-sol" "gpt-5.6-terra" "gpt-5.6-luna")
CODEX_EFFORTS=("xhigh" "max" "ultra" "high" "medium" "low" "minimal" "none")
CLAUDE_MODELS=("claude-opus-4-8" "claude-fable-5" "claude-sonnet-5" "claude-haiku-4-5")
CLAUDE_EFFORTS=("max" "xhigh" "high" "medium" "low" "-")
GEMINI_MODELS=("Gemini 3.1 Pro (High)" "Gemini 3.1 Pro (Low)" "Gemini 3.5 Flash (High)" "Gemini 3.5 Flash (Medium)" "Gemini 3.5 Flash (Low)")
GROK_MODELS=("grok-4.5" "grok-4.3")

pick() { # title, options... -> prints the chosen value
  local title="$1"; shift
  local opts=("$@") i choice
  echo "$title" >&2
  for i in "${!opts[@]}"; do printf '  %d) %s\n' "$((i + 1))" "${opts[$i]}" >&2; done
  printf '%s\n' "$(msg cfg_custom)" >&2
  while true; do
    read -rp "> " choice || choice=""
    [[ -z "$choice" ]] && { echo "$(msg cfg_aborted)" >&2; exit 1; }
    if [[ "$choice" == "c" ]]; then
      read -rp "$(msg cfg_custom_value)" choice || choice=""
      [[ -n "$choice" ]] && { printf '%s' "$choice"; return; }
      continue
    fi
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#opts[@]} )); then
      printf '%s' "${opts[$((choice - 1))]}"; return
    fi
    echo "$(msgf cfg_pick_or_c "${#opts[@]}")" >&2
  done
}

LANES=()
while IFS= read -r line; do
  [[ "$line" =~ ^([a-z][a-z0-9-]*): ]] && LANES+=("${BASH_REMATCH[1]}")
done < "$OMNILANE_REPO/routing.yaml"

echo "$(msg cfg_title)"
bash "$OMNILANE_REPO/scripts/dispatch.sh" --list
echo

OVERRIDES=()
while true; do
  echo "$(msg cfg_pick_lane)"
  for i in "${!LANES[@]}"; do printf '  %d) %s\n' "$((i + 1))" "${LANES[$i]}"; done
  read -rp "lane> " n || n=""
  [[ -z "$n" ]] && break
  if ! [[ "$n" =~ ^[0-9]+$ ]] || (( n < 1 || n > ${#LANES[@]} )); then
    echo "$(msgf cfg_pick_range "${#LANES[@]}")"; continue
  fi
  lane="${LANES[$((n - 1))]}"
  vendor="$(pick "$(msgf cfg_vendor_for "$lane")" codex claude grok gemini "vote (multi-model panel)" "exec (your own script/gate)" off)"
  [[ "$vendor" == exec* ]] && vendor="exec"
  [[ "$vendor" == vote* ]] && vendor="vote"
  if [[ "$vendor" == "off" ]]; then
    OVERRIDES+=("$lane: off - -")
    echo "-> $lane: off"; echo; continue
  fi
  case "$vendor" in
    codex)  model="$(pick "$(msg cfg_model)" "${CODEX_MODELS[@]}")";  effort="$(pick "$(msg cfg_effort)" "${CODEX_EFFORTS[@]}")" ;;
    claude) model="$(pick "$(msg cfg_model)" "${CLAUDE_MODELS[@]}")"; effort="$(pick "$(msg cfg_effort)" "${CLAUDE_EFFORTS[@]}")" ;;
    gemini) model="$(pick "$(msg cfg_model)" "${GEMINI_MODELS[@]}")"; effort="-" ;;
    grok)   model="$(pick "$(msg cfg_model)" "${GROK_MODELS[@]}")";   effort="-" ;;
    vote)   while true; do
              count="$(pick "$(msg cfg_voters_count)" "1" "2" "3" "4")"
              [[ "$count" =~ ^[1-4]$ ]] && break
              echo "$(msg cfg_voters_range)" >&2
            done
            model=""; remaining=(codex claude grok gemini)
            for ((s = 1; s <= count; s++)); do
              v="$(pick "$(msgf cfg_voter_n "$s")" "${remaining[@]}")"
              model+="${model:+,}$v"
              next=()
              for r in "${remaining[@]}"; do [[ "$r" == "$v" ]] || next+=("$r"); done
              remaining=("${next[@]}")
            done
            effort="$(pick "$(msg cfg_rounds)" "1" "2")"
            [[ "$effort" == "1" ]] && effort="-" ;;
    exec)   read -rp "$(msg cfg_exec_path)" model || model=""
            [[ -n "$model" ]] || { echo "$(msg cfg_empty_path)"; continue; }
            effort="-" ;;
    *)      model="$(pick "$(msg cfg_model)" "custom")";              effort="-" ;;
  esac
  [[ "$model" == *" "* ]] && model="\"$model\""
  OVERRIDES+=("$lane: $vendor $model $effort")
  echo "-> $lane: $vendor $model $effort"
  echo
done

[[ ${#OVERRIDES[@]} -gt 0 ]] || { echo "$(msg cfg_no_changes)"; exit 0; }

mkdir -p "$OMNILANE_HOME"
[[ -f "$LOCAL_FILE" ]] && cp "$LOCAL_FILE" "$LOCAL_FILE.bak"
{
  echo "# written by scripts/configure.sh on $(date +%F) — first match per lane wins"
  printf '%s\n' "${OVERRIDES[@]}"
  # keep earlier customizations below (new lines above shadow same-lane old ones)
  [[ -f "$LOCAL_FILE.bak" ]] && grep -v '^#' "$LOCAL_FILE.bak" || true
} > "$LOCAL_FILE"

echo "$(msgf cfg_wrote "$LOCAL_FILE")"
bash "$OMNILANE_REPO/scripts/dispatch.sh" --list
