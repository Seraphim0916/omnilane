#!/usr/bin/env bash
set -euo pipefail
# omnilane dispatch — one routing table, any harness.
#
# Usage:
#   dispatch.sh [--background] [--dry-run] [--mode advise|work] [--workdir DIR]
#               [--vendor V] [--model M] [--effort E] [--timeout SECONDS]
#               [--job-timeout SECONDS] LANE "TASK TEXT"
#   dispatch.sh [--json] --list [--json]
#   dispatch.sh [--json] --explain LANE [--json]
#   dispatch.sh [--json] --validate [--json]
#
# TASK TEXT of "-" reads the task from stdin.
#
# A lane line may hold a fallback chain:
#   lane: vendor model effort | vendor model effort | off
# The first candidate whose vendor CLI exists on this machine wins, so the same
# table degrades gracefully for people with fewer subscriptions.
#
# The watchdog caps EACH CLI invocation, highest priority first:
#   --timeout SECONDS  >  OMNILANE_TIMEOUT_<LANE>  >  OMNILANE_TIMEOUT  >  600
# The per-lane knob is the lane upper-cased with "-" turned into "_"
# (hard-judgment -> OMNILANE_TIMEOUT_HARD_JUDGMENT), so it can live in local.sh.
# This is a per-call hang-guard, NOT a whole-job budget: a retrying vendor
# (grok, up to OMNILANE_GROK_MAX_ATTEMPTS) or the vote panel (voters x rounds)
# spawns several CLI calls, so total wall-clock can be a multiple of this value.
# A separate --job-timeout can cap lock wait plus all calls in this dispatch.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODE="advise"; WORKDIR="$PWD"; BACKGROUND=0; DRY_RUN=0
OVERRIDE_VENDOR=""; OVERRIDE_MODEL=""; OVERRIDE_EFFORT=""; OVERRIDE_TIMEOUT=""
OVERRIDE_JOB_TIMEOUT=""

usage_error() {
  echo 'usage: dispatch.sh [--background] [--dry-run] [flags] LANE "TASK" | [--json] --list|--validate [--json] | [--json] --explain LANE [--json] | --help' >&2
  exit 2
}

print_usage() {
  cat <<'EOF'
usage: dispatch.sh [flags] LANE "TASK"
       dispatch.sh [--json] --list | --explain LANE | --validate
       dispatch.sh --help

Dispatch one task to the first available vendor CLI in LANE's fallback chain.
A TASK of "-" reads the task text from stdin.

flags:
  --background           run in the background and print the JOB_ID
  --dry-run              print the fully resolved dispatch plan and stop
                         before any provider call or job state
  --mode advise|work     advise (read-only, default) or work (may edit files)
  --workdir DIR          working directory handed to the vendor CLI
  --vendor V             pin one configured vendor (codex|claude|grok|gemini)
  --model M              override the routed model
  --effort E             override the routed effort
  --timeout SECONDS      cap each CLI call (default 600)
  --job-timeout SECONDS  cap the whole dispatch (lock wait plus all calls)

read-only queries (no provider call, no job state; --json for one envelope):
  --list                 effective routing table (local overrides win)
  --explain LANE         candidate availability for one lane
  --validate             lint the effective routing table
  --help, -h             this help
EOF
}

json_escape() {
  local s="$1" out="" ch escaped code i
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:i:1}"
    case "$ch" in
      '"') out="$out\\\"" ;;
      '\\') out="$out\\\\" ;;
      $'\b') out="$out\\b" ;;
      $'\f') out="$out\\f" ;;
      $'\n') out="$out\\n" ;;
      $'\r') out="$out\\r" ;;
      $'\t') out="$out\\t" ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'$ch"
        # Bash 3.2 on macOS reports UTF-8 bytes above 0x7f as signed values.
        # Only non-negative C0 bytes are JSON control characters.
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

# Run one read-only inspection command once, preserving its human output and
# exit status inside a stable JSON envelope. This avoids a second routing
# implementation drifting from the human CLI contract.
emit_json_inspection() {
  local command="$1" output rc ok=false first=1 line
  shift
  if output="$("$@" 2>&1)"; then rc=0; else rc=$?; fi
  [[ "$rc" -eq 0 ]] && ok=true
  printf '{"schema_version":1,"command":"%s","ok":%s,"exit_code":%d,"lines":[' \
    "$(json_escape "$command")" "$ok" "$rc"
  if [[ -n "$output" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$first" -eq 1 ]] || printf ','
      first=0
      printf '"%s"' "$(json_escape "$line")"
    done <<< "$output"
  fi
  printf ']}\n'
  exit "$rc"
}

print_dry_run_value() {
  printf '%s=' "$1"
  printf '%q\n' "$2"
}

print_dry_run_plan() {
  local background=no task_source=argument write_worktree=no job_timeout=disabled
  [[ "$BACKGROUND" -eq 1 ]] && background=yes
  [[ "$TASK" == "-" ]] && task_source=stdin
  [[ "$MODE" == "work" ]] && write_worktree=yes
  [[ -n "$JOB_TIMEOUT" ]] && job_timeout="$JOB_TIMEOUT"
  printf 'dry_run=yes\n'
  print_dry_run_value lane "$LANE"
  print_dry_run_value vendor "$VENDOR"
  print_dry_run_value model "$MODEL"
  print_dry_run_value effort "$EFFORT"
  print_dry_run_value mode "$MODE"
  print_dry_run_value workdir "$WORKDIR"
  printf 'timeout=%s\n' "$TIMEOUT"
  printf 'job_timeout=%s\n' "$job_timeout"
  printf 'candidate=%s/%s\n' "$RESOLVED_IDX" "$RESOLVED_TOTAL"
  printf 'background=%s\n' "$background"
  printf 'task_source=%s\n' "$task_source"
  printf 'provider_invoked=no\n'
  printf 'job_state_created=no\n'
  printf 'would_invoke_provider=yes\n'
  printf 'would_create_job=yes\n'
  printf 'would_write_worktree=%s\n' "$write_worktree"
}

raw_lane_line() { # LANE -> chain text (comments stripped); local file wins
  local lane="$1" f line
  for f in "$OMNILANE_HOME/routing.local.yaml" "$OMNILANE_REPO/routing.yaml"; do
    [[ -f "$f" ]] || continue
    line="$(grep -E "^${lane}:" "$f" | head -1 | sed 's/#.*$//' | cut -d: -f2-)" || true
    [[ -n "${line// /}" ]] && { printf '%s\n' "$line"; return 0; }
  done
  return 1
}

# Split one routing segment without invoking the shell. Double quotes group a
# model containing spaces; every other character is literal data.
parse_lane_segment() {
  local input="$1" token="" ch i in_quote=0 have_token=0
  PARSED_FIELDS=()
  for ((i = 0; i < ${#input}; i++)); do
    ch="${input:i:1}"
    case "$ch" in
      '"')
        if [[ "$in_quote" -eq 1 ]]; then in_quote=0; else in_quote=1; fi
        have_token=1
        ;;
      ' '|$'\t')
        if [[ "$in_quote" -eq 1 ]]; then
          token="$token$ch"
        elif [[ "$have_token" -eq 1 ]]; then
          PARSED_FIELDS+=("$token"); token=""; have_token=0
        fi
        ;;
      *) token="$token$ch"; have_token=1 ;;
    esac
  done
  [[ "$in_quote" -eq 0 ]] || return 1
  [[ "$have_token" -eq 1 ]] && PARSED_FIELDS+=("$token")
  [[ ${#PARSED_FIELDS[@]} -gt 0 ]]
}

routing_candidate_available() {
  local vendor="$1" model="${2:-}" script
  if [[ "$vendor" == "exec" ]]; then
    script="$(expand_home_path "$model")"
    [[ -n "$script" && -f "$script" && -x "$script" ]]
  else
    vendor_available "$vendor"
  fi
}

# Pick the first candidate whose vendor CLI is present ("off" always matches).
# Sets RESOLVED_SPEC / RESOLVED_FIELDS / RESOLVED_IDX / RESOLVED_TOTAL.
resolve_chain() {
  local chain="$1" requested_vendor="${2:-}" seg i=0 vendor
  RESOLVED_SPEC=""; RESOLVED_IDX=0; RESOLVED_TOTAL=0; RESOLVED_FIELDS=()
  local SEGS=() F=()
  IFS='|' read -ra SEGS <<< "$chain"
  RESOLVED_TOTAL="${#SEGS[@]}"
  # ${SEGS[@]} on an empty chain is an unbound-variable error under set -u on
  # Bash 3.2 and would abort --list mid-table; the guard makes it iterate zero
  # times instead.
  for seg in ${SEGS[@]+"${SEGS[@]}"}; do
    i=$((i + 1))
    [[ -n "${seg// /}" ]] || continue
    parse_lane_segment "$seg" || {
      echo "omnilane: malformed quoted routing segment: $seg" >&2
      return 2
    }
    F=("${PARSED_FIELDS[@]}")
    vendor="${F[0]:-}"
    if [[ -n "$requested_vendor" ]]; then
      [[ "$vendor" == "$requested_vendor" ]] || continue
      RESOLVED_SPEC="$seg"; RESOLVED_FIELDS=("${F[@]}"); RESOLVED_IDX="$i"
      if routing_candidate_available "$vendor" "${F[1]:-}"; then return 0; fi
      return 4
    fi
    if [[ "$vendor" == "off" ]] || routing_candidate_available "$vendor" "${F[1]:-}"; then
      RESOLVED_SPEC="$seg"; RESOLVED_FIELDS=("${F[@]}")
      RESOLVED_IDX="$i"; return 0
    fi
  done
  [[ -n "$requested_vendor" ]] && return 5
  return 1
}

print_effective_routing() {
  local seen=" " f line lane chain spec note
  for f in "$OMNILANE_HOME/routing.local.yaml" "$OMNILANE_REPO/routing.yaml"; do
    [[ -f "$f" ]] || continue
    while IFS= read -r line; do
      [[ "$line" =~ ^([a-z][a-z0-9-]*): ]] || continue
      lane="${BASH_REMATCH[1]}"
      [[ "$seen" == *" $lane "* ]] && continue
      seen="$seen$lane "
      chain="${line%%#*}"; chain="${chain#*:}"
      if resolve_chain "$chain"; then
        spec="$(printf '%s' "$RESOLVED_SPEC" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        note=""
        [[ "$RESOLVED_IDX" -gt 1 ]] && note="   # fallback ($RESOLVED_IDX/$RESOLVED_TOTAL)"
        printf '%-17s%s%s\n' "$lane:" "$spec" "$note"
      else
        printf '%-17s%s\n' "$lane:" "unavailable   # no vendor CLI found in chain"
      fi
    done < "$f"
  done
}

explain_lane() {
  local lane="$1" chain seg i=0 total selected=0 vendor model effort status
  local available=0
  local SEGS=() F=()
  [[ "$lane" =~ ^[a-z][a-z0-9-]*$ ]] || {
    echo "omnilane: invalid lane name" >&2
    return 2
  }
  chain="$(raw_lane_line "$lane")" || {
    echo "omnilane: unknown lane '$lane' (try --list)" >&2
    return 2
  }
  IFS='|' read -ra SEGS <<< "$chain"
  total="${#SEGS[@]}"
  printf 'lane: %s\n' "$lane"
  for seg in "${SEGS[@]}"; do
    i=$((i + 1))
    [[ -n "${seg// /}" ]] || continue
    parse_lane_segment "$seg" || {
      echo "omnilane: malformed quoted routing segment: $seg" >&2
      return 2
    }
    F=("${PARSED_FIELDS[@]}")
    vendor="${F[0]:-}"
    model="${F[1]:--}"
    effort="${F[2]:--}"
    available=0
    status="unavailable"
    if [[ "$vendor" == "off" ]]; then
      available=1
      status="available-disabled"
    elif routing_candidate_available "$vendor" "$model"; then
      available=1
      status="available"
    fi
    if [[ "$available" -eq 1 && "$selected" -eq 0 ]]; then
      selected="$i"
      if [[ "$vendor" == "off" ]]; then status="selected-disabled"; else status="selected"; fi
    elif [[ "$available" -eq 1 ]]; then
      status="available-not-selected"
    fi
    printf 'candidate %d: vendor=%s model=%s effort=%s status=%s\n' \
      "$i" "$vendor" "$model" "$effort" "$status"
  done
  if [[ "$selected" -gt 0 ]]; then
    printf 'decision: candidate %d/%d\n' "$selected" "$total"
    return 0
  fi
  printf 'decision: unavailable\n'
  return 4
}

validate_routing() {
  local effective_seen=" " file_seen f line content lane chain seg vendor
  local line_no i total selected lane_invalid invalid=0 unreachable=0
  local SEGS=() F=()
  for f in "$OMNILANE_HOME/routing.local.yaml" "$OMNILANE_REPO/routing.yaml"; do
    [[ -f "$f" ]] || continue
    file_seen=" "
    line_no=0
    while IFS= read -r line || [[ -n "$line" ]]; do
      line_no=$((line_no + 1))
      content="${line%%#*}"
      [[ -n "${content//[[:space:]]/}" ]] || continue
      if ! [[ "$content" =~ ^([a-z][a-z0-9-]*):[[:space:]]*(.*)$ ]]; then
        printf 'FAIL line-%d invalid-line\n' "$line_no"
        invalid=$((invalid + 1))
        continue
      fi
      lane="${BASH_REMATCH[1]}"
      chain="${BASH_REMATCH[2]}"
      if [[ "$file_seen" == *" $lane "* ]]; then
        printf 'FAIL %s duplicate-lane\n' "$lane"
        invalid=$((invalid + 1))
        continue
      fi
      file_seen="$file_seen$lane "
      [[ "$effective_seen" == *" $lane "* ]] && continue
      effective_seen="$effective_seen$lane "
      IFS='|' read -ra SEGS <<< "$chain"
      total="${#SEGS[@]}"
      if [[ "$total" -eq 0 ]]; then
        printf 'FAIL %s empty-chain\n' "$lane"
        invalid=$((invalid + 1))
        continue
      fi
      selected=0
      lane_invalid=0
      i=0
      for seg in "${SEGS[@]}"; do
        i=$((i + 1))
        if [[ -z "${seg//[[:space:]]/}" ]]; then
          printf 'FAIL %s candidate=%d empty-segment\n' "$lane" "$i"
          lane_invalid=1
          break
        fi
        if ! parse_lane_segment "$seg"; then
          printf 'FAIL %s candidate=%d malformed-quotes\n' "$lane" "$i"
          lane_invalid=1
          break
        fi
        F=("${PARSED_FIELDS[@]}")
        vendor="${F[0]:-}"
        if [[ "$vendor" == "off" ]]; then
          if [[ "${#F[@]}" -ne 1 && "${#F[@]}" -ne 3 ]]; then
            printf 'FAIL %s candidate=%d field-count=%d\n' "$lane" "$i" "${#F[@]}"
            lane_invalid=1
            break
          fi
        elif [[ "${#F[@]}" -ne 3 ]]; then
          printf 'FAIL %s candidate=%d field-count=%d\n' "$lane" "$i" "${#F[@]}"
          lane_invalid=1
          break
        fi
        if ! [[ "$vendor" =~ ^(codex|claude|grok|gemini|exec|off)$ ]]; then
          printf 'FAIL %s unknown-vendor=%s\n' "$lane" "$vendor"
          lane_invalid=1
          break
        fi
        if printf '%s%s%s' "${F[0]}" "${F[1]:-}" "${F[2]:-}" | LC_ALL=C grep -q '[[:cntrl:]]'; then
          printf 'FAIL %s candidate=%d control-character\n' "$lane" "$i"
          lane_invalid=1
          break
        fi
        if [[ "$selected" -eq 0 ]] && { [[ "$vendor" == "off" ]] || routing_candidate_available "$vendor" "${F[1]}"; }; then
          selected="$i"
        fi
      done
      if [[ "$lane_invalid" -eq 1 ]]; then
        invalid=$((invalid + 1))
      elif [[ "$selected" -eq 0 ]]; then
        printf 'WARN %s no-candidate-available\n' "$lane"
        unreachable=$((unreachable + 1))
      else
        parse_lane_segment "${SEGS[$((selected - 1))]}"
        printf 'PASS %s selected=%d/%d vendor=%s\n' \
          "$lane" "$selected" "$total" "${PARSED_FIELDS[0]}"
      fi
    done < "$f"
  done
  [[ "$invalid" -eq 0 ]] || return 2
  [[ "$unreachable" -eq 0 ]] || return 4
  return 0
}

# Inspection modes are deliberately parsed before dispatch flags. JSON may be
# placed before or after the inspection command, but can never decorate a real
# dispatch and therefore cannot accidentally create job state.
JSON_INSPECTION=0
if [[ "${1:-}" == "--json" ]]; then
  JSON_INSPECTION=1
  shift
  [[ $# -gt 0 ]] || usage_error
fi
case "${1:-}" in
  --help|-h)
    [[ "$JSON_INSPECTION" -eq 0 && $# -eq 1 ]] || usage_error
    print_usage
    exit 0 ;;
  --list)
    if [[ "${2:-}" == "--json" ]]; then
      [[ "$JSON_INSPECTION" -eq 0 && $# -eq 2 ]] || usage_error
      JSON_INSPECTION=1
    else
      [[ $# -eq 1 ]] || usage_error
    fi
    if [[ "$JSON_INSPECTION" -eq 1 ]]; then
      emit_json_inspection list print_effective_routing
    fi
    print_effective_routing
    exit 0
    ;;
  --explain)
    [[ $# -ge 2 ]] || usage_error
    if [[ "${3:-}" == "--json" ]]; then
      [[ "$JSON_INSPECTION" -eq 0 && $# -eq 3 ]] || usage_error
      JSON_INSPECTION=1
    else
      [[ $# -eq 2 ]] || usage_error
    fi
    if [[ "$JSON_INSPECTION" -eq 1 ]]; then
      emit_json_inspection explain explain_lane "$2"
    fi
    explain_lane "$2"
    exit $?
    ;;
  --validate)
    if [[ "${2:-}" == "--json" ]]; then
      [[ "$JSON_INSPECTION" -eq 0 && $# -eq 2 ]] || usage_error
      JSON_INSPECTION=1
    else
      [[ $# -eq 1 ]] || usage_error
    fi
    if [[ "$JSON_INSPECTION" -eq 1 ]]; then
      emit_json_inspection validate validate_routing
    fi
    validate_routing
    exit $?
    ;;
  *)
    [[ "$JSON_INSPECTION" -eq 0 ]] || usage_error
    ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list|--explain|--validate|--json) usage_error ;;
    --background) BACKGROUND=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --mode|--workdir|--vendor|--model|--effort|--timeout|--job-timeout)
      # Value-taking flags: a missing value must be a clean usage error (exit 2),
      # not a `set -u` "unbound variable" crash on $2.
      [[ $# -ge 2 ]] || { echo "omnilane: $1 needs a value" >&2; exit 2; }
      case "$1" in
        --mode) MODE="$2" ;;
        --workdir) WORKDIR="$2" ;;
        --vendor) OVERRIDE_VENDOR="$2" ;;
        --model) OVERRIDE_MODEL="$2" ;;
        --effort) OVERRIDE_EFFORT="$2" ;;
        --timeout) OVERRIDE_TIMEOUT="$2" ;;
        --job-timeout) OVERRIDE_JOB_TIMEOUT="$2" ;;
      esac
      shift 2 ;;
    -*) echo "omnilane: unknown flag" >&2; exit 2 ;;
    *) break ;;
  esac
done

[[ $# -ge 1 ]] || usage_error
[[ $# -ge 2 ]] || { echo "omnilane: missing task text (use - for stdin)" >&2; exit 2; }
[[ $# -eq 2 ]] || {
  echo 'omnilane: unexpected extra arguments; quote a multiword task' >&2
  exit 2
}
LANE="$1"
TASK="$2"
[[ "$LANE" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid lane name" >&2; exit 2; }
# A typo like --mode advice must not fall through to the write-enabled branch.
[[ "$MODE" == "advise" || "$MODE" == "work" ]] || { echo "omnilane: invalid --mode (advise|work)" >&2; exit 2; }
if [[ -n "$OVERRIDE_VENDOR" ]] &&
   ! [[ "$OVERRIDE_VENDOR" =~ ^(codex|claude|grok|gemini)$ ]]; then
  echo "omnilane: invalid vendor (codex|claude|grok|gemini)" >&2
  exit 2
fi

depth_guard

CHAIN="$(raw_lane_line "$LANE")" || { echo "omnilane: unknown lane '$LANE' (try --list)" >&2; exit 2; }
if [[ -n "$OVERRIDE_VENDOR" ]]; then
  if resolve_chain "$CHAIN" "$OVERRIDE_VENDOR"; then
    :
  else
    resolve_rc=$?
    case "$resolve_rc" in
      2) exit 2 ;;
      4)
        echo "omnilane: requested vendor '$OVERRIDE_VENDOR' is configured for lane '$LANE' but its CLI is unavailable" >&2
        exit 4
        ;;
      5)
        echo "omnilane: requested vendor '$OVERRIDE_VENDOR' is not configured for lane '$LANE'" >&2
        exit 2
        ;;
      *)
        echo "omnilane: could not resolve requested vendor '$OVERRIDE_VENDOR' for lane '$LANE'" >&2
        exit 2
        ;;
    esac
  fi
else
  resolve_chain "$CHAIN" || {
    echo "omnilane: no vendor CLI available for lane '$LANE' (chain:$CHAIN)." >&2
    echo "omnilane: install a vendor CLI or override the lane in ~/.omnilane/routing.local.yaml" >&2
    exit 4
  }
fi

FIELDS=("${RESOLVED_FIELDS[@]}")
VENDOR="${FIELDS[0]}"; MODEL="${FIELDS[1]:-}"; EFFORT="${FIELDS[2]:-}"
[[ -n "$OVERRIDE_MODEL" ]] && MODEL="$OVERRIDE_MODEL"
[[ -n "$OVERRIDE_EFFORT" ]] && EFFORT="$OVERRIDE_EFFORT"

if [[ "$VENDOR" == "off" ]]; then
  echo "omnilane: lane '$LANE' is disabled in routing config" >&2; exit 3
fi
RUNNER="$OMNILANE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omnilane: no runner for vendor '$VENDOR'" >&2; exit 2; }

# Watchdog seconds: --timeout > per-lane OMNILANE_TIMEOUT_<LANE> > OMNILANE_TIMEOUT > 600.
# Resolve here and export so every runner (and vote's sub-runners) inherits the
# same value without a per-runner code change; they already read OMNILANE_TIMEOUT.
# This bounds each runner CLI call, not the whole dispatch (see header note).
TIMEOUT="$OVERRIDE_TIMEOUT"
if [[ -z "$TIMEOUT" ]]; then
  LANE_TIMEOUT_VAR="OMNILANE_TIMEOUT_$(printf '%s' "${LANE//-/_}" | tr '[:lower:]' '[:upper:]')"
  TIMEOUT="${!LANE_TIMEOUT_VAR:-}"
fi
[[ -n "$TIMEOUT" ]] || TIMEOUT="${OMNILANE_TIMEOUT:-600}"
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "omnilane: invalid timeout (want a positive integer of seconds)" >&2; exit 2
}
export OMNILANE_TIMEOUT="$TIMEOUT"

JOB_SUPERVISOR="$OMNILANE_REPO/scripts/lib/job-timeout.pl"
JOB_WORKER="$OMNILANE_REPO/scripts/lib/job-worker.sh"

# Optional whole-job seconds: flag > per-lane env > global env > automatic
# non-Git Codex work guard > disabled.
# Validate lexically only. Bash arithmetic recursively evaluates variable text,
# so untrusted timeout text must never enter [[ -gt ]] or $((...)).
JOB_TIMEOUT="$OVERRIDE_JOB_TIMEOUT"
if [[ -z "$JOB_TIMEOUT" ]]; then
  LANE_JOB_TIMEOUT_VAR="OMNILANE_JOB_TIMEOUT_$(printf '%s' "${LANE//-/_}" | tr '[:lower:]' '[:upper:]')"
  JOB_TIMEOUT="${!LANE_JOB_TIMEOUT_VAR:-}"
fi
[[ -n "$JOB_TIMEOUT" ]] || JOB_TIMEOUT="${OMNILANE_JOB_TIMEOUT:-}"
CODEX_NONGIT_WORK=0
CODEX_NONGIT_AUTO_JOB_TIMEOUT=0
if [[ "$VENDOR" == "codex" && "$MODE" == "work" ]]; then
  # The target directory is authoritative. Caller-supplied GIT_* state must not
  # redirect or corrupt discovery, so probe with a minimal clean environment.
  GIT_WORKTREE_STATE="$(env -i PATH="$PATH" HOME="${HOME:-}" \
    git -C "$WORKDIR" rev-parse --is-inside-work-tree 2>/dev/null || true)"
  if [[ "$GIT_WORKTREE_STATE" != "true" ]]; then
    CODEX_NONGIT_WORK=1
    if [[ -z "$JOB_TIMEOUT" ]]; then
      if [[ -f "$JOB_SUPERVISOR" ]] && command -v perl &>/dev/null &&
         perl -MPOSIX=setsid -MTime::HiRes=clock_gettime,CLOCK_MONOTONIC -e 'exit 0' \
           >/dev/null 2>&1; then
        CODEX_NONGIT_AUTO_JOB_TIMEOUT=1
        # Preserve the per-call budget while adding process-group cleanup. The
        # supervisor accepts at most nine digits, so larger values receive its
        # effective ceiling rather than making non-Git work unavailable.
        if [[ "$TIMEOUT" =~ ^[1-9][0-9]{0,8}$ ]]; then
          JOB_TIMEOUT="$TIMEOUT"
        else
          JOB_TIMEOUT=999999999
        fi
      else
        echo "omnilane: automatic non-Git Codex job guard is unavailable; continuing without a whole-job fuse (the per-call watchdog path remains)" >&2
      fi
    fi
  fi
fi
if [[ -n "$JOB_TIMEOUT" && ! "$JOB_TIMEOUT" =~ ^[1-9][0-9]{0,8}$ ]]; then
  echo "omnilane: invalid job timeout (want 1..999999999 seconds)" >&2
  exit 2
fi
JOB_TIMEOUT_JSON="${JOB_TIMEOUT:-null}"
unset OMNILANE_JOB_SUPERVISED
[[ -x "$JOB_WORKER" ]] || { echo "omnilane: internal job worker is unavailable" >&2; exit 2; }
if [[ -n "$JOB_TIMEOUT" ]]; then
  [[ -f "$JOB_SUPERVISOR" ]] || { echo "omnilane: whole-job timeout supervisor is unavailable" >&2; exit 2; }
  command -v perl &>/dev/null || { echo "omnilane: --job-timeout requires perl" >&2; exit 2; }
  perl -MPOSIX=setsid -MTime::HiRes=clock_gettime,CLOCK_MONOTONIC -e 'exit 0' \
    >/dev/null 2>&1 || { echo "omnilane: perl lacks whole-job timeout support" >&2; exit 2; }
fi

JOBS_ROOT="$OMNILANE_HOME/jobs"
if [[ -L "$JOBS_ROOT" || ( -e "$JOBS_ROOT" && ! -d "$JOBS_ROOT" ) ]]; then
  echo "omnilane: unsafe jobs store path (want a real directory): $JOBS_ROOT" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  print_dry_run_plan
  exit 0
fi

mkdir -p "$OMNILANE_HOME"
if [[ ! -d "$JOBS_ROOT" ]]; then
  mkdir -m 700 "$JOBS_ROOT"
fi
[[ -d "$JOBS_ROOT" && ! -L "$JOBS_ROOT" ]] || {
  echo "omnilane: jobs store changed while preparing it" >&2
  exit 1
}
# Prompts and model answers may contain private source code or credentials.
# Keep the privacy boundary on Omnilane's own job store instead of changing the
# runner umask, which would also affect files a model creates in --mode work.
chmod 700 "$JOBS_ROOT"
JOB_ID="$(date +%Y%m%d-%H%M%S)-$$-$RANDOM"
JOB_DIR="$JOBS_ROOT/$JOB_ID"
mkdir -m 700 "$JOB_DIR"

if [[ "$TASK" == "-" ]]; then
  (umask 077; cat > "$JOB_DIR/task.txt")
else
  (umask 077; printf '%s\n' "$TASK" > "$JOB_DIR/task.txt")
fi

# meta "timeout" is the resolved per-CLI-call watchdog cap, not a whole-job total.
(umask 077; printf '{"lane":"%s","vendor":"%s","model":"%s","effort":"%s","timeout":%s,"job_timeout":%s,"mode":"%s","workdir":"%s","candidate":"%s/%s","started":"%s"}\n' \
  "$(json_escape "$LANE")" "$(json_escape "$VENDOR")" "$(json_escape "$MODEL")" \
  "$(json_escape "$EFFORT")" "$TIMEOUT" "$JOB_TIMEOUT_JSON" "$(json_escape "$MODE")" \
  "$(json_escape "$WORKDIR")" "$RESOLVED_IDX" "$RESOLVED_TOTAL" \
  "$(date -u +%FT%TZ)" > "$JOB_DIR/meta.json")

secure_job_files() {
  find "$JOB_DIR" -type f -exec chmod 600 {} +
}

finish_job() {
  local rc="$1"
  secure_job_files
  (umask 077; printf '%s\n' "$rc" > "$JOB_DIR/exit")
}

run_job() {
  local rc=0
  write_current_pid_file "$JOB_DIR/pid"
  set +e
  if [[ -n "$JOB_TIMEOUT" ]]; then
    OMNILANE_JOB_SUPERVISED=1 perl "$JOB_SUPERVISOR" "$JOB_TIMEOUT" \
      "$JOB_WORKER" "$VENDOR" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" \
      "$JOB_DIR/task.txt" "$JOB_DIR/out.txt"
  else
    "$JOB_WORKER" "$VENDOR" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" \
      "$JOB_DIR/task.txt" "$JOB_DIR/out.txt"
  fi
  rc=$?
  set -e
  if [[ "$CODEX_NONGIT_WORK" -eq 1 && "$rc" -eq 124 ]]; then
    echo "omnilane: Codex work in a non-Git directory timed out after ${JOB_TIMEOUT}s; the supervised process group was terminated" >&2
  elif [[ "$CODEX_NONGIT_AUTO_JOB_TIMEOUT" -eq 1 && "$rc" -eq 142 ]]; then
    # Equal implicit inner/outer deadlines can race. Only the automatic case is
    # normalized; an explicitly longer whole-job fuse must preserve status 142.
    echo "omnilane: Codex work in a non-Git directory timed out after ${TIMEOUT}s; the supervised process group was terminated" >&2
    rc=124
  fi
  finish_job "$rc"
  return "$rc"
}

if [[ "$BACKGROUND" == "1" ]]; then
  # set -m gives the worker its own process group so it survives the caller's
  # exit and group-wide signals; traps persist a best-effort exit code so
  # jobs.sh never reports a killed worker as still running.
  set -m
  (umask 077; : > "$JOB_DIR/worker.log")
  (
    trap 'finish_job 129; exit 129' HUP
    trap 'finish_job 143; exit 143' TERM
    run_job
  ) < /dev/null > "$JOB_DIR/worker.log" 2>&1 &
  disown
  set +m
  echo "$JOB_ID"
  exit 0
fi

set +e; run_job; RC=$?; set -e
[[ -f "$JOB_DIR/out.txt" ]] && cat "$JOB_DIR/out.txt"
exit "$RC"
