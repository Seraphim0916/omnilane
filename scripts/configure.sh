#!/usr/bin/env bash
set -euo pipefail
# omnilane interactive lane configurator — writes ~/.omnilane/routing.local.yaml.
# Pure bash + a tty, no extra dependencies. Prefer editing routing.local.yaml
# by hand for scripted/non-interactive setups.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

LOCAL_FILE="$OMNILANE_HOME/routing.local.yaml"

# Curated suggestions only — "c" always allows free text so new models work.
CODEX_MODELS=("gpt-5.6-sol" "gpt-5.6-terra" "gpt-5.6-luna")
CODEX_EFFORTS=("xhigh" "max" "ultra" "high" "medium" "low" "minimal" "none")
CLAUDE_MODELS=("claude-opus-4-8" "claude-sonnet-5" "claude-haiku-4-5")
CLAUDE_EFFORTS=("max" "xhigh" "high" "medium" "low" "-")
GEMINI_MODELS=("Gemini 3.1 Pro (High)" "Gemini 3.1 Pro (Low)" "Gemini 3.5 Flash (High)" "Gemini 3.5 Flash (Medium)" "Gemini 3.5 Flash (Low)")
GROK_MODELS=("grok-4.5" "grok-4.3")

pick() { # title, options... -> prints the chosen value
  local title="$1"; shift
  local opts=("$@") i choice
  echo "$title" >&2
  for i in "${!opts[@]}"; do printf '  %d) %s\n' "$((i + 1))" "${opts[$i]}" >&2; done
  printf '  c) custom (type your own)\n' >&2
  while true; do
    read -rp "> " choice || choice=""
    [[ -z "$choice" ]] && { echo "aborted" >&2; exit 1; }
    if [[ "$choice" == "c" ]]; then
      read -rp "custom value: " choice || choice=""
      [[ -n "$choice" ]] && { printf '%s' "$choice"; return; }
      continue
    fi
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#opts[@]} )); then
      printf '%s' "${opts[$((choice - 1))]}"; return
    fi
    echo "pick 1-${#opts[@]} or c" >&2
  done
}

LANES=()
while IFS= read -r line; do
  [[ "$line" =~ ^([a-z][a-z0-9-]*): ]] && LANES+=("${BASH_REMATCH[1]}")
done < "$OMNILANE_REPO/routing.yaml"

echo "omnilane lane configurator — current effective routing:"
bash "$OMNILANE_REPO/scripts/dispatch.sh" --list
echo

OVERRIDES=()
while true; do
  echo "Pick a lane number to override, or press Enter to finish:"
  for i in "${!LANES[@]}"; do printf '  %d) %s\n' "$((i + 1))" "${LANES[$i]}"; done
  read -rp "lane> " n || n=""
  [[ -z "$n" ]] && break
  if ! [[ "$n" =~ ^[0-9]+$ ]] || (( n < 1 || n > ${#LANES[@]} )); then
    echo "pick 1-${#LANES[@]} or Enter"; continue
  fi
  lane="${LANES[$((n - 1))]}"
  vendor="$(pick "vendor for '$lane':" codex claude grok gemini "vote (multi-model panel)" "exec (your own script/gate)" off)"
  [[ "$vendor" == exec* ]] && vendor="exec"
  [[ "$vendor" == vote* ]] && vendor="vote"
  if [[ "$vendor" == "off" ]]; then
    OVERRIDES+=("$lane: off - -")
    echo "-> $lane: off"; echo; continue
  fi
  case "$vendor" in
    codex)  model="$(pick "model:" "${CODEX_MODELS[@]}")";  effort="$(pick "effort:" "${CODEX_EFFORTS[@]}")" ;;
    claude) model="$(pick "model:" "${CLAUDE_MODELS[@]}")"; effort="$(pick "effort:" "${CLAUDE_EFFORTS[@]}")" ;;
    gemini) model="$(pick "model:" "${GEMINI_MODELS[@]}")"; effort="-" ;;
    grok)   model="$(pick "model:" "${GROK_MODELS[@]}")";   effort="-" ;;
    vote)   while true; do
              count="$(pick "how many voters? (each costs one call per round)" "1" "2" "3" "4")"
              [[ "$count" =~ ^[1-4]$ ]] && break
              echo "voters must be 1-4" >&2
            done
            model=""; remaining=(codex claude grok gemini)
            for ((s = 1; s <= count; s++)); do
              v="$(pick "voter $s:" "${remaining[@]}")"
              model+="${model:+,}$v"
              next=()
              for r in "${remaining[@]}"; do [[ "$r" == "$v" ]] || next+=("$r"); done
              remaining=("${next[@]}")
            done
            effort="$(pick "rounds (2 = voters rebut each other):" "1" "2")"
            [[ "$effort" == "1" ]] && effort="-" ;;
    exec)   read -rp "script path (gets MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE): " model || model=""
            [[ -n "$model" ]] || { echo "empty path, skipped"; continue; }
            effort="-" ;;
    *)      model="$(pick "model:" "custom")";              effort="-" ;;
  esac
  [[ "$model" == *" "* ]] && model="\"$model\""
  OVERRIDES+=("$lane: $vendor $model $effort")
  echo "-> $lane: $vendor $model $effort"
  echo
done

[[ ${#OVERRIDES[@]} -gt 0 ]] || { echo "no changes."; exit 0; }

mkdir -p "$OMNILANE_HOME"
[[ -f "$LOCAL_FILE" ]] && cp "$LOCAL_FILE" "$LOCAL_FILE.bak"
{
  echo "# written by scripts/configure.sh on $(date +%F) — first match per lane wins"
  printf '%s\n' "${OVERRIDES[@]}"
  # keep earlier customizations below (new lines above shadow same-lane old ones)
  [[ -f "$LOCAL_FILE.bak" ]] && grep -v '^#' "$LOCAL_FILE.bak" || true
} > "$LOCAL_FILE"

echo "wrote $LOCAL_FILE — effective routing now:"
bash "$OMNILANE_REPO/scripts/dispatch.sh" --list
