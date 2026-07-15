#!/usr/bin/env bash
set -euo pipefail
# omnilane dispatch — one routing table, any harness.
#
# Usage:
#   dispatch.sh [--background] [--mode advise|work] [--workdir DIR]
#               [--vendor V] [--model M] [--effort E] [--timeout SECONDS]
#               LANE "TASK TEXT"
#   dispatch.sh --list            # effective routing: local overrides + fallback resolution
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
# For a true end-to-end deadline that is a separate, future control.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODE="advise"; WORKDIR="$PWD"; BACKGROUND=0
OVERRIDE_VENDOR=""; OVERRIDE_MODEL=""; OVERRIDE_EFFORT=""; OVERRIDE_TIMEOUT=""

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
    expand_home_path "$model"
    script="$EXPANDED_PATH"
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
  for seg in "${SEGS[@]}"; do
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) print_effective_routing; exit 0 ;;
    --background) BACKGROUND=1; shift ;;
    --mode|--workdir|--vendor|--model|--effort|--timeout)
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
      esac
      shift 2 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

LANE="${1:?usage: dispatch.sh [flags] LANE \"TASK\"}"
TASK="${2:?missing task text (use - for stdin)}"
[[ "$LANE" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid lane name '$LANE'" >&2; exit 2; }
# A typo like --mode advice must not fall through to the write-enabled branch.
[[ "$MODE" == "advise" || "$MODE" == "work" ]] || { echo "omnilane: invalid --mode '$MODE' (advise|work)" >&2; exit 2; }
if [[ -n "$OVERRIDE_VENDOR" ]] &&
   ! [[ "$OVERRIDE_VENDOR" =~ ^(codex|claude|grok|gemini)$ ]]; then
  echo "omnilane: invalid vendor '$OVERRIDE_VENDOR' (codex|claude|grok|gemini)" >&2
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
  echo "omnilane: invalid timeout '$TIMEOUT' (want a positive integer of seconds)" >&2; exit 2
}
export OMNILANE_TIMEOUT="$TIMEOUT"

JOBS_ROOT="$OMNILANE_HOME/jobs"
mkdir -p "$OMNILANE_HOME"
if [[ -L "$JOBS_ROOT" || ( -e "$JOBS_ROOT" && ! -d "$JOBS_ROOT" ) ]]; then
  echo "omnilane: unsafe jobs store path (want a real directory): $JOBS_ROOT" >&2
  exit 1
fi
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
# meta "timeout" is the resolved per-CLI-call watchdog cap, not a whole-job total.
(umask 077; printf '{"lane":"%s","vendor":"%s","model":"%s","effort":"%s","timeout":%s,"mode":"%s","workdir":"%s","candidate":"%s/%s","started":"%s"}\n' \
  "$(json_escape "$LANE")" "$(json_escape "$VENDOR")" "$(json_escape "$MODEL")" \
  "$(json_escape "$EFFORT")" "$TIMEOUT" "$(json_escape "$MODE")" \
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
  # Two concurrent codex exec in one target dir corrupt its job index — serialize.
  [[ "$VENDOR" == "codex" ]] && acquire_cwd_lock codex "$WORKDIR"
  set +e
  "$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$JOB_DIR/task.txt" "$JOB_DIR/out.txt"
  rc=$?
  set -e
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
