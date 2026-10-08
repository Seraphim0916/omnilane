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

cfg_set() (
  local lane="${1:-}"; shift || true
  local spec="$*"
  cfg_valid_lane "$lane" || { echo "omnilane: invalid lane '$lane'" >&2; exit 2; }
  cfg_lane_is_known "$lane" || { echo "omnilane: unknown lane '$lane' (see: omnilane list)" >&2; exit 2; }
  [[ -n "$spec" ]] || { echo "omnilane: missing routing spec for '$lane'" >&2; cfg_usage; exit 2; }
  cfg_spec_is_safe "$spec" || { echo 'omnilane: unsafe routing spec (not allowed: $ ` \ # ; & < > newlines)' >&2; exit 2; }

  mkdir -p "$OMNILANE_HOME"
  local had_file=0 file_mode="" tmp retain_status
  if [[ -f "$LOCAL_FILE" ]]; then
    had_file=1
    # GNU and BSD stat use different flags for the existing permission mode.
    file_mode="$(stat -c '%a' "$LOCAL_FILE" 2>/dev/null || stat -f '%Lp' "$LOCAL_FILE")"
  fi
  tmp="$(mktemp "$LOCAL_FILE.tmp.XXXXXX")"
  # Keep cleanup local to this command, including validation/publication errors.
  trap '/bin/rm -f -- "$tmp"' EXIT
  {
    echo "# updated by 'configure set' on $(date +%F) — first match per lane wins"
    echo "$lane: $spec"
    # Drop only our own stamp line and the lane being replaced; the user's own
    # comments in routing.local.yaml survive a set. Empty retained output is
    # valid, but a failed read must not publish a partially retained file.
    if [[ "$had_file" -eq 1 ]]; then
      if awk -v stamp="# updated by 'configure set'" -v lane="$lane:" \
        'index($0, stamp) != 1 && index($0, lane) != 1' "$LOCAL_FILE" 2>/dev/null; then
        :
      else
        retain_status=$?
        printf 'omnilane: configure set: failed to retain existing routing content (exit %s)\n' "$retain_status" >&2
        exit "$retain_status"
      fi
    fi
  } > "$tmp"

  # Validate the private candidate with the real local.sh configuration before
  # publishing. Known lint statuses still reject only a FAIL on THIS lane;
  # availability WARN and unrelated-lane errors remain acceptable.
  local validate_out validate_status=0
  validate_out="$(OMNILANE_HOME="$OMNILANE_HOME" OMNILANE_CONFIGURE_VALIDATE_FILE="$tmp" \
    bash "$OMNILANE_REPO/scripts/dispatch.sh" --validate 2>&1)" || validate_status=$?
  case "$validate_status" in
    0|2|4) ;;
    *)
      printf 'omnilane: configure set: failed to validate routing candidate (exit %s)\n' "$validate_status" >&2
      exit "$validate_status"
      ;;
  esac
  if printf '%s\n' "$validate_out" | grep -q "^FAIL $lane "; then
    echo "omnilane: rejected — $(printf '%s\n' "$validate_out" | grep "^FAIL $lane " | head -1)" >&2
    exit 2
  fi
  if [[ "$had_file" -eq 1 ]]; then
    chmod "$file_mode" "$tmp"
  else
    # Omitting 'who' makes chmod honor the caller's umask, like file creation.
    chmod '=rw' "$tmp"
  fi
  mv "$tmp" "$LOCAL_FILE"
  echo "set $lane -> $spec"
)

cfg_get() {
  local lane="${1:-}"
  cfg_valid_lane "$lane" || { echo "omnilane: invalid lane '$lane'" >&2; exit 2; }
  local table line status
  if table="$(OMNILANE_HOME="$OMNILANE_HOME" bash "$OMNILANE_REPO/scripts/dispatch.sh" --list 2>/dev/null)"; then
    :
  else
    status=$?
    printf 'omnilane: configure get: failed to inspect effective routing table (exit %s)\n' "$status" >&2
    return "$status"
  fi
  while IFS= read -r line; do
    case "$line" in
      "$lane":*) printf '%s\n' "$line"; return 0 ;;
    esac
  done <<< "$table"
  echo "omnilane: unknown lane '$lane' (see: omnilane list)" >&2
  return 2
}

cfg_unset() (
  local lane="${1:-}"
  cfg_valid_lane "$lane" || { echo "omnilane: invalid lane '$lane'" >&2; exit 2; }
  if [[ ! -f "$LOCAL_FILE" ]]; then
    echo "no local override for '$lane'"; exit 0
  fi
  local status tmp
  if grep -q "^$lane:" "$LOCAL_FILE" 2>/dev/null; then
    :
  else
    status=$?
    if [[ "$status" -eq 1 ]]; then
      echo "no local override for '$lane'"; exit 0
    fi
    printf 'omnilane: configure unset: failed to inspect existing routing content (exit %s)\n' "$status" >&2
    exit "$status"
  fi
  if tmp="$(mktemp "$LOCAL_FILE.tmp.XXXXXX" 2>/dev/null)"; then
    :
  else
    status=$?
    printf 'omnilane: configure unset: failed to prepare routing candidate (exit %s)\n' "$status" >&2
    exit "$status"
  fi
  trap '/bin/rm -f -- "$tmp"' EXIT
  # Retain every other row, including all comments and configure-set stamps.
  # Empty output is valid; a failed read must never reach the public file.
  if awk -v lane="$lane:" 'index($0, lane) != 1' "$LOCAL_FILE" 2>/dev/null > "$tmp"; then
    :
  else
    status=$?
    printf 'omnilane: configure unset: failed to retain existing routing content (exit %s)\n' "$status" >&2
    exit "$status"
  fi
  # Keep the existing in-place publication semantics. A write failure can leave
  # partial content, but must not report successful removal.
  if cat "$tmp" 2>/dev/null > "$LOCAL_FILE"; then
    :
  else
    status=$?
    printf 'omnilane: configure unset: failed to publish routing content (exit %s)\n' "$status" >&2
    exit "$status"
  fi
  echo "unset $lane (local override removed)"
)

cfg_list() {
  local status
  if [[ ! -f "$LOCAL_FILE" ]]; then
    echo "no local overrides in $LOCAL_FILE"
    return 0
  fi
  if grep -qE '^[a-z]' "$LOCAL_FILE" 2>/dev/null; then
    cat "$LOCAL_FILE"
  else
    status=$?
    if [[ "$status" -ne 1 ]]; then
      printf 'omnilane: configure list: failed to inspect existing routing content (exit %s)\n' "$status" >&2
      return "$status"
    fi
    echo "no local overrides in $LOCAL_FILE"
  fi
}

# Diff the effective table (local wins) against a defaults-only resolution, so
# the user sees exactly which lanes their overrides change. Reuses dispatch.sh
# --list for both, so availability annotation and formatting stay consistent.
# Keep local.sh for both inspections; only suppress the routing overlay when
# resolving defaults, so unexported provider paths still affect availability.
cfg_diff() {
  if [[ ! -f "$LOCAL_FILE" ]] || ! grep -qE '^[a-z]' "$LOCAL_FILE"; then
    echo "no local overrides ($LOCAL_FILE); effective table equals the defaults"
    return 0
  fi
  local eff def changed=0 lane eff_line def_line status
  if eff="$(OMNILANE_HOME="$OMNILANE_HOME" OMNILANE_CONFIGURE_DEFAULTS_ONLY=0 bash "$OMNILANE_REPO/scripts/dispatch.sh" --list 2>/dev/null)"; then
    :
  else
    status=$?
    printf 'omnilane: configure diff: failed to inspect effective routing table (exit %s)\n' "$status" >&2
    return "$status"
  fi
  if def="$(OMNILANE_HOME="$OMNILANE_HOME" OMNILANE_CONFIGURE_DEFAULTS_ONLY=1 bash "$OMNILANE_REPO/scripts/dispatch.sh" --list 2>/dev/null)"; then
    :
  else
    status=$?
    printf 'omnilane: configure diff: failed to inspect defaults routing table (exit %s)\n' "$status" >&2
    return "$status"
  fi
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

# Native CLI catalogs are pinned from each installed CLI's live model surface.
# Dynamic/API catalogs stay curated — "c" always accepts an exact model ID.
CODEX_MODELS=("gpt-6-astra" "gpt-6.1-sol" "gpt-6-sol" "gpt-6-luna" "gpt-5.6" "gpt-5.6-sol" "gpt-5.6-terra" "gpt-5.6-luna" "gpt-5.5" "gpt-5.4" "gpt-5.3-codex-spark")
CODEX_EFFORTS=("xhigh" "max" "ultra" "high" "medium" "low" "minimal" "none")
CLAUDE_MODELS=("default" "best" "fable" "opus" "sonnet" "haiku" "opus[1m]" "sonnet[1m]" "opusplan" "claude-fable-5" "claude-fable-5-1" "claude-opus-5-5" "claude-opus-5" "claude-sonnet-5-5" "claude-haiku-5-5" "claude-sonnet-5" "claude-opus-4-8" "claude-opus-4-7" "claude-opus-4-6" "claude-opus-4-5-20251101" "claude-sonnet-4-6" "claude-sonnet-4-5-20250929" "claude-haiku-4-5" "claude-haiku-4-5-20251001")
CLAUDE_EFFORTS=("max" "xhigh" "high" "medium" "low" "-")
GEMINI_MODELS=("gemini-3.8-flash-high" "gemini-3.8-flash-medium" "gemini-3.8-flash-low" "gemini-3.7-flash-high" "gemini-3.7-flash-medium" "gemini-3.7-flash-low" "gemini-3.6-flash-high" "gemini-3.6-flash-medium" "gemini-3.6-flash-low" "gemini-3.1-pro-high" "gemini-3.1-pro-low" "claude-sonnet-4-6" "claude-opus-4-6-thinking" "gpt-oss-120b-medium")
GROK_MODELS=("grok-4.7" "grok-4.6" "headroom-grok-build" "grok-4.3-official")
KIMI_MODELS=("kimi-k3" "kimi-k2.7-code" "kimi-k2.5")
QWEN_MODELS=("qwen3.7-max" "qwen3.7-plus" "qwen3.6-plus" "qwen3.5-plus" "qwen3-max-2026-01-23" "qwen3-coder-next" "qwen3-coder-plus" "qwen3-coder-flash")
# OpenCode models use provider/model form; OpenRouter models use catalog slugs.
OPENCODE_MODELS=("openrouter/anthropic/claude-opus-5" "openrouter/anthropic/claude-fable-5" "openrouter/anthropic/claude-sonnet-5" "openrouter/openai/gpt-5.6-sol" "openrouter/openai/gpt-5.6-terra" "openrouter/openai/gpt-5.6-luna" "openrouter/x-ai/grok-4.5" "openrouter/google/gemini-3.6-flash" "openrouter/moonshotai/kimi-k3" "openrouter/moonshotai/kimi-k2.7-code" "openrouter/qwen/qwen3.7-max" "openrouter/qwen/qwen3.7-plus" "openrouter/qwen/qwen3-coder-plus" "opencode/default (leave model to opencode)")
OPENROUTER_MODELS=("anthropic/claude-opus-5" "anthropic/claude-fable-5" "anthropic/claude-sonnet-5" "openai/gpt-5.6-sol" "openai/gpt-5.6-terra" "openai/gpt-5.6-luna" "x-ai/grok-4.5" "google/gemini-3.6-flash" "moonshotai/kimi-k3" "moonshotai/kimi-k2.7-code" "qwen/qwen3.7-max" "qwen/qwen3.7-plus" "qwen/qwen3-coder-plus")
# Direct-API OpenAI-compatible vendors (curl + <VENDOR>_API_KEY); slugs are
# suggestions — "c" free text covers anything each provider's /models lists.
DEEPSEEK_MODELS=("deepseek-v4-pro" "deepseek-v4-flash")
ZAI_MODELS=("glm-5.3" "glm-5.1" "glm-5" "glm-5-turbo" "glm-4.7" "glm-4.7-flashx" "glm-4.7-flash" "glm-4.6")
MISTRAL_MODELS=("devstral-latest" "devstral-small-latest" "codestral-latest" "mistral-medium-3-5" "mistral-medium-latest" "mistral-large-latest")
GROQ_MODELS=("groq/compound" "groq/compound-mini" "openai/gpt-oss-120b" "openai/gpt-oss-20b" "qwen/qwen3.6-27b" "llama-3.3-70b-versatile" "llama-3.1-8b-instant")
CEREBRAS_MODELS=("zai-glm-4.7" "gpt-oss-120b" "qwen-3-235b-a22b-instruct-2507" "qwen-3-32b" "llama3.1-8b")

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
  vendor="$(pick "$(msgf cfg_vendor_for "$lane")" codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras "vote (multi-model panel)" "exec (your own script/gate)" off)"
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
    deepseek)   model="$(pick "$(msg cfg_model)" "${DEEPSEEK_MODELS[@]}")"; effort="-" ;;
    zai)        model="$(pick "$(msg cfg_model)" "${ZAI_MODELS[@]}")";      effort="-" ;;
    mistral)    model="$(pick "$(msg cfg_model)" "${MISTRAL_MODELS[@]}")";  effort="-" ;;
    groq)       model="$(pick "$(msg cfg_model)" "${GROQ_MODELS[@]}")";     effort="-" ;;
    cerebras)   model="$(pick "$(msg cfg_model)" "${CEREBRAS_MODELS[@]}")"; effort="-" ;;
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
