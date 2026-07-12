#!/usr/bin/env bash
set -euo pipefail
# omnilane dispatch — one routing table, any harness.
#
# Usage:
#   dispatch.sh [--background] [--mode advise|work] [--workdir DIR]
#               [--model M] [--effort E] LANE "TASK TEXT"
#   dispatch.sh --list            # effective routing: local overrides + fallback resolution
#
# TASK TEXT of "-" reads the task from stdin.
#
# A lane line may hold a fallback chain:
#   lane: vendor model effort | vendor model effort | off
# The first candidate whose vendor CLI exists on this machine wins, so the same
# table degrades gracefully for people with fewer subscriptions.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODE="advise"; WORKDIR="$PWD"; BACKGROUND=0
OVERRIDE_MODEL=""; OVERRIDE_EFFORT=""

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

# Pick the first candidate whose vendor CLI is present ("off" always matches).
# Sets RESOLVED_SPEC / RESOLVED_FIELDS / RESOLVED_IDX / RESOLVED_TOTAL.
resolve_chain() {
  local chain="$1" seg i=0
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
    if [[ "${F[0]:-}" == "off" ]] || vendor_available "${F[0]:-}"; then
      RESOLVED_SPEC="$seg"; RESOLVED_FIELDS=("${F[@]}")
      RESOLVED_IDX="$i"; return 0
    fi
  done
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
    --mode) MODE="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --model) OVERRIDE_MODEL="$2"; shift 2 ;;
    --effort) OVERRIDE_EFFORT="$2"; shift 2 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

LANE="${1:?usage: dispatch.sh [flags] LANE \"TASK\"}"
TASK="${2:?missing task text (use - for stdin)}"
[[ "$LANE" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid lane name '$LANE'" >&2; exit 2; }
# A typo like --mode advice must not fall through to the write-enabled branch.
[[ "$MODE" == "advise" || "$MODE" == "work" ]] || { echo "omnilane: invalid --mode '$MODE' (advise|work)" >&2; exit 2; }

depth_guard

CHAIN="$(raw_lane_line "$LANE")" || { echo "omnilane: unknown lane '$LANE' (try --list)" >&2; exit 2; }
resolve_chain "$CHAIN" || {
  echo "omnilane: no vendor CLI available for lane '$LANE' (chain:$CHAIN)." >&2
  echo "omnilane: install a vendor CLI or override the lane in ~/.omnilane/routing.local.yaml" >&2
  exit 4
}

FIELDS=("${RESOLVED_FIELDS[@]}")
VENDOR="${FIELDS[0]}"; MODEL="${FIELDS[1]:-}"; EFFORT="${FIELDS[2]:-}"
[[ -n "$OVERRIDE_MODEL" ]] && MODEL="$OVERRIDE_MODEL"
[[ -n "$OVERRIDE_EFFORT" ]] && EFFORT="$OVERRIDE_EFFORT"

if [[ "$VENDOR" == "off" ]]; then
  echo "omnilane: lane '$LANE' is disabled in routing config" >&2; exit 3
fi
RUNNER="$OMNILANE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omnilane: no runner for vendor '$VENDOR'" >&2; exit 2; }

mkdir -p "$OMNILANE_HOME/jobs"
JOB_ID="$(date +%Y%m%d-%H%M%S)-$$-$RANDOM"
JOB_DIR="$OMNILANE_HOME/jobs/$JOB_ID"
mkdir -p "$JOB_DIR"

if [[ "$TASK" == "-" ]]; then cat > "$JOB_DIR/task.txt"; else printf '%s\n' "$TASK" > "$JOB_DIR/task.txt"; fi
jesc() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "$s"; }
printf '{"lane":"%s","vendor":"%s","model":"%s","effort":"%s","mode":"%s","workdir":"%s","candidate":"%s/%s","started":"%s"}\n' \
  "$LANE" "$VENDOR" "$(jesc "$MODEL")" "$EFFORT" "$MODE" "$(jesc "$WORKDIR")" "$RESOLVED_IDX" "$RESOLVED_TOTAL" "$(date -u +%FT%TZ)" > "$JOB_DIR/meta.json"

run_job() {
  local rc=0
  current_pid > "$JOB_DIR/pid"
  # Two concurrent codex exec in one target dir corrupt its job index — serialize.
  [[ "$VENDOR" == "codex" ]] && acquire_cwd_lock codex "$WORKDIR"
  set +e
  "$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$JOB_DIR/task.txt" "$JOB_DIR/out.txt"
  rc=$?
  set -e
  echo "$rc" > "$JOB_DIR/exit"
  return "$rc"
}

if [[ "$BACKGROUND" == "1" ]]; then
  # set -m gives the worker its own process group so it survives the caller's
  # exit and group-wide signals; traps persist a best-effort exit code so
  # jobs.sh never reports a killed worker as still running.
  set -m
  (
    trap 'echo 129 > "$JOB_DIR/exit"; exit 129' HUP
    trap 'echo 143 > "$JOB_DIR/exit"; exit 143' TERM
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
