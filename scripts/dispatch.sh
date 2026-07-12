#!/usr/bin/env bash
set -euo pipefail
# omniroute dispatch — one routing table, any harness.
#
# Usage:
#   dispatch.sh [--background] [--mode advise|work] [--workdir DIR]
#               [--model M] [--effort E] LANE "TASK TEXT"
#   dispatch.sh --list            # show effective routing (local overrides applied)
#
# TASK TEXT of "-" reads the task from stdin.

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODE="advise"; WORKDIR="$PWD"; BACKGROUND=0
OVERRIDE_MODEL=""; OVERRIDE_EFFORT=""

print_effective_routing() {
  local seen=" " f lane
  for f in "$OMNIROUTE_HOME/routing.local.yaml" "$OMNIROUTE_REPO/routing.yaml"; do
    [[ -f "$f" ]] || continue
    while IFS= read -r line; do
      [[ "$line" =~ ^([a-z-]+): ]] || continue
      lane="${BASH_REMATCH[1]}"
      [[ "$seen" == *" $lane "* ]] && continue
      seen="$seen$lane "
      printf '%s\n' "${line%%#*}"
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

depth_guard

SPEC=""
for f in "$OMNIROUTE_HOME/routing.local.yaml" "$OMNIROUTE_REPO/routing.yaml"; do
  [[ -f "$f" ]] || continue
  SPEC="$(grep -E "^${LANE}:" "$f" | head -1 | sed 's/#.*$//' | cut -d: -f2-)" || true
  [[ -n "${SPEC// /}" ]] && break
done
[[ -n "${SPEC// /}" ]] || { echo "omniroute: unknown lane '$LANE' (try --list)" >&2; exit 2; }

# Routing files are operator-trusted config; eval supports quoted model strings.
eval "FIELDS=( $SPEC )"
VENDOR="${FIELDS[0]}"; MODEL="${FIELDS[1]:-}"; EFFORT="${FIELDS[2]:-}"
[[ -n "$OVERRIDE_MODEL" ]] && MODEL="$OVERRIDE_MODEL"
[[ -n "$OVERRIDE_EFFORT" ]] && EFFORT="$OVERRIDE_EFFORT"

if [[ "$VENDOR" == "off" ]]; then
  echo "omniroute: lane '$LANE' is disabled in routing config" >&2; exit 3
fi
RUNNER="$OMNIROUTE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omniroute: no runner for vendor '$VENDOR'" >&2; exit 2; }

mkdir -p "$OMNIROUTE_HOME/jobs"
JOB_ID="$(date +%Y%m%d-%H%M%S)-$$-$RANDOM"
JOB_DIR="$OMNIROUTE_HOME/jobs/$JOB_ID"
mkdir -p "$JOB_DIR"

if [[ "$TASK" == "-" ]]; then cat > "$JOB_DIR/task.txt"; else printf '%s\n' "$TASK" > "$JOB_DIR/task.txt"; fi
printf '{"lane":"%s","vendor":"%s","model":"%s","effort":"%s","mode":"%s","workdir":"%s","started":"%s"}\n' \
  "$LANE" "$VENDOR" "$MODEL" "$EFFORT" "$MODE" "$WORKDIR" "$(date -u +%FT%TZ)" > "$JOB_DIR/meta.json"

run_job() {
  local rc=0
  # Two concurrent codex exec in one cwd corrupt its job index — serialize.
  [[ "$VENDOR" == "codex" ]] && acquire_cwd_lock codex
  set +e
  "$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$JOB_DIR/task.txt" "$JOB_DIR/out.txt"
  rc=$?
  set -e
  echo "$rc" > "$JOB_DIR/exit"
  return "$rc"
}

if [[ "$BACKGROUND" == "1" ]]; then
  ( run_job ) >/dev/null 2>&1 &
  echo "$JOB_ID"
  exit 0
fi

set +e; run_job; RC=$?; set -e
[[ -f "$JOB_DIR/out.txt" ]] && cat "$JOB_DIR/out.txt"
exit "$RC"
