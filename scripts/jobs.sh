#!/usr/bin/env bash
set -euo pipefail
# omniroute background-job helper.
# Usage: jobs.sh list | status JOB_ID | result JOB_ID

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
JOBS="$OMNIROUTE_HOME/jobs"

case "${1:-}" in
  list)
    ls -1t "$JOBS" 2>/dev/null | head -20 | while read -r id; do
      state="running"; [[ -f "$JOBS/$id/exit" ]] && state="done(exit $(cat "$JOBS/$id/exit"))"
      printf '%s  %s  %s\n' "$id" "$state" "$(cat "$JOBS/$id/meta.json" 2>/dev/null)"
    done ;;
  status)
    id="${2:?job id}"
    [[ -d "$JOBS/$id" ]] || { echo "no such job" >&2; exit 1; }
    if [[ -f "$JOBS/$id/exit" ]]; then echo "done exit=$(cat "$JOBS/$id/exit")"; else echo "running"; fi ;;
  result)
    id="${2:?job id}"
    [[ -f "$JOBS/$id/exit" ]] || { echo "still running" >&2; exit 1; }
    cat "$JOBS/$id/out.txt" 2>/dev/null
    if [[ -s "$JOBS/$id/out.txt.stderr.log" ]]; then
      echo "--- stderr ---" >&2; cat "$JOBS/$id/out.txt.stderr.log" >&2
    fi
    exit "$(cat "$JOBS/$id/exit")" ;;
  *) echo "usage: jobs.sh list|status ID|result ID" >&2; exit 2 ;;
esac
