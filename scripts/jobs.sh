#!/usr/bin/env bash
set -euo pipefail
# omnilane background-job helper.
# Usage: jobs.sh [--json] list | status JOB_ID | result JOB_ID | stats [--last N]
#        jobs.sh [--json] recommend [--last N] [--lane L] [--min-samples N]
#        jobs.sh wait JOB_ID [--timeout N]
#        jobs.sh send JOB_ID TEXT | watch JOB_ID | close JOB_ID
#        jobs.sh audit [--last N] [--json] | prune [--keep N] [--apply]

# Runtime-relative shared library.
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
JOBS="$OMNILANE_HOME/jobs"
JOB_ID_PATTERN='^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$'
JSON_MODE=0
COMMAND="unknown"

# The backslash case pattern is intentional.
# shellcheck disable=SC1003
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

USAGE_TEXT="usage: jobs.sh [--json] list [--lane L] [--vendor V] [--status running|done]|status ID|result ID|tail ID [--lines N]|send ID TEXT|watch ID|close ID|retry ID [--background]|stats [--last N] [--lane L] [--vendor V]|recommend [--last N] [--lane L] [--min-samples N]|wait ID [--timeout N]|cancel ID|rm ID|audit [--last N]|prune [--keep N] [--older-than DAYS] [--apply]|help"

usage() {
  die 2 "$USAGE_TEXT"
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

print_mode_notice() {
  local path="$JOB_DIR/mode-notice.txt"
  [[ -e "$path" || -L "$path" ]] || return 0
  if read_public_metadata "$path"; then
    printf '%s\n' "$PUBLIC_METADATA"
  fi
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
  # 2>/dev/null before < : the file can vanish between the check above and
  # these reads (a job being removed mid-wait). Redirections apply left to
  # right, so stderr is already silenced when the open fails; the failure is
  # classified by the caller and the noise must not leak into captured output.
  size="$(wc -c 2>/dev/null < "$path" | tr -d '[:space:]')" || return 2
  [[ "$size" =~ ^[0-9]+$ && "$size" -le 11 ]] || return 2
  value="$(cat "$path" 2>/dev/null)" || return 2
  length="${#value}"
  [[ "$size" -eq "$length" || "$size" -eq $((length + 1)) ]] || return 2
  [[ "$value" =~ ^[1-9][0-9]{0,9}$ ]] || return 2
  RECORDED_PID="$value"
}

load_job_vendor() {
  if ! read_public_metadata "$JOB_DIR/meta.json" || ! parse_stats_metadata "$PUBLIC_METADATA"; then
    die 1 "job metadata is not safely readable"
  fi
  JOB_VENDOR="$STATS_VENDOR"
}

require_live_job() {
  load_job_vendor
  [[ "$JOB_VENDOR" == "claude" ]] || die 1 "job vendor '$JOB_VENDOR' is not live-capable; it runs in single-shot mode"
}

wait_for_live_ready() {
  local deadline=$((SECONDS + 5))
  while [[ ! -f "$JOB_DIR/inbox.ready" ]]; do
    if [[ -e "$JOB_DIR/exit" || -L "$JOB_DIR/exit" ]]; then
      die 1 "job is no longer accepting live messages"
    fi
    if [[ "$SECONDS" -ge "$deadline" ]]; then
      die 124 "live inbox did not become ready within 5s"
    fi
    sleep 0.1
  done
  [[ ! -L "$JOB_DIR/inbox.ready" ]] || die 1 "unsafe live inbox readiness path"
  [[ -p "$JOB_DIR/inbox.fifo" && ! -L "$JOB_DIR/inbox.fifo" ]] || die 1 "live inbox FIFO is unavailable"
}

write_fifo_with_timeout() {
  local fifo="$1" payload="$2" writer_pid deadline rc
  (
    printf '%s\n' "$payload" > "$fifo"
  ) &
  writer_pid=$!
  deadline=$((SECONDS + 5))
  while kill -0 "$writer_pid" 2>/dev/null; do
    if [[ "$SECONDS" -ge "$deadline" ]]; then
      kill -TERM "$writer_pid" 2>/dev/null || true
      wait "$writer_pid" 2>/dev/null || true
      return 124
    fi
    sleep 0.1
  done
  set +e
  wait "$writer_pid"
  rc=$?
  set -e
  return "$rc"
}

parse_stats_metadata() {
  local value="$1"
  local metadata_re='^\{"lane":"([a-z][a-z0-9-]*)","vendor":"('"$OMNILANE_VENDOR_ALT"')"(,|})'
  STATS_LANE=""
  STATS_VENDOR=""
  [[ "$value" =~ $metadata_re ]] || return 1
  STATS_LANE="${BASH_REMATCH[1]}"
  STATS_VENDOR="${BASH_REMATCH[2]}"
}

parse_audit_metadata() {
  local value="$1" metadata_re
  local json_string='([^"\\]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*'
  metadata_re="^\\{\"lane\":\"[a-z][a-z0-9-]*\",\"vendor\":\"(${OMNILANE_VENDOR_ALT})\",\"model\":\"${json_string}\",\"effort\":\"${json_string}\",\"timeout\":(0|[1-9][0-9]*),\"job_timeout\":(null|0|[1-9][0-9]*),\"mode\":\"(advise|work)\",\"workdir\":\"${json_string}\",\"candidate\":\"[1-9][0-9]*/[1-9][0-9]*\",\"started\":\"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\"\\}$"
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

build_recommendation_rows() {
  local minimum="$1"
  shift
  [[ $# -gt 0 ]] || return 0
  printf '%s\n' "$@" | awk -F '\t' -v minimum="$minimum" '
    {
      key = $1 SUBSEP $2
      samples[key]++
      if ($3 == 0) succeeded[key]++
      else failed[key]++
    }
    END {
      for (key in samples) {
        split(key, parts, SUBSEP)
        rate = int((succeeded[key] * 100) / samples[key])
        eligible = samples[key] >= minimum ? 1 : 0
        printf "%s\t%d\t%d\t%d\t%s\t%d\t%d\n", \
          parts[1], eligible, rate, samples[key], parts[2], \
          succeeded[key] + 0, failed[key] + 0
      }
    }
  ' | LC_ALL=C sort -t $'\t' -k1,1 -k2,2nr -k3,3nr -k4,4nr -k5,5
}

recommendations_json() {
  local rows="$1"
  [[ -n "$rows" ]] || { printf '[]'; return 0; }
  printf '%s\n' "$rows" | awk -F '\t' '
    function close_lane( status, recommendation) {
      if (current == "") return
      status = recommended == "" ? "insufficient_data" : "ready"
      recommendation = recommended == "" ? "null" : "\"" recommended "\""
      printf "],\"status\":\"%s\",\"recommended_vendor\":%s}", status, recommendation
    }
    BEGIN { printf "["; current = ""; first_lane = 1 }
    {
      if ($1 != current) {
        close_lane()
        if (!first_lane) printf ","
        first_lane = 0
        current = $1
        first_candidate = 1
        recommended = ""
        printf "{\"lane\":\"%s\",\"candidates\":[", current
      }
      if (!first_candidate) printf ","
      first_candidate = 0
      eligible = $2 == 1 ? "true" : "false"
      printf "{\"vendor\":\"%s\",\"samples\":%d,\"succeeded\":%d,\"failed\":%d,\"success_rate\":%d,\"eligible\":%s}", \
        $5, $4, $6, $7, $3, eligible
      if (recommended == "" && $2 == 1) recommended = $5
    }
    END { close_lane(); printf "]" }
  '
}

print_recommendations() {
  local rows="$1" minimum="$2"
  [[ -n "$rows" ]] || return 0
  printf '%s\n' "$rows" | awk -F '\t' -v minimum="$minimum" '
    $1 != current {
      current = $1
      if ($2 == 1) {
        printf "recommend lane=%s vendor=%s success_rate=%d%% samples=%d succeeded=%d failed=%d\n", \
          $1, $5, $3, $4, $6, $7
      } else {
        printf "recommend lane=%s status=insufficient_data min_samples=%d\n", $1, minimum
      }
    }
    {
      eligible = $2 == 1 ? "yes" : "no"
      printf "candidate lane=%s vendor=%s success_rate=%d%% samples=%d succeeded=%d failed=%d eligible=%s\n", \
        $1, $5, $3, $4, $6, $7, eligible
    }
  '
}

path_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
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
# ${args[@]} with zero remaining arguments is an unbound-variable error under
# set -u on Bash 3.2; a bare `jobs.sh` must reach usage, not crash.
set -- ${args[@]+"${args[@]}"}
COMMAND="${1:-unknown}"
if [[ "$JSON_MODE" -eq 1 ]]; then
  case "$COMMAND" in
    list|status|result|stats|recommend|audit) ;;
    *) usage ;;
  esac
fi

validate_job_store

case "${1:-}" in
  help|--help|-h)
    [[ "$JSON_MODE" -eq 0 && $# -eq 1 ]] || usage
    echo "$USAGE_TEXT" ;;
  send)
    [[ "$JSON_MODE" -eq 0 && $# -eq 3 ]] || usage
    select_job "$2"
    require_live_job
    wait_for_live_ready
    if [[ "${FOREMAN_SESSION+x}" == "x" ]]; then
      foreman_value="$FOREMAN_SESSION"
    else
      foreman_value="${foreman_session-}"
    fi
    payload="$(printf '{"type":"user","omnilane_job_id":"%s","foreman_session":"%s","message":{"role":"user","content":[{"type":"text","text":"%s"}]}}' \
      "$(json_escape "$2")" "$(json_escape "$foreman_value")" "$(json_escape "$3")")"
    set +e
    write_fifo_with_timeout "$JOB_DIR/inbox.fifo" "$payload"
    send_rc=$?
    set -e
    [[ "$send_rc" -eq 0 ]] || die "$send_rc" "send timed out or failed; the live worker may be gone"
    echo "sent $2"
    ;;
  watch)
    [[ "$JSON_MODE" -eq 0 && $# -eq 2 ]] || usage
    select_job "$2"
    require_live_job
    events_path="$JOB_DIR/events.jsonl"
    watch_deadline=$((SECONDS + 5))
    while [[ ! -f "$events_path" ]]; do
      if [[ -e "$JOB_DIR/exit" || -L "$JOB_DIR/exit" ]]; then
        die 1 "job has no live event stream"
      fi
      [[ "$SECONDS" -lt "$watch_deadline" ]] || die 124 "live event stream did not appear within 5s"
      sleep 0.1
    done
    [[ ! -L "$events_path" ]] || die 1 "unsafe live event path"
    if [[ -e "$JOB_DIR/exit" || -L "$JOB_DIR/exit" ]]; then
      cat "$events_path"
    else
      tail -n +1 -f -- "$events_path"
    fi
    ;;
  close)
    [[ "$JSON_MODE" -eq 0 && $# -eq 2 ]] || usage
    select_job "$2"
    require_live_job
    if [[ -e "$JOB_DIR/exit" || -L "$JOB_DIR/exit" ]]; then
      read_exit_code "$JOB_DIR/exit" || die 1 "invalid recorded exit status"
      echo "already closed (exit $RECORDED_EXIT)"
      exit "$RECORDED_EXIT"
    fi
    holder_path="$JOB_DIR/inbox.holder.pid"
    read_job_pid "$holder_path" || die 1 "live inbox holder is unavailable"
    holder_pid="$RECORDED_PID"
    kill -0 "$holder_pid" 2>/dev/null || die 1 "live inbox holder is gone"
    kill -USR1 "$holder_pid" 2>/dev/null || die 1 "could not signal live inbox holder"
    close_deadline=$((SECONDS + 10))
    while [[ ! -e "$JOB_DIR/exit" && ! -L "$JOB_DIR/exit" ]]; do
      if [[ "$SECONDS" -ge "$close_deadline" ]]; then
        die 124 "close signal sent, but the live worker did not terminate within 10s"
      fi
      sleep 0.1
    done
    read_exit_code "$JOB_DIR/exit" || die 1 "invalid recorded exit status"
    events_path="$JOB_DIR/events.jsonl"
    result_count=0
    if [[ -f "$events_path" && ! -L "$events_path" ]]; then
      result_count="$(grep -Ec '"type"[[:space:]]*:[[:space:]]*"result"' "$events_path" || true)"
      [[ "$result_count" =~ ^[0-9]+$ ]] || result_count=0
    fi
    echo "closed $2 (result_events=$result_count exit=$RECORDED_EXIT)"
    exit "$RECORDED_EXIT"
    ;;
  tail)
    # Peek at the live public output stream of one job (running or done)
    # without touching its exit contract. Only out.txt is shown; stderr and
    # worker logs stay private to `result` and the job directory.
    [[ "$JSON_MODE" -eq 0 && $# -ge 2 ]] || usage
    tail_id="$2"
    lines=20
    shift 2
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --lines)
          [[ $# -ge 2 ]] || usage
          lines="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ "$lines" =~ ^([1-9][0-9]{0,2}|1000)$ ]] || {
      die 2 "invalid --lines value (want 1..1000)"
    }
    select_job "$tail_id"
    out_path="$JOB_DIR/out.txt"
    if [[ -L "$out_path" ]]; then
      die 1 "unsafe output path (symlink)"
    fi
    if [[ ! -f "$out_path" ]]; then
      die 1 "no output yet"
    fi
    tail -n "$lines" -- "$out_path" ;;
  retry)
    # Re-dispatch a COMPLETED job with its recorded route and original task
    # text. Metadata parsing is fail-closed: any value that needed JSON
    # escaping does not match the strict patterns below and refuses to retry,
    # so corrupted or hand-edited metadata can never smuggle flags or paths.
    [[ "$JSON_MODE" -eq 0 && $# -ge 2 ]] || usage
    retry_id="$2"
    retry_background=0
    shift 2
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --background) retry_background=1; shift ;;
        *) usage ;;
      esac
    done
    select_job "$retry_id"
    read_exit_code "$JOB_DIR/exit" || {
      die 1 "retry needs a completed job (still running, or invalid exit metadata)"
    }
    read_public_metadata "$JOB_DIR/meta.json" || {
      die 1 "cannot retry: unreadable job metadata"
    }
    retry_re='^\{"lane":"([a-z][a-z0-9-]*)","vendor":"('"$OMNILANE_VENDOR_ALT"')","model":"([^"\\]*)","effort":"([^"\\]*)","timeout":([1-9][0-9]*),"job_timeout":([1-9][0-9]*|null),"mode":"(advise|work)","workdir":"([^"\\]*)",'
    [[ "$PUBLIC_METADATA" =~ $retry_re ]] || {
      die 1 "cannot retry: job metadata is not safely parseable"
    }
    retry_lane="${BASH_REMATCH[1]}"
    retry_vendor="${BASH_REMATCH[2]}"
    retry_model="${BASH_REMATCH[3]}"
    retry_effort="${BASH_REMATCH[4]}"
    retry_timeout="${BASH_REMATCH[5]}"
    retry_job_timeout="${BASH_REMATCH[6]}"
    retry_mode="${BASH_REMATCH[7]}"
    retry_workdir="${BASH_REMATCH[8]}"
    task_path="$JOB_DIR/task.txt"
    [[ -f "$task_path" && ! -L "$task_path" ]] || {
      die 1 "cannot retry: original task text is missing"
    }
    [[ -d "$retry_workdir" ]] || {
      die 1 "cannot retry: original workdir no longer exists: $retry_workdir"
    }
    retry_args=(--mode "$retry_mode" --workdir "$retry_workdir" --timeout "$retry_timeout")
    [[ "$retry_job_timeout" == "null" ]] || retry_args+=(--job-timeout "$retry_job_timeout")
    # dispatch --vendor only accepts real CLI vendors; an exec gate is re-run
    # through normal lane resolution plus the recorded --model script path.
    [[ "$retry_vendor" == "exec" ]] || retry_args+=(--vendor "$retry_vendor")
    [[ -z "$retry_model" ]] || retry_args+=(--model "$retry_model")
    [[ -z "$retry_effort" ]] || retry_args+=(--effort "$retry_effort")
    [[ "$retry_background" -eq 0 ]] || retry_args+=(--background)
    exec bash "$OMNILANE_REPO/scripts/dispatch.sh" "${retry_args[@]}" \
      "$retry_lane" - < "$task_path" ;;
  audit)
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
      [[ "$JSON_MODE" -eq 1 ]] || printf 'FAIL %s %s\n' "$1" "$2"
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
      elif [[ -p "$artifact" && "${artifact##*/}" == "inbox.fifo" ]]; then
        mode="$(path_mode "$artifact" 2>/dev/null || true)"
        if [[ "$mode" != "600" ]]; then
          audit_emit "$id" unsafe-fifo-mode
          findings=$((findings + 1)); job_failed=1
        fi
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
          [[ "$JSON_MODE" -eq 1 ]] || printf 'PASS %s\n' "$id"
          passed=$((passed + 1))
        else
          failed=$((failed + 1))
        fi
      done < <(printf '%s\n' "${ids[@]}" | LC_ALL=C sort -r | sed -n "1,${limit}p")
    fi
    if [[ "$JSON_MODE" -eq 1 ]]; then
      printf '{"schema_version":1,"command":"audit","sampled":%s,"passed":%s,"failed":%s,"findings":[' \
        "$sampled" "$passed" "$failed"
      for ((index = 0; index < ${#audit_codes[@]}; index++)); do
        [[ "$index" -eq 0 ]] || printf ','
        printf '{"scope":"%s","code":"%s"}' \
          "$(json_escape "${audit_scopes[$index]}")" "$(json_escape "${audit_codes[$index]}")"
      done
      printf '],"passed_ids":['
      for ((index = 0; index < ${#passed_ids[@]}; index++)); do
        [[ "$index" -eq 0 ]] || printf ','
        printf '"%s"' "$(json_escape "${passed_ids[$index]}")"
      done
      printf ']}\n'
    else
      printf 'audit: sampled=%s passed=%s failed=%s findings=%s\n' \
        "$sampled" "$passed" "$failed" "$findings"
    fi
    [[ "$findings" -eq 0 ]] || exit 1 ;;
  recommend)
    limit=100
    minimum_samples=3
    filter_lane=""
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --last)
          [[ $# -ge 2 ]] || usage
          limit="$2"; shift 2 ;;
        --lane)
          [[ $# -ge 2 ]] || usage
          filter_lane="$2"; shift 2 ;;
        --min-samples)
          [[ $# -ge 2 ]] || usage
          minimum_samples="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ "$limit" =~ ^[1-9][0-9]{0,3}$ && "$limit" -le 10000 ]] || {
      die 2 "invalid --last value (want 1..10000)"
    }
    [[ "$minimum_samples" =~ ^[1-9][0-9]{0,2}$ && "$minimum_samples" -le 1000 ]] || {
      die 2 "invalid --min-samples value (want 1..1000)"
    }
    [[ -z "$filter_lane" || "$filter_lane" =~ ^[a-z][a-z0-9-]*$ ]] || die 2 "invalid --lane value"
    sampled=0
    completed=0
    running=0
    invalid_exit=0
    invalid_metadata=0
    ids=()
    outcomes=()
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
        job_dir="$JOBS/$id"
        metadata_path="$job_dir/meta.json"
        meta_ok=0
        if read_public_metadata "$metadata_path" &&
           parse_stats_metadata "$PUBLIC_METADATA"; then
          meta_ok=1
        fi
        if [[ -n "$filter_lane" ]]; then
          [[ "$meta_ok" -eq 1 && "$filter_lane" == "$STATS_LANE" ]] || continue
        fi
        sampled=$((sampled + 1))
        if [[ "$meta_ok" -ne 1 ]]; then
          invalid_metadata=$((invalid_metadata + 1))
          continue
        fi
        exit_path="$job_dir/exit"
        if [[ ! -e "$exit_path" && ! -L "$exit_path" ]]; then
          running=$((running + 1))
          continue
        fi
        if ! read_exit_code "$exit_path"; then
          invalid_exit=$((invalid_exit + 1))
          continue
        fi
        completed=$((completed + 1))
        outcomes+=("$STATS_LANE"$'\t'"$STATS_VENDOR"$'\t'"$RECORDED_EXIT")
      done < <(printf '%s\n' "${ids[@]}" | LC_ALL=C sort -r | sed -n "1,${limit}p")
    fi
    rows="$(build_recommendation_rows "$minimum_samples" ${outcomes[@]+"${outcomes[@]}"})"
    if [[ "$JSON_MODE" -eq 1 ]]; then
      recommendations="$(recommendations_json "$rows")"
      printf '{"schema_version":1,"command":"recommend","ok":true,"sampled":%s,"completed":%s,"excluded":{"running":%s,"invalid_exit":%s,"invalid_metadata":%s},"minimum_samples":%s,"recommendations":%s}\n' \
        "$sampled" "$completed" "$running" "$invalid_exit" "$invalid_metadata" \
        "$minimum_samples" "$recommendations"
    else
      printf 'recommendations: sampled=%s completed=%s running=%s invalid_exit=%s invalid_metadata=%s min_samples=%s\n' \
        "$sampled" "$completed" "$running" "$invalid_exit" "$invalid_metadata" "$minimum_samples"
      print_recommendations "$rows" "$minimum_samples"
    fi ;;
  stats)
    limit=100
    filter_lane=""; filter_vendor=""
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --last)
          [[ $# -ge 2 ]] || usage
          limit="$2"; shift 2 ;;
        --lane)
          [[ $# -ge 2 ]] || usage
          filter_lane="$2"; shift 2 ;;
        --vendor)
          [[ $# -ge 2 ]] || usage
          filter_vendor="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ "$limit" =~ ^[1-9][0-9]{0,3}$ && "$limit" -le 10000 ]] || {
      die 2 "invalid --last value (want 1..10000)"
    }
    [[ -z "$filter_lane" || "$filter_lane" =~ ^[a-z][a-z0-9-]*$ ]] || die 2 "invalid --lane value"
    [[ -z "$filter_vendor" ]] || omnilane_known_vendor "$filter_vendor" || die 2 "invalid --vendor value"
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
        job_dir="$JOBS/$id"
        # Resolve lane/vendor first so a --lane/--vendor filter can exclude a job
        # before it counts toward any aggregate.
        meta_ok=0
        metadata_path="$job_dir/meta.json"
        if read_public_metadata "$metadata_path" &&
           parse_stats_metadata "$PUBLIC_METADATA"; then
          meta_ok=1
        fi
        if [[ -n "$filter_lane" || -n "$filter_vendor" ]]; then
          [[ "$meta_ok" -eq 1 ]] || continue
          [[ -z "$filter_lane" || "$filter_lane" == "$STATS_LANE" ]] || continue
          [[ -z "$filter_vendor" || "$filter_vendor" == "$STATS_VENDOR" ]] || continue
        fi
        sampled=$((sampled + 1))
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
        if [[ "$meta_ok" -eq 1 ]]; then
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
    filter_lane=""; filter_vendor=""; filter_status=""
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --lane)   [[ $# -ge 2 ]] || usage; filter_lane="$2"; shift 2 ;;
        --vendor) [[ $# -ge 2 ]] || usage; filter_vendor="$2"; shift 2 ;;
        --status) [[ $# -ge 2 ]] || usage; filter_status="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ -z "$filter_lane" || "$filter_lane" =~ ^[a-z][a-z0-9-]*$ ]] || die 2 "invalid --lane value"
    [[ -z "$filter_vendor" ]] || omnilane_known_vendor "$filter_vendor" || die 2 "invalid --vendor value"
    case "$filter_status" in
      ""|running|done) ;;
      *) die 2 "invalid --status value (want running or done)" ;;
    esac
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
    shown=0
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
      if [[ -n "$filter_status" ]]; then
        case "$json_state" in
          done) [[ "$filter_status" == "done" ]] || continue ;;
          running) [[ "$filter_status" == "running" ]] || continue ;;
          *) continue ;;
        esac
      fi
      if [[ -n "$filter_lane" || -n "$filter_vendor" ]]; then
        if [[ "$metadata_status" == "valid" ]] && parse_stats_metadata "$metadata"; then
          [[ -z "$filter_lane" || "$filter_lane" == "$STATS_LANE" ]] || continue
          [[ -z "$filter_vendor" || "$filter_vendor" == "$STATS_VENDOR" ]] || continue
        else
          continue
        fi
      fi
      if [[ "$JSON_MODE" -eq 1 ]]; then
        [[ "$json_first" -eq 1 ]] || printf ','
        json_first=0
        printf '{"id":"%s","state":"%s","exit_code":%s,"metadata":%s,"metadata_status":"%s"}' \
          "$(json_escape "$id")" "$json_state" "$exit_json" "$metadata_json" "$metadata_status"
      else
        printf '%s  %s  %s\n' "$id" "$state" "$metadata"
      fi
      shown=$((shown + 1))
      if [[ "$shown" -ge 20 ]]; then break; fi
    done < <(printf '%s\n' "${ids[@]}" | LC_ALL=C sort -r)
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
    fi
    if [[ "$JSON_MODE" -eq 0 ]]; then
      print_mode_notice
    fi ;;
  wait)
    [[ "$JSON_MODE" -eq 0 && $# -ge 2 ]] || usage
    wait_id="$2"
    wait_timeout=600
    shift 2
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --timeout)
          [[ $# -ge 2 ]] || usage
          wait_timeout="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ "$wait_timeout" =~ ^(0|[1-9][0-9]{0,4})$ && "$wait_timeout" -le 86400 ]] || {
      die 2 "invalid --timeout value (want 0..86400)"
    }
    select_job "$wait_id"
    wait_started="$SECONDS"
    while true; do
      [[ -d "$JOB_DIR" && ! -L "$JOB_DIR" ]] || {
        die 1 "job disappeared while waiting"
      }
      exit_path="$JOB_DIR/exit"
      if [[ -e "$exit_path" || -L "$exit_path" ]]; then
        read_exit_code "$exit_path" || {
          die 1 "invalid recorded exit status"
        }
        echo "done exit=$RECORDED_EXIT"
        exit "$RECORDED_EXIT"
      fi

      pid=""; pid_state="missing"
      if [[ -e "$JOB_DIR/pid" || -L "$JOB_DIR/pid" ]]; then
        if read_job_pid "$JOB_DIR/pid"; then
          pid="$RECORDED_PID"; pid_state="valid"
        else
          pid_state="invalid"
        fi
      fi
      if [[ "$pid_state" == "invalid" ]]; then
        # A pid file that vanished between the existence check and the bounded
        # read is a job being removed, not corrupt metadata; loop again so the
        # directory check reports the disappearance.
        if [[ ! -e "$JOB_DIR/pid" && ! -L "$JOB_DIR/pid" ]]; then
          continue
        fi
        echo "dead (invalid pid metadata)"
        exit 125
      elif [[ "$pid_state" == "valid" ]] && ! kill -0 "$pid" 2>/dev/null; then
        if [[ -e "$exit_path" || -L "$exit_path" ]]; then
          continue
        fi
        echo "dead (worker gone, no exit recorded)"
        exit 125
      fi

      wait_elapsed=$((SECONDS - wait_started))
      if [[ "$wait_elapsed" -ge "$wait_timeout" ]]; then
        printf 'wait timeout after %ss\n' "$wait_timeout" >&2
        exit 124
      fi
      sleep 1
    done ;;
  cancel)
    [[ "$JSON_MODE" -eq 0 && $# -eq 2 ]] || usage
    select_job "$2"
    exit_path="$JOB_DIR/exit"
    if [[ -e "$exit_path" || -L "$exit_path" ]]; then
      if read_exit_code "$exit_path"; then
        echo "already finished (exit $RECORDED_EXIT)"
      else
        echo "already finished (invalid exit metadata)"
      fi
      exit 0
    fi
    pid=""; pid_state="missing"
    if [[ -e "$JOB_DIR/pid" || -L "$JOB_DIR/pid" ]]; then
      if read_job_pid "$JOB_DIR/pid"; then pid="$RECORDED_PID"; pid_state="valid"; else pid_state="invalid"; fi
    fi
    if [[ "$pid_state" != "valid" ]] || ! kill -0 "$pid" 2>/dev/null; then
      echo "not running (no live worker to cancel)"
      exit 0
    fi
    # Background workers run as their own process-group leader (dispatch `set -m`),
    # and that leader traps SIGTERM to record a best-effort exit code. Signal the
    # whole group so the vendor CLI child dies too and the worker writes its exit.
    cancel_grp="$pid"
    cancel_pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
    [[ "$cancel_pgid" =~ ^[1-9][0-9]*$ ]] && cancel_grp="$cancel_pgid"
    kill -TERM "-$cancel_grp" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    cancel_waited=0
    while [[ "$cancel_waited" -lt 5 ]]; do
      if [[ -e "$exit_path" || -L "$exit_path" ]]; then break; fi
      if ! kill -0 "$pid" 2>/dev/null; then break; fi
      sleep 1; cancel_waited=$((cancel_waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      # The TERM trap did not fire in time; SIGKILL cannot be trapped, so no exit
      # gets recorded — force the group down and record the terminal state below.
      kill -KILL "-$cancel_grp" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      sleep 1
    fi
    if [[ -e "$exit_path" || -L "$exit_path" ]]; then
      if read_exit_code "$exit_path"; then
        echo "cancelled (exit $RECORDED_EXIT)"
      else
        echo "cancelled (invalid exit metadata)"
      fi
    elif kill -0 "$pid" 2>/dev/null; then
      die 1 "cancel could not terminate worker pid $pid"
    else
      (umask 077; printf '137\n' > "$exit_path")
      echo "cancelled (exit 137)"
    fi
    exit 0 ;;
  rm)
    [[ "$JSON_MODE" -eq 0 && $# -eq 2 ]] || usage
    select_job "$2"
    exit_path="$JOB_DIR/exit"
    # Refuse to delete a job whose worker is still alive: removing the directory
    # out from under a running worker would strand it writing into a deleted path.
    # A recorded exit (finished) or a dead pid (crashed worker) is safe to remove.
    if [[ ! -e "$exit_path" && ! -L "$exit_path" ]]; then
      if [[ -e "$JOB_DIR/pid" || -L "$JOB_DIR/pid" ]] &&
         read_job_pid "$JOB_DIR/pid" && kill -0 "$RECORDED_PID" 2>/dev/null; then
        die 1 "job is running (pid $RECORDED_PID); cancel it first"
      fi
    fi
    # select_job already proved $JOB_DIR is a real child dir, never a symlink.
    /bin/rm -rf "$JOB_DIR"
    echo "removed $2"
    exit 0 ;;
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
    keep_given=0
    older_than=""
    apply=0
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --keep)
          [[ $# -ge 2 ]] || usage
          keep="$2"; keep_given=1; shift 2 ;;
        --older-than)
          [[ $# -ge 2 ]] || usage
          older_than="$2"; shift 2 ;;
        --apply)
          apply=1; shift ;;
        *) usage ;;
      esac
    done
    [[ "$keep" =~ ^(0|[1-9][0-9]{0,8})$ ]] || {
      echo "invalid --keep value (want 0..999999999)" >&2
      exit 2
    }
    cutoff=""
    if [[ -n "$older_than" ]]; then
      [[ "$older_than" =~ ^[1-9][0-9]{0,3}$ ]] || {
        die 2 "invalid --older-than value (want 1..9999 days)"
      }
      # Age comes from the timestamp embedded in the job id (local time, the
      # same clock that created it), not from mtime, which any tool can touch.
      # The fixed-width YYYYMMDD-HHMMSS format makes string comparison exact.
      if ! cutoff="$(date -v "-${older_than}d" +%Y%m%d-%H%M%S 2>/dev/null)" &&
         ! cutoff="$(date -d "${older_than} days ago" +%Y%m%d-%H%M%S 2>/dev/null)"; then
        die 1 "cannot compute --older-than cutoff on this host's date command"
      fi
      [[ "$cutoff" =~ ^[0-9]{8}-[0-9]{6}$ ]] || {
        die 1 "cannot compute --older-than cutoff on this host's date command"
      }
      # --older-than alone prunes purely by age; an explicit --keep composes
      # as AND (a job must be both beyond the keep window and old enough).
      [[ "$keep_given" -eq 1 ]] || keep=0
    fi
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
        [[ "$index" -le "$keep" ]] && continue
        if [[ -n "$cutoff" ]]; then
          [[ "${id:0:15}" < "$cutoff" ]] || continue
        fi
        candidates+=("$id")
      done < <(printf '%s\n' "${completed[@]}" | sort -r)
    fi

    deleted=0
    # ${arr[@]} on an empty array is an unbound-variable error under set -u
    # on Bash 3.2, so an empty candidate list must never reach the loop.
    if [[ ${#candidates[@]} -gt 0 ]]; then
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
    fi
    if [[ "$apply" -eq 1 ]]; then
      if [[ -n "$older_than" && "$keep_given" -eq 1 ]]; then
        printf '%s jobs deleted; kept the newest %s completed jobs and everything newer than %s days\n' \
          "$deleted" "$keep" "$older_than"
      elif [[ -n "$older_than" ]]; then
        printf '%s jobs deleted; completed jobs newer than %s days retained\n' \
          "$deleted" "$older_than"
      else
        printf '%s jobs deleted; newest %s completed jobs retained\n' "$deleted" "$keep"
      fi
    else
      printf '%s jobs eligible; rerun with --apply to delete\n' "${#candidates[@]}"
    fi ;;
  *) usage ;;
esac
