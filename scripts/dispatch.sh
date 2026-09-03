#!/usr/bin/env bash
set -euo pipefail
# omnilane dispatch — one routing table, any harness.
#
# Usage:
#   dispatch.sh [--background] [--live|--single-shot] [--dry-run] [--thread NAME]
#               [--mode advise|work|sysops] [--workdir DIR]
#               [--vendor V] [--model M] [--effort E] [--timeout SECONDS]
#               [--job-timeout SECONDS] [--idle-timeout SECONDS] LANE "TASK TEXT"
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
# shellcheck disable=SC1091
source "$OMNILANE_REPO/scripts/lib/live-protocol.sh"

MODE="advise"; WORKDIR="$PWD"; BACKGROUND=0; DRY_RUN=0
OVERRIDE_VENDOR=""; OVERRIDE_MODEL=""; OVERRIDE_EFFORT=""; OVERRIDE_TIMEOUT=""
OVERRIDE_JOB_TIMEOUT=""; OVERRIDE_IDLE_TIMEOUT=""; SESSION_REQUEST="auto"
THREAD_NAME=""; THREAD_MODE=""; THREAD_ID=""; THREAD_TURN=""; THREAD_CREATED=""

usage_error() {
  echo 'usage: dispatch.sh [--background] [--dry-run] [--thread NAME] [flags] LANE "TASK" | [--json] --list|--validate [--json] | [--json] --explain LANE [--json] | --help' >&2
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
  --thread NAME          continue a named thread (claude/codex/grok/gemini; single-shot)
  --live                    require a resident session (background only)
  --single-shot             force one-shot dispatch (background only)
  --idle-timeout SECONDS    close an idle live mailbox (default 900; 0 disables)
  --background           run in the background and print the JOB_ID
  --dry-run              print the fully resolved dispatch plan and stop
                         before any provider call or job state
  --mode advise|work|sysops
                         advise (read-only, default), work (may edit files),
                         or sysops (work without the vendor sandbox, for
                         service operations like launchctl — codex only;
                         other vendors treat it as work)
  --workdir DIR          working directory handed to the vendor CLI
  --vendor V             pin one configured vendor (codex|claude|grok|gemini|kimi|qwen|opencode|openrouter|deepseek|zai|mistral|groq|cerebras)
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

thread_refuse() {
  local notice="$1"
  printf '%s\n' "$notice"
  printf '%s\n' "$notice" >&2
  exit 2
}

generate_thread_id() {
  local value=""
  if command -v uuidgen >/dev/null 2>&1; then
    value="$(uuidgen)" || return 1
  elif command -v python3 >/dev/null 2>&1; then
    value="$(python3 -c 'import uuid; print(uuid.uuid4())')" || return 1
  else
    return 1
  fi
  [[ "$value" =~ ^[A-Za-z0-9._:-]+$ && "${#value}" -le 256 ]] || return 1
  printf '%s\n' "$value"
}

extract_claude_result_session_id() {
  local events_path="$1"
  [[ -f "$events_path" && ! -L "$events_path" ]] || return 1
  perl -MJSON::PP -e '
    use strict;
    use warnings;
    my ($path) = @ARGV;
    open my $fh, "<", $path or die $!;
    my $last = "";
    while (my $line = <$fh>) {
      my $event = eval { decode_json($line) };
      next unless ref($event) eq "HASH" && ($event->{type} // "") eq "result";
      my $id = $event->{session_id};
      next if !defined($id) || ref($id) || $id !~ /\A[A-Za-z0-9._:-]{1,256}\z/;
      $last = $id;
    }
    exit 1 unless length($last);
    print $last;
  ' "$events_path" 2>/dev/null
}

extract_codex_result_session_id() {
  local events_path="$1"
  [[ -f "$events_path" && ! -L "$events_path" ]] || return 1
  perl -MJSON::PP -e '
    use strict;
    use warnings;
    my ($path) = @ARGV;
    open my $fh, "<", $path or die $!;
    my $last = "";
    while (my $line = <$fh>) {
      my $event = eval { decode_json($line) };
      next unless ref($event) eq "HASH";
      my $id = $event->{thread_id};
      next if !defined($id) || ref($id) || $id !~ /\A[A-Za-z0-9._:-]{1,256}\z/;
      $last = $id;
    }
    exit 1 unless length($last);
    print $last;
  ' "$events_path" 2>/dev/null
}

extract_grok_result_session_id() {
  local stderr_path="$1" fallback="$2" forked_id=""
  if [[ -f "$stderr_path" && ! -L "$stderr_path" ]] && grep -Eiq 'fork' "$stderr_path"; then
    forked_id="$(perl -ne '
      while (/([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})/g) {
        $last = $1;
      }
      END { exit 1 unless defined($last); print $last }
    ' "$stderr_path" 2>/dev/null)" || return 1
    printf '%s\n' "$forked_id"
    return 0
  fi
  [[ "$fallback" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$ ]] || return 1
  printf '%s\n' "$fallback"
}

extract_gemini_result_session_id() {
  local result_path="$1"
  [[ -f "$result_path" && ! -L "$result_path" ]] || return 1
  perl -MJSON::PP -e '
    use strict;
    use warnings;
    my ($path) = @ARGV;
    open my $fh, "<", $path or die $!;
    local $/;
    my $result = decode_json(<$fh>);
    my $id = ref($result) eq "HASH" ? $result->{conversation_id} : undef;
    exit 1 if !defined($id) || ref($id) || $id !~ /\A[A-Za-z0-9._:-]{1,256}\z/;
    print $id;
  ' "$result_path" 2>/dev/null
}

append_thread_notice() {
  local notice="$1"
  printf '%s\n' "$notice" >&2
  printf '\n%s\n' "$notice" >> "$JOB_DIR/out.txt"
}

write_thread_state() {
  local returned_id="$1" final="$OMNILANE_HOME/threads/$THREAD_NAME.json"
  local tmp="$OMNILANE_HOME/threads/.$THREAD_NAME.tmp.$$-$RANDOM"
  local updated old_umask write_rc=0
  prepare_threads_store || return 1
  updated="$(date -u +%FT%TZ)" || return 1
  [[ -n "$THREAD_CREATED" ]] || THREAD_CREATED="$updated"
  old_umask="$(umask)"
  umask 077
  printf '{"name":"%s","vendor":"%s","model":"%s","effort":"%s","workdir":"%s","session_id":"%s","turns":%s,"last_job_id":"%s","created":"%s","updated":"%s"}\n' \
    "$(json_escape "$THREAD_NAME")" "$(json_escape "$VENDOR")" \
    "$(json_escape "$MODEL")" "$(json_escape "$EFFORT")" \
    "$(json_escape "$WORKDIR")" "$(json_escape "$returned_id")" \
    "$THREAD_TURN" "$(json_escape "$JOB_ID")" \
    "$(json_escape "$THREAD_CREATED")" "$(json_escape "$updated")" \
    > "$tmp" || write_rc=$?
  if [[ "$write_rc" -eq 0 ]]; then
    chmod 600 "$tmp" || write_rc=$?
  fi
  if [[ "$write_rc" -eq 0 ]]; then
    mv "$tmp" "$final" || write_rc=$?
  fi
  umask "$old_umask"
  rm "$tmp" 2>/dev/null || true
  return "$write_rc"
}

update_thread_state() {
  local returned_id notice original_id="$THREAD_ID"
  case "$VENDOR" in
    claude)
      returned_id="$(extract_claude_result_session_id "$JOB_DIR/out.txt.events.jsonl")"
      ;;
    codex)
      returned_id="$(extract_codex_result_session_id "$JOB_DIR/out.txt.progress.log")"
      ;;
    grok)
      returned_id="$(extract_grok_result_session_id "$JOB_DIR/out.txt.stderr.log" "$THREAD_ID")"
      ;;
    gemini)
      returned_id="$(extract_gemini_result_session_id "$JOB_DIR/out.txt.result.json")"
      ;;
    *)
      return 1
      ;;
  esac || {
    append_thread_notice "omnilane: thread $THREAD_NAME did not return a resumable $VENDOR session id"
    return 1
  }
  if [[ "$returned_id" != "$THREAD_ID" && "$THREAD_ID" != "pending" ]]; then
    notice="omnilane: thread $THREAD_NAME: $VENDOR returned a different session id ($THREAD_ID -> $returned_id)"
    append_thread_notice "$notice"
  fi
  write_thread_state "$returned_id" || {
    append_thread_notice "omnilane: thread $THREAD_NAME state could not be written"
    return 1
  }
  THREAD_ID="$returned_id"
  # Completes the pre-run "session pending" line on stdout; it is visibility,
  # not a notice, so it stays out of out.txt (which foreground mode cats).
  if [[ "$original_id" == "pending" ]]; then
    printf 'omnilane: thread %s turn %s (%s session %s, %s)\n' \
      "$THREAD_NAME" "$THREAD_TURN" "$VENDOR" "$THREAD_ID" "$THREAD_MODE"
  fi
}

print_dry_run_value() {
  printf '%s=' "$1"
  printf '%q\n' "$2"
}

print_dry_run_plan() {
  local background=no task_source=argument write_worktree=no job_timeout=disabled
  [[ "$BACKGROUND" -eq 1 ]] && background=yes
  [[ "$TASK" == "-" ]] && task_source=stdin
  [[ "$MODE" == "work" || "$MODE" == "sysops" ]] && write_worktree=yes
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
  printf 'idle_timeout=%s\n' "$IDLE_TIMEOUT"
  print_dry_run_value session_mode "$SESSION_MODE"
  printf 'candidate=%s/%s\n' "$RESOLVED_IDX" "$RESOLVED_TOTAL"
  if [[ -n "$THREAD_NAME" ]]; then
    print_dry_run_value thread "$THREAD_NAME"
    print_dry_run_value thread_mode "$THREAD_MODE"
    print_dry_run_value thread_session "$THREAD_ID"
    print_dry_run_value thread_turn "$THREAD_TURN"
  fi
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
        if ! [[ "$vendor" =~ ^(${OMNILANE_VENDOR_ALT}|off)$ ]]; then
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
    --live)
      [[ "$SESSION_REQUEST" != "single-shot" ]] || {
        echo "omnilane: --live and --single-shot are mutually exclusive" >&2; exit 2
      }
      SESSION_REQUEST="live"; shift ;;
    --single-shot)
      [[ "$SESSION_REQUEST" != "live" ]] || {
        echo "omnilane: --live and --single-shot are mutually exclusive" >&2; exit 2
      }
      SESSION_REQUEST="single-shot"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --mode|--workdir|--vendor|--model|--effort|--timeout|--job-timeout|--idle-timeout|--thread)
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
        --idle-timeout) OVERRIDE_IDLE_TIMEOUT="$2" ;;
        --thread) THREAD_NAME="$2" ;;
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
if [[ -n "$THREAD_NAME" ]] && ! omnilane_valid_thread_name "$THREAD_NAME"; then
  thread_refuse "omnilane: invalid --thread name '$THREAD_NAME' (want 1-64 characters: A-Z, a-z, 0-9, dot, underscore, hyphen)"
fi
if [[ -n "$THREAD_NAME" && "$SESSION_REQUEST" == "live" ]]; then
  thread_refuse "omnilane: --thread cannot be combined with --live; threads run single-shot and a live session is already a conversation"
fi
if [[ -n "$THREAD_NAME" && -n "$OVERRIDE_VENDOR" ]]; then
  case "$OVERRIDE_VENDOR" in
    claude|codex|grok|gemini) ;;
    *)
      thread_refuse "omnilane: --thread is supported for claude, codex, grok and gemini only; resolved vendor '$OVERRIDE_VENDOR' cannot continue a thread"
      ;;
  esac
fi
if [[ "$SESSION_REQUEST" != "auto" && "$BACKGROUND" -ne 1 &&
      ! ( -n "$THREAD_NAME" && "$SESSION_REQUEST" == "single-shot" ) ]]; then
  echo "omnilane: --${SESSION_REQUEST} requires --background" >&2
  exit 2
fi

LANE="$1"
TASK="$2"
[[ "$LANE" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid lane name" >&2; exit 2; }
# A typo like --mode advice must not fall through to the write-enabled branch.
[[ "$MODE" == "advise" || "$MODE" == "work" || "$MODE" == "sysops" ]] || { echo "omnilane: invalid --mode (advise|work|sysops)" >&2; exit 2; }
if [[ -n "$OVERRIDE_VENDOR" ]] &&
   ! [[ "$OVERRIDE_VENDOR" =~ ^(${OMNILANE_OVERRIDE_VENDOR_ALT})$ ]]; then
  echo "omnilane: invalid vendor (${OMNILANE_OVERRIDE_VENDOR_ALT})" >&2
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

if [[ -n "$THREAD_NAME" ]]; then
  case "$VENDOR" in
    claude|codex|grok|gemini) ;;
    *)
      thread_refuse "omnilane: --thread is supported for claude, codex, grok and gemini only; resolved vendor '$VENDOR' cannot continue a thread"
      ;;
  esac
  THREAD_WORKDIR="$(cd -- "$WORKDIR" 2>/dev/null && pwd -P)" || {
    thread_refuse "omnilane: thread $THREAD_NAME workdir is not accessible: $WORKDIR"
  }
  WORKDIR="$THREAD_WORKDIR"
  THREADS_ROOT="$OMNILANE_HOME/threads"
  if [[ -L "$THREADS_ROOT" || ( -e "$THREADS_ROOT" && ! -d "$THREADS_ROOT" ) ]]; then
    thread_refuse "omnilane: unsafe thread store path (want real directory): $THREADS_ROOT"
  fi
  if [[ "$DRY_RUN" -ne 1 ]]; then
    prepare_threads_store || thread_refuse "omnilane: thread store could not be prepared"
  fi
  THREAD_STATE_FILE="$THREADS_ROOT/$THREAD_NAME.json"
  if [[ -e "$THREAD_STATE_FILE" || -L "$THREAD_STATE_FILE" ]]; then
    read_thread_state "$THREAD_STATE_FILE" "$THREAD_NAME" || {
      thread_refuse "omnilane: thread $THREAD_NAME state is not safely readable"
    }
    if [[ "$THREAD_STATE_VENDOR" != "$VENDOR" ]]; then
      thread_refuse "omnilane: thread $THREAD_NAME pinned vendor '$THREAD_STATE_VENDOR' but resolved vendor is '$VENDOR'"
    fi
    if [[ "$THREAD_STATE_MODEL" != "$MODEL" ]]; then
      thread_refuse "omnilane: thread $THREAD_NAME pinned model '$THREAD_STATE_MODEL' but resolved model is '$MODEL'"
    fi
    if [[ "$THREAD_STATE_EFFORT" != "$EFFORT" ]]; then
      thread_refuse "omnilane: thread $THREAD_NAME pinned effort '$THREAD_STATE_EFFORT' but resolved effort is '$EFFORT'"
    fi
    if [[ "$THREAD_STATE_WORKDIR" != "$WORKDIR" ]]; then
      thread_refuse "omnilane: thread $THREAD_NAME pinned workdir '$THREAD_STATE_WORKDIR' but effective workdir is '$WORKDIR'"
    fi
    THREAD_MODE="resume"
    THREAD_ID="$THREAD_STATE_SESSION_ID"
    THREAD_TURN=$((THREAD_STATE_TURNS + 1))
    THREAD_CREATED="$THREAD_STATE_CREATED"
  else
    THREAD_MODE="new"
    case "$VENDOR" in
      codex|gemini) THREAD_ID="pending" ;;
      *)
        THREAD_ID="$(generate_thread_id)" || {
          thread_refuse "omnilane: thread $THREAD_NAME could not generate a session id"
        }
        ;;
    esac
    THREAD_TURN=1
    THREAD_CREATED=""
  fi
  export OMNILANE_THREAD_MODE="$THREAD_MODE"
  export OMNILANE_THREAD_ID="$THREAD_ID"
  export OMNILANE_THREAD_NAME="$THREAD_NAME"
fi

if [[ "$VENDOR" == "off" ]]; then
  echo "omnilane: lane '$LANE' is disabled in routing config" >&2; exit 3
fi
RUNNER="$OMNILANE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omnilane: no runner for vendor '$VENDOR'" >&2; exit 2; }

SESSION_MODE="single-shot"
if [[ -z "$THREAD_NAME" && "$SESSION_REQUEST" != "single-shot" ]] && live_vendor_capable "$VENDOR"; then
  SESSION_MODE="live"
fi
if [[ "$SESSION_REQUEST" == "live" ]] && ! live_vendor_capable "$VENDOR"; then
  echo "omnilane: vendor '$VENDOR' cannot run live; live-capable vendors: $(live_capable_vendors)" >&2
  exit 2
fi
export OMNILANE_SESSION_MODE="$SESSION_MODE"
if [[ "$SESSION_REQUEST" == "live" ]]; then
  export OMNILANE_LIVE_REQUIRED=1
else
  export OMNILANE_LIVE_REQUIRED=0
fi

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

IDLE_TIMEOUT="$OVERRIDE_IDLE_TIMEOUT"
[[ -n "$IDLE_TIMEOUT" ]] || IDLE_TIMEOUT="${OMNILANE_IDLE_TIMEOUT:-900}"
[[ "$IDLE_TIMEOUT" =~ ^(0|[1-9][0-9]*)$ ]] || {
  echo "omnilane: invalid idle timeout (want a non-negative integer of seconds)" >&2
  exit 2
}
export OMNILANE_IDLE_TIMEOUT="$IDLE_TIMEOUT"

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
if [[ "$VENDOR" == "codex" && ( "$MODE" == "work" || "$MODE" == "sysops" ) ]]; then
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
FOREMAN_SESSION=""
if [[ -n "${CLAUDE_CODE_SESSION_ID+x}" ]]; then
  if [[ "$CLAUDE_CODE_SESSION_ID" =~ ^[A-Za-z0-9._:-]+$ &&
    "${#CLAUDE_CODE_SESSION_ID}" -le 256 ]]; then
    FOREMAN_SESSION="$CLAUDE_CODE_SESSION_ID"
  fi
elif resolved_foreman_session="$(find_foreman_session "$$" 2>/dev/null)"; then
  FOREMAN_SESSION="$resolved_foreman_session"
fi

if [[ "$TASK" == "-" ]]; then
  (umask 077; cat > "$JOB_DIR/task.txt")
else
  (umask 077; printf '%s\n' "$TASK" > "$JOB_DIR/task.txt")
fi

# meta "timeout" is the resolved per-CLI-call watchdog cap, not a whole-job total.
THREAD_TURN_JSON="${THREAD_TURN:-null}"
if [[ -n "$THREAD_NAME" ]]; then
  (umask 077; printf '{"lane":"%s","vendor":"%s","session_mode":"%s","idle_timeout":%s,"model":"%s","effort":"%s","timeout":%s,"job_timeout":%s,"mode":"%s","workdir":"%s","foreman_session":"%s","thread":"%s","thread_turn":%s,"candidate":"%s/%s","started":"%s"}\n' \
    "$(json_escape "$LANE")" "$(json_escape "$VENDOR")" "$(json_escape "$SESSION_MODE")" \
    "$IDLE_TIMEOUT" "$(json_escape "$MODEL")" "$(json_escape "$EFFORT")" \
    "$TIMEOUT" "$JOB_TIMEOUT_JSON" "$(json_escape "$MODE")" \
    "$(json_escape "$WORKDIR")" "$(json_escape "$FOREMAN_SESSION")" \
    "$(json_escape "$THREAD_NAME")" "$THREAD_TURN_JSON" \
    "$RESOLVED_IDX" "$RESOLVED_TOTAL" \
    "$(date -u +%FT%TZ)" > "$JOB_DIR/meta.json")
else
  (umask 077; printf '{"lane":"%s","vendor":"%s","session_mode":"%s","idle_timeout":%s,"model":"%s","effort":"%s","timeout":%s,"job_timeout":%s,"mode":"%s","workdir":"%s","foreman_session":"%s","candidate":"%s/%s","started":"%s"}\n' \
    "$(json_escape "$LANE")" "$(json_escape "$VENDOR")" "$(json_escape "$SESSION_MODE")" \
    "$IDLE_TIMEOUT" "$(json_escape "$MODEL")" "$(json_escape "$EFFORT")" \
    "$TIMEOUT" "$JOB_TIMEOUT_JSON" "$(json_escape "$MODE")" \
    "$(json_escape "$WORKDIR")" "$(json_escape "$FOREMAN_SESSION")" \
    "$RESOLVED_IDX" "$RESOLVED_TOTAL" \
    "$(date -u +%FT%TZ)" > "$JOB_DIR/meta.json")
fi

if [[ -n "$THREAD_NAME" ]]; then
  printf 'omnilane: thread %s turn %s (%s session %s, %s)\n' \
    "$THREAD_NAME" "$THREAD_TURN" "$VENDOR" "$THREAD_ID" "$THREAD_MODE"
fi

secure_job_files() {
  find "$JOB_DIR" -type f -exec chmod 600 {} +
}

sanitize_utf8() {
  local max="$1" drop_leading="${2:-0}"
  perl -MEncode -e '
    use strict;
    use warnings;
    binmode STDIN, ":raw";
    binmode STDOUT, ":raw";
    local $/;
    my $bytes = <STDIN> // "";
    my ($max, $drop_leading) = @ARGV;
    $bytes =~ s/\A[\x80-\xBF]+// if $drop_leading;
    my $text = Encode::decode("UTF-8", $bytes, Encode::FB_DEFAULT);
    my $encoded = Encode::encode("UTF-8", $text);
    while (length($encoded) > $max && length($text)) {
      $text =~ s/\A.//s;
      $encoded = Encode::encode("UTF-8", $text);
    }
    print $encoded;
  ' "$max" "$drop_leading"
}

completion_tail() {
  local path="$1" size=0 keep value="" sentinel=$'\001'
  local note=$'[truncated: leading output omitted]\n'
  local LC_ALL=C

  if [[ -f "$path" && ! -L "$path" ]]; then
    size="$(wc -c < "$path" 2>/dev/null | tr -d '[:space:]')" || return 1
    [[ "$size" =~ ^[0-9]+$ ]] || return 1
    if [[ "$size" -gt 2000 ]]; then
      keep=$((2000 - ${#note}))
      value="$({
        printf '%s' "$note"
        tail -c "$keep" "$path" | sanitize_utf8 "$keep" 1
        printf '%s' "$sentinel"
      })" || return 1
    else
      value="$({
        sanitize_utf8 2000 0 < "$path"
        printf '%s' "$sentinel"
      })" || return 1
    fi
    value="${value%"$sentinel"}"
  fi
  printf '%s' "$value"
}

write_completion_record() {
  local rc="$1" inbox="$OMNILANE_HOME/inbox"
  local final="$inbox/$JOB_ID.json" tmp="$inbox/.$JOB_ID.tmp.$$-$RANDOM"
  local tail_value finished old_umask write_rc=0 sentinel=$'\001'

  prepare_inbox_store || return 1
  tail_value="$(completion_tail "$JOB_DIR/out.txt"; printf '%s' "$sentinel")" || return 1
  tail_value="${tail_value%"$sentinel"}"
  finished="$(date -u +%FT%TZ)" || return 1
  old_umask="$(umask)"
  umask 077
  if [[ -n "$THREAD_NAME" ]]; then
    printf '{"job_id":"%s","lane":"%s","vendor":"%s","model":"%s","mode":"%s","workdir":"%s","foreman_session":"%s","thread":"%s","thread_turn":%s,"exit":%s,"finished":"%s","tail":"%s"}\n' \
      "$(json_escape "$JOB_ID")" "$(json_escape "$LANE")" "$(json_escape "$VENDOR")" \
      "$(json_escape "$MODEL")" "$(json_escape "$MODE")" "$(json_escape "$WORKDIR")" \
      "$(json_escape "$FOREMAN_SESSION")" "$(json_escape "$THREAD_NAME")" \
      "$THREAD_TURN_JSON" "$rc" "$(json_escape "$finished")" \
      "$(json_escape "$tail_value")" > "$tmp" || write_rc=$?
  else
    printf '{"job_id":"%s","lane":"%s","vendor":"%s","model":"%s","mode":"%s","workdir":"%s","foreman_session":"%s","exit":%s,"finished":"%s","tail":"%s"}\n' \
      "$(json_escape "$JOB_ID")" "$(json_escape "$LANE")" "$(json_escape "$VENDOR")" \
      "$(json_escape "$MODEL")" "$(json_escape "$MODE")" "$(json_escape "$WORKDIR")" \
      "$(json_escape "$FOREMAN_SESSION")" \
      "$rc" "$(json_escape "$finished")" "$(json_escape "$tail_value")" \
      > "$tmp" || write_rc=$?
  fi
  if [[ "$write_rc" -eq 0 ]]; then
    chmod 600 "$tmp" || write_rc=$?
  fi
  if [[ "$write_rc" -eq 0 ]]; then
    mv "$tmp" "$final" || write_rc=$?
  fi
  umask "$old_umask"
  # no -f: force-flag rm is blocked by some environments' destructive guards
  rm "$tmp" 2>/dev/null || true
  return "$write_rc"
}

finish_job() {
  local rc="$1"
  if [[ -n "$THREAD_NAME" ]]; then
    if [[ "$rc" -eq 0 ]]; then
      update_thread_state || rc=1
    elif [[ "$THREAD_MODE" == "resume" ]]; then
      append_thread_notice "omnilane: thread $THREAD_NAME could not be continued; stored $VENDOR session $THREAD_ID was preserved"
    fi
  fi
  secure_job_files
  (umask 077; printf '%s\n' "$rc" > "$JOB_DIR/exit")
  if [[ "${OMNILANE_INBOX:-1}" != "0" ]]; then
    write_completion_record "$rc" >/dev/null 2>&1 || true
  fi
  FINISHED_RC="$rc"
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
  return "$FINISHED_RC"
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
