#!/usr/bin/env bash
set -euo pipefail
# omnilane background-job helper.
# Usage: jobs.sh list | status JOB_ID | result JOB_ID | stats [--last N]
#        jobs.sh audit [--last N] [--json] | prune [--keep N] [--apply]

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
JOBS="$OMNILANE_HOME/jobs"
JOB_ID_PATTERN='^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$'

usage() {
  echo "usage: jobs.sh list|status ID|result ID|stats [--last N]|audit [--last N] [--json]|prune [--keep N] [--apply]" >&2
  exit 2
}

validate_job_store() {
  if [[ -L "$JOBS" || ( -e "$JOBS" && ! -d "$JOBS" ) ]]; then
    echo "unsafe jobs store path (want a real directory)" >&2
    exit 1
  fi
}

select_job() {
  local id="${1:-}"
  [[ -n "$id" ]] || usage
  [[ "$id" =~ $JOB_ID_PATTERN ]] || {
    echo "invalid job id" >&2
    exit 2
  }
  JOB_DIR="$JOBS/$id"
  # A job must be a real child directory, never a symlink outside the store.
  [[ -d "$JOB_DIR" && ! -L "$JOB_DIR" ]] || {
    echo "no such job" >&2
    exit 1
  }
}

read_exit_code() {
  local path="$1" size value length
  RECORDED_EXIT=""
  [[ -f "$path" && ! -L "$path" ]] || return 1
  size="$(LC_ALL=C wc -c < "$path" | tr -d '[:space:]')" || return 2
  [[ "$size" =~ ^[0-9]+$ && "$size" -le 4 ]] || return 2
  value="$(cat "$path")" || return 2
  length="${#value}"
  # Accept digits with either no terminator or exactly one trailing newline.
  [[ "$size" -eq "$length" || "$size" -eq $((length + 1)) ]] || return 2
  [[ "$value" =~ ^(0|[1-9][0-9]{0,2})$ && "$value" -le 255 ]] || return 2
  RECORDED_EXIT="$value"
}

read_public_metadata() {
  local path="$1" size value length
  local LC_ALL=C
  PUBLIC_METADATA=""
  [[ -f "$path" && ! -L "$path" ]] || return 1
  size="$(wc -c < "$path" | tr -d '[:space:]')" || return 2
  [[ "$size" =~ ^[0-9]+$ && "$size" -le 4096 ]] || return 2
  value="$(cat "$path")" || return 2
  length="${#value}"
  [[ "$length" -gt 0 ]] || return 2
  # Metadata is generated as one JSON line. Reject extra lines and terminal
  # controls instead of echoing corrupted local state to the user's terminal.
  [[ "$size" -eq "$length" || "$size" -eq $((length + 1)) ]] || return 2
  [[ "$value" != *$'\n'* ]] || return 2
  if printf '%s' "$value" | grep -q '[[:cntrl:]]'; then
    return 2
  fi
  PUBLIC_METADATA="$value"
}

read_job_pid() {
  local path="$1" size value length
  local LC_ALL=C
  RECORDED_PID=""
  [[ -f "$path" && ! -L "$path" ]] || return 1
  size="$(wc -c < "$path" | tr -d '[:space:]')" || return 2
  [[ "$size" =~ ^[0-9]+$ && "$size" -le 11 ]] || return 2
  value="$(cat "$path")" || return 2
  length="${#value}"
  [[ "$size" -eq "$length" || "$size" -eq $((length + 1)) ]] || return 2
  [[ "$value" =~ ^[1-9][0-9]{0,9}$ ]] || return 2
  RECORDED_PID="$value"
}

parse_stats_metadata() {
  local value="$1"
  local metadata_re='^\{"lane":"([a-z][a-z0-9-]*)","vendor":"(codex|claude|grok|gemini|exec)"(,|})'
  STATS_LANE=""
  STATS_VENDOR=""
  [[ "$value" =~ $metadata_re ]] || return 1
  STATS_LANE="${BASH_REMATCH[1]}"
  STATS_VENDOR="${BASH_REMATCH[2]}"
}

parse_audit_metadata() {
  local value="$1" metadata_re
  local json_string='([^"\\]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*'
  metadata_re="^\\{\"lane\":\"[a-z][a-z0-9-]*\",\"vendor\":\"(codex|claude|grok|gemini|exec)\",\"model\":\"${json_string}\",\"effort\":\"${json_string}\",\"timeout\":(0|[1-9][0-9]*),\"job_timeout\":(null|0|[1-9][0-9]*),\"mode\":\"(advise|work)\",\"workdir\":\"${json_string}\",\"candidate\":\"[1-9][0-9]*/[1-9][0-9]*\",\"started\":\"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\"\\}$"
  [[ "$value" =~ $metadata_re ]]
}

print_stat_counts() {
  local label="$1"
  shift
  [[ $# -gt 0 ]] || return 0
  printf '%s\n' "$@" | LC_ALL=C sort | uniq -c | while read -r count value; do
    printf '%s %s %s\n' "$label" "$value" "$count"
  done
}

path_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

validate_job_store

case "${1:-}" in
  audit)
    limit=100
    audit_json=0
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --last)
          [[ $# -ge 2 ]] || usage
          limit="$2"; shift 2 ;;
        --json) audit_json=1; shift ;;
        *) usage ;;
      esac
    done
    [[ "$limit" =~ ^[1-9][0-9]{0,3}$ && "$limit" -le 10000 ]] || {
      echo "invalid --last value (want 1..10000)" >&2
      exit 2
    }

    findings=0
    sampled=0
    passed=0
    failed=0
    ids=()
    audit_scopes=()
    audit_codes=()
    passed_ids=()
    audit_emit() {
      audit_scopes+=("$1")
      audit_codes+=("$2")
      [[ "$audit_json" -eq 1 ]] || printf 'FAIL %s %s\n' "$1" "$2"
    }
    if [[ -d "$JOBS" ]]; then
      mode="$(path_mode "$JOBS" 2>/dev/null || true)"
      if [[ "$mode" != "700" ]]; then
        audit_emit store unsafe-store-mode
        findings=$((findings + 1))
      fi
      for job_dir in "$JOBS"/*; do
        [[ -e "$job_dir" || -L "$job_dir" ]] || continue
        if [[ -L "$job_dir" || ! -d "$job_dir" ]]; then
          audit_emit store unsafe-job-entry
          findings=$((findings + 1))
          continue
        fi
        id="${job_dir##*/}"
        if [[ ! "$id" =~ $JOB_ID_PATTERN ]]; then
          audit_emit store invalid-job-name
          findings=$((findings + 1))
          continue
        fi
        ids+=("$id")
      done
    fi

    if [[ ${#ids[@]} -gt 0 ]]; then
      while IFS= read -r id; do
        sampled=$((sampled + 1))
        job_failed=0
        job_dir="$JOBS/$id"
        if [[ ! -d "$job_dir" || -L "$job_dir" ]]; then
          audit_emit "$id" changed-job-path
          findings=$((findings + 1)); failed=$((failed + 1))
          continue
        fi
        mode="$(path_mode "$job_dir" 2>/dev/null || true)"
        if [[ "$mode" != "700" ]]; then
          audit_emit "$id" unsafe-job-mode
          findings=$((findings + 1)); job_failed=1
        fi
        for artifact in "$job_dir"/*; do
          [[ -e "$artifact" || -L "$artifact" ]] || continue
          if [[ -L "$artifact" ]]; then
            audit_emit "$id" symlink-artifact
            findings=$((findings + 1)); job_failed=1
          elif [[ -d "$artifact" ]]; then
            audit_emit "$id" nested-directory
            findings=$((findings + 1)); job_failed=1
          elif [[ -f "$artifact" ]]; then
            mode="$(path_mode "$artifact" 2>/dev/null || true)"
            if [[ "$mode" != "600" ]]; then
              audit_emit "$id" unsafe-file-mode
              findings=$((findings + 1)); job_failed=1
            fi
          else
            audit_emit "$id" unsafe-artifact-type
            findings=$((findings + 1)); job_failed=1
          fi
        done
        if [[ ! -f "$job_dir/task.txt" || -L "$job_dir/task.txt" ]]; then
          audit_emit "$id" missing-task
          findings=$((findings + 1)); job_failed=1
        fi
        if ! read_public_metadata "$job_dir/meta.json" ||
           ! parse_audit_metadata "$PUBLIC_METADATA"; then
          audit_emit "$id" invalid-metadata
          findings=$((findings + 1)); job_failed=1
        fi
        if [[ -e "$job_dir/pid" || -L "$job_dir/pid" ]]; then
          if ! read_job_pid "$job_dir/pid"; then
            audit_emit "$id" invalid-pid
            findings=$((findings + 1)); job_failed=1
          fi
        else
          audit_emit "$id" missing-pid
          findings=$((findings + 1)); job_failed=1
        fi
        if [[ -e "$job_dir/exit" || -L "$job_dir/exit" ]]; then
          if ! read_exit_code "$job_dir/exit"; then
            audit_emit "$id" invalid-exit
            findings=$((findings + 1)); job_failed=1
          fi
        fi
        if [[ "$job_failed" -eq 0 ]]; then
          passed_ids+=("$id")
          [[ "$audit_json" -eq 1 ]] || printf 'PASS %s\n' "$id"
          passed=$((passed + 1))
        else
          failed=$((failed + 1))
        fi
      done < <(printf '%s\n' "${ids[@]}" | LC_ALL=C sort -r | sed -n "1,${limit}p")
    fi
    if [[ "$audit_json" -eq 1 ]]; then
      printf '{"schema_version":1,"command":"audit","sampled":%s,"passed":%s,"failed":%s,"findings":[' \
        "$sampled" "$passed" "$failed"
      for ((index = 0; index < ${#audit_codes[@]}; index++)); do
        [[ "$index" -eq 0 ]] || printf ','
        printf '{"scope":"%s","code":"%s"}' \
          "${audit_scopes[$index]}" "${audit_codes[$index]}"
      done
      printf '],"passed_ids":['
      for ((index = 0; index < ${#passed_ids[@]}; index++)); do
        [[ "$index" -eq 0 ]] || printf ','
        printf '"%s"' "${passed_ids[$index]}"
      done
      printf ']}\n'
    else
      printf 'audit: sampled=%s passed=%s failed=%s findings=%s\n' \
        "$sampled" "$passed" "$failed" "$findings"
    fi
    [[ "$findings" -eq 0 ]] || exit 1 ;;
  stats)
    limit=100
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --last)
          [[ $# -ge 2 ]] || usage
          limit="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ "$limit" =~ ^[1-9][0-9]{0,3}$ && "$limit" -le 10000 ]] || {
      echo "invalid --last value (want 1..10000)" >&2
      exit 2
    }
    sampled=0
    succeeded=0
    failed=0
    running=0
    invalid_exit=0
    invalid_metadata=0
    ids=()
    lanes=()
    vendors=()
    if [[ -d "$JOBS" ]]; then
      for job_dir in "$JOBS"/*; do
        [[ -d "$job_dir" && ! -L "$job_dir" ]] || continue
        id="${job_dir##*/}"
        [[ "$id" =~ $JOB_ID_PATTERN ]] || continue
        ids+=("$id")
      done
    fi
    if [[ ${#ids[@]} -gt 0 ]]; then
      while IFS= read -r id; do
        sampled=$((sampled + 1))
        job_dir="$JOBS/$id"
        exit_path="$job_dir/exit"
        if [[ -e "$exit_path" || -L "$exit_path" ]]; then
          if read_exit_code "$exit_path"; then
            if [[ "$RECORDED_EXIT" -eq 0 ]]; then
              succeeded=$((succeeded + 1))
            else
              failed=$((failed + 1))
            fi
          else
            invalid_exit=$((invalid_exit + 1))
          fi
        else
          running=$((running + 1))
        fi
        metadata_path="$job_dir/meta.json"
        if read_public_metadata "$metadata_path" &&
           parse_stats_metadata "$PUBLIC_METADATA"; then
          lanes+=("$STATS_LANE")
          vendors+=("$STATS_VENDOR")
        else
          invalid_metadata=$((invalid_metadata + 1))
        fi
      done < <(printf '%s\n' "${ids[@]}" | LC_ALL=C sort -r | sed -n "1,${limit}p")
    fi
    completed=$((succeeded + failed))
    success_rate=0
    [[ "$completed" -eq 0 ]] || success_rate=$((succeeded * 100 / completed))
    printf 'jobs: sampled=%s succeeded=%s failed=%s running=%s invalid_exit=%s success_rate=%s%%\n' \
      "$sampled" "$succeeded" "$failed" "$running" "$invalid_exit" "$success_rate"
    printf 'invalid_metadata=%s\n' "$invalid_metadata"
    valid_metadata=$((sampled - invalid_metadata))
    if [[ "$valid_metadata" -gt 0 ]]; then
      print_stat_counts lane "${lanes[@]}"
      print_stat_counts vendor "${vendors[@]}"
    fi ;;
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
      exit_path="$JOBS/$id/exit"
      if [[ -e "$exit_path" || -L "$exit_path" ]]; then
        if read_exit_code "$exit_path"; then
          state="done(exit $RECORDED_EXIT)"
        else
          state="done(invalid exit metadata)"
        fi
      fi
      metadata=""
      metadata_path="$JOBS/$id/meta.json"
      if [[ -f "$metadata_path" && ! -L "$metadata_path" ]]; then
        if read_public_metadata "$metadata_path"; then
          metadata="$PUBLIC_METADATA"
        else
          metadata="[invalid metadata]"
        fi
      fi
      printf '%s  %s  %s\n' "$id" "$state" "$metadata"
    done < <(printf '%s\n' "${ids[@]}" | sort -r | sed -n '1,20p') ;;
  status)
    [[ $# -eq 2 ]] || usage
    select_job "$2"
    exit_path="$JOB_DIR/exit"
    if [[ -e "$exit_path" || -L "$exit_path" ]]; then
      read_exit_code "$exit_path" || {
        echo "invalid recorded exit status" >&2
        exit 1
      }
      echo "done exit=$RECORDED_EXIT"
    else
      pid=""; pid_state="missing"
      if [[ -e "$JOB_DIR/pid" || -L "$JOB_DIR/pid" ]]; then
        if read_job_pid "$JOB_DIR/pid"; then
          pid="$RECORDED_PID"; pid_state="valid"
        else
          pid_state="invalid"
        fi
      fi
      if [[ "$pid_state" == "invalid" ]]; then
        echo "dead (invalid pid metadata)"
      elif [[ "$pid_state" == "valid" ]] && ! kill -0 "$pid" 2>/dev/null; then
        echo "dead (worker gone, no exit recorded)"
      else
        echo "running"
      fi
    fi ;;
  result)
    [[ $# -eq 2 ]] || usage
    select_job "$2"
    exit_path="$JOB_DIR/exit"
    [[ -e "$exit_path" || -L "$exit_path" ]] || {
      echo "still running" >&2; exit 1
    }
    read_exit_code "$exit_path" || {
      echo "invalid recorded exit status" >&2; exit 1
    }
    rc="$RECORDED_EXIT"
    # Guarded cat: under set -e a missing out.txt must not eat the real exit code.
    [[ -f "$JOB_DIR/out.txt" && ! -L "$JOB_DIR/out.txt" ]] && cat "$JOB_DIR/out.txt"
    if [[ -s "$JOB_DIR/out.txt.stderr.log" && ! -L "$JOB_DIR/out.txt.stderr.log" ]]; then
      echo "--- stderr ---" >&2; cat "$JOB_DIR/out.txt.stderr.log" >&2
    fi
    exit "$rc" ;;
  prune)
    keep=100
    apply=0
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --keep)
          [[ $# -ge 2 ]] || usage
          keep="$2"; shift 2 ;;
        --apply)
          apply=1; shift ;;
        *) usage ;;
      esac
    done
    [[ "$keep" =~ ^(0|[1-9][0-9]{0,8})$ ]] || {
      echo "invalid --keep value (want 0..999999999)" >&2
      exit 2
    }
    completed=()
    if [[ -d "$JOBS" ]]; then
      for job_dir in "$JOBS"/*; do
        [[ -d "$job_dir" && ! -L "$job_dir" ]] || continue
        id="${job_dir##*/}"
        [[ "$id" =~ $JOB_ID_PATTERN ]] || continue
        read_exit_code "$job_dir/exit" || continue
        completed+=("$id")
      done
    fi
    candidates=()
    index=0
    if [[ ${#completed[@]} -gt 0 ]]; then
      while IFS= read -r id; do
        index=$((index + 1))
        [[ "$index" -le "$keep" ]] || candidates+=("$id")
      done < <(printf '%s\n' "${completed[@]}" | sort -r)
    fi

    deleted=0
    for id in "${candidates[@]}"; do
      job_dir="$JOBS/$id"
      if [[ "$apply" -eq 0 ]]; then
        printf 'would delete %s\n' "$id"
        continue
      fi
      # Re-check immediately before deletion so a replaced path or a job whose
      # completion marker disappeared is never removed.
      if [[ -d "$job_dir" && ! -L "$job_dir" ]] &&
         read_exit_code "$job_dir/exit"; then
        /bin/rm -rf "$job_dir"
        printf 'deleted %s\n' "$id"
        deleted=$((deleted + 1))
      else
        printf 'skipped changed job %s\n' "$id" >&2
      fi
    done
    if [[ "$apply" -eq 1 ]]; then
      printf '%s jobs deleted; newest %s completed jobs retained\n' "$deleted" "$keep"
    else
      printf '%s jobs eligible; rerun with --apply to delete\n' "${#candidates[@]}"
    fi ;;
  *) usage ;;
esac
