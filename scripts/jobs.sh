#!/usr/bin/env bash
set -euo pipefail
# omnilane background-job helper.
# Usage: jobs.sh list | status JOB_ID | result JOB_ID

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
JOBS="$OMNILANE_HOME/jobs"

case "${1:-}" in
  list)
    ls -1t "$JOBS" 2>/dev/null | head -20 | while read -r id; do
      state="running"; [[ -f "$JOBS/$id/exit" ]] && state="done(exit $(cat "$JOBS/$id/exit"))"
      printf '%s  %s  %s\n' "$id" "$state" "$(cat "$JOBS/$id/meta.json" 2>/dev/null)"
    done ;;
  status)
    id="${2:?job id}"
    [[ -d "$JOBS/$id" ]] || { echo "no such job" >&2; exit 1; }
    if [[ -f "$JOBS/$id/exit" ]]; then
      echo "done exit=$(cat "$JOBS/$id/exit")"
    else
      pid="$(cat "$JOBS/$id/pid" 2>/dev/null || true)"
      if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
        echo "dead (worker gone, no exit recorded)"
      else
        echo "running"
      fi
    fi ;;
  result)
    id="${2:?job id}"
    [[ -f "$JOBS/$id/exit" ]] || { echo "still running" >&2; exit 1; }
    rc="$(cat "$JOBS/$id/exit")"
    # Guarded cat: under set -e a missing out.txt must not eat the real exit code.
    [[ -f "$JOBS/$id/out.txt" ]] && cat "$JOBS/$id/out.txt"
    if [[ -s "$JOBS/$id/out.txt.stderr.log" ]]; then
      echo "--- stderr ---" >&2; cat "$JOBS/$id/out.txt.stderr.log" >&2
    fi
    exit "$rc" ;;
  *) echo "usage: jobs.sh list|status ID|result ID" >&2; exit 2 ;;
esac
