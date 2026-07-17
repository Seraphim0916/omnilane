#!/usr/bin/env bash
set -euo pipefail
# omnilane background-job helper.
# Usage: jobs.sh [--json] list | status JOB_ID | result JOB_ID | stats [--last N]
#        jobs.sh prune [--keep N] [--apply]

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
JOBS="$OMNILANE_HOME/jobs"
JOB_ID_PATTERN='^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$'
JSON_MODE=0
COMMAND="unknown"

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

die() {
  local rc="$1" message="$2"
  if [[ "$JSON_MODE" -eq 1 ]]; then
    printf '{"schema_version":1,"command":"%s","ok":false,"error":"%s"}\n' \
      "$(json_escape "$COMMAND")" "$(json_escape "$message")"
  else
    printf '%s\n' "$message" >&2
  fi
  exit "$rc"
}

usage() {
  die 2 "usage: jobs.sh [--json] list|status ID|result ID|stats [--last N]|prune [--keep N] [--apply]"
}

validate_job_store() {
  if [[ -L "$JOBS" || ( -e "$JOBS" && ! -d "$JOBS" ) ]]; then
    die 1 "unsafe jobs store path (want a real directory)"
  fi
}

select_job() {
  local id="${1:-}"
  [[ -n "$id" ]] || usage
  [[ "$id" =~ $JOB_ID_PATTERN ]] || {
    die 2 "invalid job id"
  }
  JOB_DIR="$JOBS/$id"
  # A job must be a real child directory, never a symlink outside the store.
  [[ -d "$JOB_DIR" && ! -L "$JOB_DIR" ]] || {
    die 1 "no such job"
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

valid_utf8() {
  command -v iconv >/dev/null 2>&1 || return 1
  printf '%s' "$1" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1
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

print_stat_counts() {
  local label="$1"
  shift
  [[ $# -gt 0 ]] || return 0
  printf '%s\n' "$@" | LC_ALL=C sort | uniq -c | while read -r count value; do
    printf '%s %s %s\n' "$label" "$value" "$count"
  done
}

json_stat_counts() {
  local first=1 value count
  [[ $# -gt 0 ]] || { printf '[]'; return 0; }
  printf '['
  while read -r count value; do
    [[ "$first" -eq 1 ]] || printf ','
    first=0
    printf '{"name":"%s","count":%s}' "$(json_escape "$value")" "$count"
  done < <(printf '%s\n' "$@" | LC_ALL=C sort | uniq -c)
  printf ']'
}

args=()
for arg in "$@"; do
  if [[ "$arg" == "--json" ]]; then
    [[ "$JSON_MODE" -eq 0 ]] || usage
    JSON_MODE=1
  else
    args+=("$arg")
  fi
done
set -- "${args[@]}"
COMMAND="${1:-unknown}"
if [[ "$JSON_MODE" -eq 1 ]]; then
  case "$COMMAND" in
    list|status|result|stats) ;;
    *) usage ;;
  esac
fi

validate_job_store

case "${1:-}" in
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
      die 2 "invalid --last value (want 1..10000)"
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
    valid_metadata=$((sampled - invalid_metadata))
    if [[ "$JSON_MODE" -eq 1 ]]; then
      lanes_json='[]'
      vendors_json='[]'
      if [[ "$valid_metadata" -gt 0 ]]; then
        lanes_json="$(json_stat_counts "${lanes[@]}")"
        vendors_json="$(json_stat_counts "${vendors[@]}")"
      fi
      printf '{"schema_version":1,"command":"stats","ok":true,"sampled":%s,"succeeded":%s,"failed":%s,"running":%s,"invalid_exit":%s,"success_rate":%s,"invalid_metadata":%s,"lanes":%s,"vendors":%s}\n' \
        "$sampled" "$succeeded" "$failed" "$running" "$invalid_exit" "$success_rate" \
        "$invalid_metadata" "$lanes_json" "$vendors_json"
    else
      printf 'jobs: sampled=%s succeeded=%s failed=%s running=%s invalid_exit=%s success_rate=%s%%\n' \
        "$sampled" "$succeeded" "$failed" "$running" "$invalid_exit" "$success_rate"
      printf 'invalid_metadata=%s\n' "$invalid_metadata"
      if [[ "$valid_metadata" -gt 0 ]]; then
        print_stat_counts lane "${lanes[@]}"
        print_stat_counts vendor "${vendors[@]}"
      fi
    fi ;;
  list)
    [[ $# -eq 1 ]] || usage
    if [[ ! -d "$JOBS" ]]; then
      [[ "$JSON_MODE" -eq 0 ]] || printf '{"schema_version":1,"command":"list","ok":true,"jobs":[]}\n'
      exit 0
    fi
    ids=()
    for job_dir in "$JOBS"/*; do
      [[ -d "$job_dir" && ! -L "$job_dir" ]] || continue
      id="${job_dir##*/}"
      [[ "$id" =~ $JOB_ID_PATTERN ]] || continue
      ids+=("$id")
    done
    if [[ ${#ids[@]} -eq 0 ]]; then
      [[ "$JSON_MODE" -eq 0 ]] || printf '{"schema_version":1,"command":"list","ok":true,"jobs":[]}\n'
      exit 0
    fi
    [[ "$JSON_MODE" -eq 0 ]] || printf '{"schema_version":1,"command":"list","ok":true,"jobs":['
    json_first=1
    while IFS= read -r id; do
      state="running"
      exit_json="null"
      exit_path="$JOBS/$id/exit"
      if [[ -e "$exit_path" || -L "$exit_path" ]]; then
        if read_exit_code "$exit_path"; then
          state="done(exit $RECORDED_EXIT)"
          json_state="done"
          exit_json="$RECORDED_EXIT"
        else
          state="done(invalid exit metadata)"
          json_state="invalid"
        fi
      else
        json_state="running"
      fi
      metadata=""
      metadata_json="null"
      metadata_status="missing"
      metadata_path="$JOBS/$id/meta.json"
      if [[ -f "$metadata_path" && ! -L "$metadata_path" ]]; then
        if read_public_metadata "$metadata_path"; then
          metadata="$PUBLIC_METADATA"
          if [[ "$JSON_MODE" -eq 0 ]] || valid_utf8 "$PUBLIC_METADATA"; then
            metadata_json="\"$(json_escape "$PUBLIC_METADATA")\""
            metadata_status="valid"
          else
            metadata_status="invalid"
          fi
        else
          metadata="[invalid metadata]"
          metadata_status="invalid"
        fi
      elif [[ -e "$metadata_path" || -L "$metadata_path" ]]; then
        metadata_status="invalid"
      fi
      if [[ "$JSON_MODE" -eq 1 ]]; then
        [[ "$json_first" -eq 1 ]] || printf ','
        json_first=0
        printf '{"id":"%s","state":"%s","exit_code":%s,"metadata":%s,"metadata_status":"%s"}' \
          "$(json_escape "$id")" "$json_state" "$exit_json" "$metadata_json" "$metadata_status"
      else
        printf '%s  %s  %s\n' "$id" "$state" "$metadata"
      fi
    done < <(printf '%s\n' "${ids[@]}" | LC_ALL=C sort -r | sed -n '1,20p')
    [[ "$JSON_MODE" -eq 0 ]] || printf ']}\n' ;;
  status)
    [[ $# -eq 2 ]] || usage
    select_job "$2"
    exit_path="$JOB_DIR/exit"
    if [[ -e "$exit_path" || -L "$exit_path" ]]; then
      read_exit_code "$exit_path" || {
        die 1 "invalid recorded exit status"
      }
      if [[ "$JSON_MODE" -eq 1 ]]; then
        printf '{"schema_version":1,"command":"status","ok":true,"job":{"id":"%s","state":"done","exit_code":%s}}\n' \
          "$(json_escape "$2")" "$RECORDED_EXIT"
      else
        echo "done exit=$RECORDED_EXIT"
      fi
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
        state="dead"; reason="invalid pid metadata"
      elif [[ "$pid_state" == "valid" ]] && ! kill -0 "$pid" 2>/dev/null; then
        state="dead"; reason="worker gone, no exit recorded"
      else
        state="running"; reason=""
      fi
      if [[ "$JSON_MODE" -eq 1 ]]; then
        if [[ -n "$reason" ]]; then
          printf '{"schema_version":1,"command":"status","ok":true,"job":{"id":"%s","state":"%s","exit_code":null,"reason":"%s"}}\n' \
            "$(json_escape "$2")" "$state" "$(json_escape "$reason")"
        else
          printf '{"schema_version":1,"command":"status","ok":true,"job":{"id":"%s","state":"running","exit_code":null}}\n' \
            "$(json_escape "$2")"
        fi
      elif [[ -n "$reason" ]]; then
        printf 'dead (%s)\n' "$reason"
      else
        echo "running"
      fi
    fi ;;
  result)
    [[ $# -eq 2 ]] || usage
    select_job "$2"
    exit_path="$JOB_DIR/exit"
    [[ -e "$exit_path" || -L "$exit_path" ]] || {
      die 1 "still running"
    }
    read_exit_code "$exit_path" || {
      die 1 "invalid recorded exit status"
    }
    rc="$RECORDED_EXIT"
    if [[ "$JSON_MODE" -eq 1 ]]; then
      output_available=false
      stderr_available=false
      [[ -f "$JOB_DIR/out.txt" && ! -L "$JOB_DIR/out.txt" ]] && output_available=true
      [[ -s "$JOB_DIR/out.txt.stderr.log" && ! -L "$JOB_DIR/out.txt.stderr.log" ]] && stderr_available=true
      printf '{"schema_version":1,"command":"result","ok":true,"job":{"id":"%s","state":"done","exit_code":%s,"output_available":%s,"stderr_available":%s}}\n' \
        "$(json_escape "$2")" "$rc" "$output_available" "$stderr_available"
    else
      # Guarded cat: under set -e a missing out.txt must not eat the real exit code.
      [[ -f "$JOB_DIR/out.txt" && ! -L "$JOB_DIR/out.txt" ]] && cat "$JOB_DIR/out.txt"
      if [[ -s "$JOB_DIR/out.txt.stderr.log" && ! -L "$JOB_DIR/out.txt.stderr.log" ]]; then
        echo "--- stderr ---" >&2; cat "$JOB_DIR/out.txt.stderr.log" >&2
      fi
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
