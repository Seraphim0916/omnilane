#!/usr/bin/env bash
set -euo pipefail
# omnilane background-job helper.
# Usage: jobs.sh list | status JOB_ID | result JOB_ID

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
JOBS="$OMNILANE_HOME/jobs"
JOB_ID_PATTERN='^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$'

usage() {
  echo "usage: jobs.sh list|status ID|result ID" >&2
  exit 2
}

select_job() {
  local id="${1:-}"
  [[ -n "$id" ]] || usage
  [[ "$id" =~ $JOB_ID_PATTERN ]] || {
    echo "invalid job id: $id" >&2
    exit 2
  }
  JOB_DIR="$JOBS/$id"
  # A job must be a real child directory, never a symlink outside the store.
  [[ -d "$JOB_DIR" && ! -L "$JOB_DIR" ]] || {
    echo "no such job" >&2
    exit 1
  }
}

case "${1:-}" in
  list)
    [[ $# -eq 1 ]] || usage
    [[ -d "$JOBS" ]] || exit 0
    ids=()
    for job_dir in "$JOBS"/*; do
      [[ -d "$job_dir" && ! -L "$job_dir" ]] || continue
      id="${job_dir##*/}"
      [[ "$id" =~ $JOB_ID_PATTERN ]] || continue
      ids+=("$id")
    done
    [[ ${#ids[@]} -gt 0 ]] || exit 0
    while IFS= read -r id; do
      state="running"
      [[ -f "$JOBS/$id/exit" && ! -L "$JOBS/$id/exit" ]] && \
        state="done(exit $(cat "$JOBS/$id/exit"))"
      metadata=""
      [[ -f "$JOBS/$id/meta.json" && ! -L "$JOBS/$id/meta.json" ]] && \
        metadata="$(cat "$JOBS/$id/meta.json" 2>/dev/null || true)"
      printf '%s  %s  %s\n' "$id" "$state" "$metadata"
    done < <(printf '%s\n' "${ids[@]}" | sort -r | sed -n '1,20p') ;;
  status)
    [[ $# -eq 2 ]] || usage
    select_job "$2"
    if [[ -f "$JOB_DIR/exit" && ! -L "$JOB_DIR/exit" ]]; then
      echo "done exit=$(cat "$JOB_DIR/exit")"
    else
      pid=""
      [[ -f "$JOB_DIR/pid" && ! -L "$JOB_DIR/pid" ]] && \
        pid="$(cat "$JOB_DIR/pid" 2>/dev/null || true)"
      if [[ -n "$pid" && ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
        echo "dead (invalid pid metadata)"
      elif [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
        echo "dead (worker gone, no exit recorded)"
      else
        echo "running"
      fi
    fi ;;
  result)
    [[ $# -eq 2 ]] || usage
    select_job "$2"
    [[ -f "$JOB_DIR/exit" && ! -L "$JOB_DIR/exit" ]] || {
      echo "still running" >&2; exit 1
    }
    rc="$(cat "$JOB_DIR/exit")"
    [[ "$rc" =~ ^[0-9]+$ && "$rc" -le 255 ]] || {
      echo "invalid recorded exit status" >&2; exit 1
    }
    # Guarded cat: under set -e a missing out.txt must not eat the real exit code.
    [[ -f "$JOB_DIR/out.txt" && ! -L "$JOB_DIR/out.txt" ]] && cat "$JOB_DIR/out.txt"
    if [[ -s "$JOB_DIR/out.txt.stderr.log" && ! -L "$JOB_DIR/out.txt.stderr.log" ]]; then
      echo "--- stderr ---" >&2; cat "$JOB_DIR/out.txt.stderr.log" >&2
    fi
    exit "$rc" ;;
  *) usage ;;
esac
