#!/usr/bin/env bash
set -euo pipefail
# omnilane interactive lane configurator — writes ~/.omnilane/routing.local.yaml.
# Pure bash + a tty, no extra dependencies. Prefer editing routing.local.yaml
# by hand for scripted/non-interactive setups.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/i18n.sh"

LOCAL_FILE="$OMNILANE_HOME/routing.local.yaml"

# --- Non-interactive subcommands -------------------------------------------
# `configure set|get|unset|list` script the same routing.local.yaml the menu
# writes, so automation never needs a tty. No subcommand => interactive menu.

cfg_usage() {
  cat >&2 <<'EOF'
usage: configure                 interactive lane menu
       configure set LANE SPEC   set/override one lane. SPEC = "vendor model effort [| ...]" or "off".
                                 Quote the whole SPEC (and the model) when a model name has spaces.
       configure get LANE        show the effective routing line for LANE
       configure unset LANE      remove LANE's local override
       configure list            show current local overrides
       configure diff            show how local overrides change the effective table vs the defaults
EOF
}

# Reject shell-dangerous bytes while allowing the routing RHS grammar
# (chains with |, quoted "models with spaces", slashes, effort tokens).
cfg_spec_is_safe() {
  case "$1" in
    '') return 1 ;;
    *'$'*|*'`'*|*'\'*|*'#'*|*';'*|*'&'*|*'<'*|*'>'*|*$'\r'*|*$'\n'*) return 1 ;;
    *) return 0 ;;
  esac
}

cfg_lane_is_known() {
  local want="$1" line
  while IFS= read -r line; do
    case "$line" in "$want":*) return 0 ;; esac
  done < "$OMNILANE_REPO/routing.yaml"
  return 1
}

cfg_valid_lane() { [[ "$1" =~ ^[a-z][a-z0-9-]*$ ]]; }

cfg_set() {
  local lane="${1:-}"; shift || true
  local spec="$*"
  cfg_valid_lane "$lane" || { echo "omnilane: invalid lane '$lane'" >&2; exit 2; }
  cfg_lane_is_known "$lane" || { echo "omnilane: unknown lane '$lane' (see: omnilane list)" >&2; exit 2; }
  [[ -n "$spec" ]] || { echo "omnilane: missing routing spec for '$lane'" >&2; cfg_usage; exit 2; }
  cfg_spec_is_safe "$spec" || { echo 'omnilane: unsafe routing spec (not allowed: $ ` \ # ; & < > newlines)' >&2; exit 2; }

  mkdir -p "$OMNILANE_HOME"
  local had_file=0
  if [[ -f "$LOCAL_FILE" ]]; then had_file=1; cp "$LOCAL_FILE" "$LOCAL_FILE.bak"; fi
  local tmp="$LOCAL_FILE.tmp.$$"
  {
    echo "# updated by 'configure set' on $(date +%F) — first match per lane wins"
    echo "$lane: $spec"
    [[ "$had_file" -eq 1 ]] && grep -v '^#' "$LOCAL_FILE.bak" | grep -v "^$lane:" || true
  } > "$tmp"
  mv "$tmp" "$LOCAL_FILE"

  # Semantic guard: reject only a structural FAIL on THIS lane. Availability
  # WARN (validate exit 4) is fine — the vendor CLI may be legitimately absent.
  local validate_out
  validate_out="$(OMNILANE_HOME="$OMNILANE_HOME" bash "$OMNILANE_REPO/scripts/dispatch.sh" --validate 2>&1)" || true
  if printf '%s\n' "$validate_out" | grep -q "^FAIL $lane "; then
    if [[ "$had_file" -eq 1 ]]; then mv "$LOCAL_FILE.bak" "$LOCAL_FILE"; else /bin/rm -f "$LOCAL_FILE"; fi
    echo "omnilane: rejected — $(printf '%s\n' "$validate_out" | grep "^FAIL $lane " | head -1)" >&2
    exit 2
  fi
  [[ "$had_file" -eq 1 ]] && /bin/rm -f "$LOCAL_FILE.bak"
  echo "set $lane -> $spec"
}

cfg_get() {
  local lane="${1:-}"
  cfg_valid_lane "$lane" || { echo "omnilane: invalid lane '$lane'" >&2; exit 2; }
  local line
  line="$(OMNILANE_HOME="$OMNILANE_HOME" bash "$OMNILANE_REPO/scripts/dispatch.sh" --list 2>/dev/null | grep "^$lane:" | head -1 || true)"
  [[ -n "$line" ]] || { echo "omnilane: unknown lane '$lane' (see: omnilane list)" >&2; exit 2; }
  printf '%s\n' "$line"
}

cfg_unset() {
  local lane="${1:-}"
  cfg_valid_lane "$lane" || { echo "omnilane: invalid lane '$lane'" >&2; exit 2; }
  if [[ ! -f "$LOCAL_FILE" ]] || ! grep -q "^$lane:" "$LOCAL_FILE"; then
    echo "no local override for '$lane'"; return 0
  fi
  cp "$LOCAL_FILE" "$LOCAL_FILE.bak"
  grep -v "^$lane:" "$LOCAL_FILE.bak" > "$LOCAL_FILE" || true
  /bin/rm -f "$LOCAL_FILE.bak"
  echo "unset $lane (local override removed)"
}

cfg_list() {
  if [[ -f "$LOCAL_FILE" ]] && grep -qE '^[a-z]' "$LOCAL_FILE"; then
    cat "$LOCAL_FILE"
  else
    echo "no local overrides in $LOCAL_FILE"
  fi
}

# Diff the effective table (local wins) against a defaults-only resolution, so
# the user sees exactly which lanes their overrides change. Reuses dispatch.sh
# --list for both, so availability annotation and formatting stay consistent; a
# throwaway empty OMNILANE_HOME yields the defaults-only table.
cfg_diff() {
  if [[ ! -f "$LOCAL_FILE" ]] || ! grep -qE '^[a-z]' "$LOCAL_FILE"; then
    echo "no local overrides ($LOCAL_FILE); effective table equals the defaults"
    return 0
  fi
  local empty eff def changed=0 lane eff_line def_line
  empty="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-diff.XXXXXX")"
  eff="$(OMNILANE_HOME="$OMNILANE_HOME" bash "$OMNILANE_REPO/scripts/dispatch.sh" --list 2>/dev/null || true)"
  def="$(OMNILANE_HOME="$empty" bash "$OMNILANE_REPO/scripts/dispatch.sh" --list 2>/dev/null || true)"
  /bin/rm -rf "$empty"
  while IFS= read -r eff_line; do
    [[ "$eff_line" =~ ^([a-z][a-z0-9-]*): ]] || continue
    lane="${BASH_REMATCH[1]}"
    def_line="$(printf '%s\n' "$def" | grep "^$lane:" | head -1 || true)"
    if [[ "$eff_line" != "$def_line" ]]; then
      changed=1
      printf 'default> %s\n' "${def_line:-($lane not in defaults)}"
      printf 'local  > %s\n' "$eff_line"
      echo
    fi
  done <<DIFF_EFF
$eff
DIFF_EFF
  [[ "$changed" -eq 1 ]] || echo "local overrides present, but the effective table matches the defaults"
}

case "${1:-}" in
  set)   shift; cfg_set "$@"; exit $? ;;
  get)   shift; cfg_get "$@"; exit $? ;;
  unset) shift; cfg_unset "$@"; exit $? ;;
  list)  shift; cfg_list "$@"; exit $? ;;
  diff)  cfg_diff; exit $? ;;
  -h|--help|help) cfg_usage; exit 0 ;;
esac

# Curated suggestions only — "c" always allows free text so new models work.
CODEX_MODELS=("gpt-5.6-sol" "gpt-5.6-terra" "gpt-5.6-luna")
CODEX_EFFORTS=("xhigh" "max" "ultra" "high" "medium" "low" "minimal" "none")
CLAUDE_MODELS=("claude-opus-4-8" "claude-fable-5" "claude-sonnet-5" "claude-haiku-4-5")
CLAUDE_EFFORTS=("max" "xhigh" "high" "medium" "low" "-")
GEMINI_MODELS=("Gemini 3.1 Pro (High)" "Gemini 3.1 Pro (Low)" "Gemini 3.5 Flash (High)" "Gemini 3.5 Flash (Medium)" "Gemini 3.5 Flash (Low)")
GROK_MODELS=("grok-4.5" "grok-4.3")
KIMI_MODELS=("kimi-k3" "kimi-k2.7-code")
QWEN_MODELS=("qwen3-coder-plus" "qwen3-coder-flash")
# OpenCode models use provider/model form; OpenRouter models are catalog slugs.
OPENCODE_MODELS=("openrouter/anthropic/claude-sonnet-5" "openrouter/openai/gpt-5.6-sol" "opencode/default (leave model to opencode)")
OPENROUTER_MODELS=("anthropic/claude-sonnet-5" "openai/gpt-5.6-sol" "moonshotai/kimi-k3" "qwen/qwen3-coder-plus")

custom_value_is_safe() {
  case "$1" in
    *'$'*|*'`'*|*'"'*|*'\'*|*'#'*|*'|'*|*$'\r'*) return 1 ;;
    *) return 0 ;;
  esac
}

warn_unsafe_value() {
  echo 'omnilane: unsafe custom value (not allowed: $ ` " \ # |)' >&2
}

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
      if [[ -n "$choice" ]] && custom_value_is_safe "$choice"; then
        printf '%s' "$choice"; return
      fi
      [[ -n "$choice" ]] && warn_unsafe_value
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
  if [[ "$line" =~ ^([a-z][a-z0-9-]*): ]]; then
    lane="${BASH_REMATCH[1]}"
    [[ "$lane" == "consult" ]] || LANES+=("$lane")
  fi
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
  vendor="$(pick "$(msgf cfg_vendor_for "$lane")" codex claude grok gemini kimi qwen opencode openrouter "vote (multi-model panel)" "exec (your own script/gate)" off)"
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
    kimi)   model="$(pick "$(msg cfg_model)" "${KIMI_MODELS[@]}")";   effort="-" ;;
    qwen)   model="$(pick "$(msg cfg_model)" "${QWEN_MODELS[@]}")";   effort="-" ;;
    opencode)   model="$(pick "$(msg cfg_model)" "${OPENCODE_MODELS[@]}")"; effort="-"
                [[ "$model" == "opencode/default"* ]] && model="-" ;;
    openrouter) model="$(pick "$(msg cfg_model)" "${OPENROUTER_MODELS[@]}")"; effort="-" ;;
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
              # next is empty once every vendor is picked; the bare expansion
              # is an unbound-variable error under set -u on Bash 3.2.
              remaining=(${next[@]+"${next[@]}"})
            done
            effort="$(pick "$(msg cfg_rounds)" "1" "2")"
            [[ "$effort" == "1" ]] && effort="-" ;;
    exec)   read -rp "$(msg cfg_exec_path)" model || model=""
            [[ -n "$model" ]] || { echo "$(msg cfg_empty_path)"; continue; }
            effort="-" ;;
    *)      model="$(pick "$(msg cfg_model)" "custom")";              effort="-" ;;
  esac
  if ! custom_value_is_safe "$vendor" || ! custom_value_is_safe "$model" ||
     ! custom_value_is_safe "$effort" ||
     [[ "$vendor" == *[[:space:]]* || "$effort" == *[[:space:]]* ]]; then
    warn_unsafe_value; continue
  fi
  [[ "$model" == *[[:space:]]* ]] && model="\"$model\""
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
