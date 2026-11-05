#!/usr/bin/env bash

# Only an explicit child invocation from an isolated parent keeps fixture state.
# A stale environment marker alone must never bypass standalone isolation.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CASE="${1:-}"
TEST_BASE="$ROOT/.test-scratch"
mkdir -p "$TEST_BASE"
# One root per run: the cases and the cases they run nested share it and nothing else
# does, so jobs and processes of another run in this checkout are never counted or touched.
own_run_root=0
case "${LIVE_RUN_ROOT:-}" in
  "$TEST_BASE"/omnilane-live-run.*) [[ -d "$LIVE_RUN_ROOT" ]] || LIVE_RUN_ROOT="" ;;
  *) LIVE_RUN_ROOT="" ;;
esac
if [[ -z "$LIVE_RUN_ROOT" ]]; then
  LIVE_RUN_ROOT="$(mktemp -d "$TEST_BASE/omnilane-live-run.XXXXXX")"
  own_run_root=1
fi
export LIVE_RUN_ROOT
TEST_ROOT="$(mktemp -d "$LIVE_RUN_ROOT/omnilane-live-tests.XXXXXX")"

test_completed=0
# Runs a command with a limit of $1 seconds, as common.sh does for ps: alarm survives
# exec and so does the SIG_IGN cleanup sets for INT and TERM. A timeout returns 142.
with_limit() {
  perl -e '$SIG{ALRM} = "DEFAULT"; alarm shift; exec { $ARGV[0] } @ARGV; exit 126' "$@"
}

# One deadline for the whole run, in four phases (seconds from the start of the run):
#   running cases  until RUN_DEADLINE  520  run_case ends a case at its own limit or here
#   cancel, wait   until STOP_BY       555  a timed-out case gets 5s of diagnostics, then
#                                           stop_jobs gets 30s: PID files until 25s before
#                                           its end, one process snapshot until 20s before,
#                                           cancel until 10s before, waiting the rest
#   force stop     until FORCE_BY      575  force_stop_jobs gets 20s: one process table and
#                                           one file-holder search for every job, TERM to
#                                           every confirmed group at once, wait, one more
#                                           table, KILL, wait
#   report         until FINAL_BY      585  end the timed-out case's own group (TERM 3s,
#                                           KILL 2s), name each job that did not stop,
#                                           remove this run's directories (5s)
# Every step that can block runs through within, which gives it what is left before its
# phase's deadline and ends it with SIGALRM when that runs out, also halfway through. A
# step never starts with less than a whole second left. Waits poll the clock every 0.1s.
# SECONDS counts whole seconds, so a step can end up to a second after its deadline;
# phases are absolute times, so that never adds up across steps. The last step of the
# report phase can end by FINAL_BY + 1s, after which only kill, exit and the shell's own
# exit remain: about 587s after LIVE_EPOCH0, 13s inside CI's 600s step limit. The
# deadline is passed down as a wall-clock time, so a case's own cleanup and the cases it
# runs nested keep to it as well.
LIVE_EPOCH0="$(date +%s)"
LIVE_RUN_LIMIT="${OMNILANE_LIVE_RUN_LIMIT:-520}"
[[ "${LIVE_FINAL_BY_EPOCH:-}" =~ ^[0-9]+$ ]] || LIVE_FINAL_BY_EPOCH=$((LIVE_EPOCH0 + LIVE_RUN_LIMIT + 5 + 30 + 20 + 10))
export LIVE_FINAL_BY_EPOCH
FINAL_BY=$((SECONDS + LIVE_FINAL_BY_EPOCH - LIVE_EPOCH0))
FORCE_BY=$((FINAL_BY - 10))
STOP_BY=$((FORCE_BY - 20))
RUN_DEADLINE=$((STOP_BY - 35))
# Seconds left before deadline $2 (STOP_BY when omitted), at most $1, never below 0.
budget() {
  local left=$(( ${2:-$STOP_BY} - SECONDS ))
  [[ "$left" -le "$1" ]] || left="$1"
  [[ "$left" -ge 0 ]] || left=0
  printf '%s\n' "$left"
}
# Runs a command limited to what budget $1 $2 leaves; with no whole second left it does
# not start and returns 142, as a timeout would.
within() {
  local seconds
  seconds="$(budget "$1" "$2")"
  shift 2
  [[ "$seconds" -ge 1 ]] || return 142
  with_limit "$seconds" "$@"
}
# Reads the first line of file $1 into PID_READ; a private regular file, so this never blocks.
read_pid_file() {
  PID_READ=""
  [[ -f "$1" ]] || return 0
  read -r PID_READ < "$1" 2>/dev/null || true
}

# Every background job a case starts is registered as "<home><TAB><job id>" in this
# process's list and in every list named by LIVE_JOB_LISTS, which a child inherits. So a
# case, the cases it runs nested, and run_case above them all know each job, wherever it
# was started: dispatch puts a background job in its own process group and disowns it,
# so ending a case's group never reaches the job.
LIVE_JOBS="$TEST_ROOT/live-jobs"
: > "$LIVE_JOBS"
LIVE_JOB_LISTS="${LIVE_JOB_LISTS:+$LIVE_JOB_LISTS:}$LIVE_JOBS"
export LIVE_JOB_LISTS
register_job() {
  local list rest="$LIVE_JOB_LISTS"
  while [[ -n "$rest" ]]; do
    list="${rest%%:*}"
    [[ "$rest" == *:* ]] && rest="${rest#*:}" || rest=""
    printf '%s\t%s\n' "$1" "$2" >> "$list"
  done
}
# Prints each job in list $1 with its controller PID and that PID's command line, from one
# process listing for all jobs that gets at most 2s and nothing after deadline $2.
describe_jobs() {
  local home id listing="" listed=0 command
  if listing="$(within 2 "$2" ps -axo pid=,command=)"; then listed=1; fi
  while IFS=$'\t' read -r home id <&3; do
    [[ -n "$id" ]] || continue
    read_pid_file "$home/jobs/$id/pid"
    command="(controller PID not recorded)"
    if [[ "$PID_READ" =~ ^[1-9][0-9]*$ ]]; then
      command="(not running or not listed in time)"
      if [[ "$listed" -eq 1 ]]; then
        command="$(printf '%s\n' "$listing" | within 1 "$2" awk -v pid="$PID_READ" \
          '$1 == pid { sub(/^[[:space:]]*[0-9]+[[:space:]]+/, ""); print; exit }')" || command=""
        [[ -n "$command" ]] || command="(not running or not listed in time)"
      fi
    fi
    printf 'job %s controller PID %s: %s\n' "$id" "${PID_READ:-none}" "$command"
  done 3< "$1"
}

# Forced stop. Every job's processes are identified from one process table and one search
# for the processes holding a job's worker.log open, both shared by all jobs, so slow
# queries cost the same with one job or ten. A process is a job's when it is
#   - the controller of its PID file, with the start time dispatch recorded beside it;
#   - a process holding <job dir>/worker.log open, which dispatch makes the controller's
#     stdout, for a job with no PID file or with a controller that cannot be confirmed;
#   - a descendant of one of those, or in the process group of one of those;
#   - recorded as one of the job's processes earlier and still the same process, with
#     the same PID and start time, even after its group's leader exited.
# Groups are signalled only while one of their members is such a process in a table taken
# just before the signal, and never the group of this script or of any of its ancestors.
# A PID that is listed with another start time than the one recorded is reused: it is
# printed and gets no signal.
#
# Writes the process table to file $1 (pid, parent, group, start time in UTC); gets at
# most $2 seconds and nothing after deadline $3.
take_process_table() {
  within "$2" "$3" env TZ=UTC LC_ALL=C ps -axo pid=,ppid=,pgid=,lstart= > "$1" 2>/dev/null || return 1
  [[ -s "$1" ]]
}
# Prints "PID PATH" for each process holding one of the files $3... open, from /proc where
# there is one, else with lsof. The whole search is one process limited to $1 seconds and
# nothing after deadline $2, so running out of time ends it halfway through. The test
# hooks LIVE_PROC_ROOT, LIVE_LSOF and LIVE_HOLDER_SCAN_DELAY stand in for /proc, lsof and
# a slow system.
file_holders() {
  local seconds="$1" by="$2" proc="${LIVE_PROC_ROOT:-/proc}" lsof="${LIVE_LSOF:-}" out rc=0
  out="$TEST_ROOT/holders.$$"
  shift 2
  [[ "$#" -gt 0 ]] || return 0
  if [[ -d "$proc/self/fd" ]]; then
    within "$seconds" "$by" env LIVE_PROC_ROOT="$proc" perl -e '
      my %want = map { $_ => 1 } @ARGV;
      my $delay = $ENV{LIVE_HOLDER_SCAN_DELAY} || 0;
      for my $fd (glob("$ENV{LIVE_PROC_ROOT}/[0-9]*/fd/*")) {
        select(undef, undef, undef, $delay) if $delay;
        my $target = readlink($fd);
        next unless defined $target && $want{$target};
        print "$1 $target\n" if $fd =~ m{/([0-9]+)/fd/[^/]+\z};
      }' "$@"
    return
  fi
  if [[ -z "$lsof" ]]; then
    [[ -x /usr/sbin/lsof ]] && lsof=/usr/sbin/lsof
    [[ -n "$lsof" || ! -x /usr/bin/lsof ]] || lsof=/usr/bin/lsof
  fi
  [[ -n "$lsof" ]] || return 3
  # Into a file, not a command substitution: lsof forks helpers for calls that can block
  # (see -O in lsof(1)), and a helper that outlives lsof would hold a substitution's pipe
  # open past the limit. The shell waits only for lsof itself.
  within "$seconds" "$by" "$lsof" -n -P -w -F pn -- "$@" > "$out" 2>/dev/null || rc=$?
  # lsof exits 1 when nobody holds the files: an answer, not a failure.
  [[ "$rc" -eq 0 || ( "$rc" -eq 1 && ! -s "$out" ) ]] || return "$rc"
  awk '/^p[0-9]+$/ { pid = substr($0, 2) } /^n/ && pid != "" { print pid, substr($0, 2) }' "$out"
}
# Reads process table $1, roots $2 ("job pid start", start "-" when none was recorded)
# and members $3 (earlier output of this function) and prints, for the processes that
# are still the job's: "M job pid group start" for each and "G job group" for each group
# to signal; "R job pid recorded-start listed-start" for each reused PID. $4 is the PID
# whose group and whose ancestors' groups are never signalled. Gets at most $5 seconds
# and nothing after deadline $6.
job_processes() {
  within "$5" "$6" awk -v self="$4" '
    function add(job, pid,   n, k, list) {
      if (pid in owner) return
      owner[pid] = job
      n = split(kids[pid], list, " ")
      for (k = 1; k <= n; k++) add(job, list[k])
    }
    function check(job, pid, start) {
      if (!(pid in begun)) return
      if (start != "-" && begun[pid] != start) {
        printf "R\t%s\t%s\t%s\t%s\n", job, pid, start, begun[pid]
        return
      }
      add(job, pid)
    }
    FILENAME == ARGV[1] {
      if (NF < 8) next
      parent[$1] = $2; group[$1] = $3; begun[$1] = $4 " " $5 " " $6 " " $7 " " $8
      kids[$2] = kids[$2] " " $1
      next
    }
    FILENAME == ARGV[2] { check($1, $2, $3 == "-" ? "-" : $3 " " $4 " " $5 " " $6 " " $7); next }
    FILENAME == ARGV[3] && $1 == "M" { check($2, $3, $5 " " $6 " " $7 " " $8 " " $9); next }
    END {
      for (pid = self; pid in parent && !(pid in seen); pid = parent[pid]) {
        seen[pid] = 1; mine[group[pid]] = 1
      }
      for (pid in owner) {
        g = group[pid]
        if (g + 0 > 1 && !(g in mine) && !(g in leader)) leader[g] = owner[pid]
      }
      for (pid in group) if ((group[pid] in leader) && !(pid in owner)) owner[pid] = leader[group[pid]]
      for (pid in owner) printf "M\t%s\t%s\t%s\t%s\n", owner[pid], pid, group[pid], begun[pid]
      for (g in leader) printf "G\t%s\t%s\n", leader[g], g
    }' "$1" "$2" "$3"
}
# Writes the roots of the jobs at indexes $3... of the arrays homes and ids to file $1:
# each confirmed controller, and the holders of worker.log for a job without one. The
# holder search runs once for all of those jobs, at most 3s and not after deadline $2.
# Unconfirmed controllers and jobs with no PID file are printed.
write_roots() {
  local out="$1" by="$2" i dir start line pid path j
  local -a logs=() log_jobs=()
  shift 2
  : > "$out"
  for i in "$@"; do
    dir="${homes[i]}/jobs/${ids[i]}"
    read_pid_file "$dir/pid"
    start=""
    [[ ! -f "$dir/pid.start" ]] || read -r start < "$dir/pid.start" 2>/dev/null || true
    if [[ "$PID_READ" =~ ^[1-9][0-9]*$ && -n "$start" ]]; then
      printf '%s %s %s\n' "$i" "$PID_READ" "$start" >> "$out"
    else
      [[ "$PID_READ" =~ ^[1-9][0-9]*$ ]] && printf 'live job %s: PID %s has no recorded start time\n' "${ids[i]}" "$PID_READ" >&2
    fi
    if [[ -e "$dir/worker.log" ]]; then logs+=("$dir/worker.log"); log_jobs+=("$i"); fi
  done
  [[ "${#logs[@]}" -gt 0 ]] || return 0
  line="$(file_holders 3 "$by" "${logs[@]}")" || {
    printf 'live jobs: could not search for the holders of %s worker.log files in time\n' "${#logs[@]}" >&2
    return 0
  }
  while read -r pid path; do
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
    for j in "${!logs[@]}"; do
      [[ "${logs[j]}" == "$path" ]] || continue
      printf '%s %s -\n' "${log_jobs[j]}" "$pid" >> "$out"
    done
  done <<< "$line"
}
# Prints the reused PIDs in job_processes output $1 and keeps its groups as SIGNAL_GROUPS.
read_decision() {
  local kind job a b c
  SIGNAL_GROUPS=""
  while IFS=$'\t' read -r kind job a b c; do
    case "$kind" in
      G) SIGNAL_GROUPS="$SIGNAL_GROUPS $a"
         printf 'live job %s: process group %s\n' "${ids[job]}" "$a" >&2 ;;
      R) printf 'live job %s: PID %s now started %s, not %s as recorded; reused, not signalled\n' \
           "${ids[job]}" "$a" "$c" "$b" >&2 ;;
    esac
  done < "$1"
}
groups_alive() {
  local group
  for group in $1; do kill -0 -- "-$group" 2>/dev/null && return 0; done
  return 1
}
# Ends the processes of the jobs at indexes $2... (of homes and ids) in at most 20s and
# never after FORCE_BY, all jobs together; $1 is the file of members recorded earlier.
# Prints what it signals and returns the members still running as FORCE_LEFT.
force_stop_jobs() {
  local recorded="$1" end tmp table roots decided group left
  shift
  end=$((SECONDS + 20))
  [[ "$end" -le "$FORCE_BY" ]] || end="$FORCE_BY"
  tmp="$TEST_ROOT/force.$$"
  table="$tmp.table" roots="$tmp.roots" decided="$tmp.term"
  : > "$decided"
  # TERM: PID files read once more, one table (3s), one holder search (3s), decide (1s).
  if take_process_table "$table" 3 "$((end - 14))"; then
    write_roots "$roots" "$((end - 14))" "$@"
    job_processes "$table" "$roots" "$recorded" "$$" 1 "$((end - 13))" > "$decided" ||
      printf 'live jobs: could not decide which groups to end in time\n' >&2
  else
    printf 'live jobs: could not list processes in time to end %s job(s)\n' "$#" >&2
  fi
  read_decision "$decided"
  for group in $SIGNAL_GROUPS; do
    printf 'live jobs: TERM process group %s\n' "$group" >&2
    kill -TERM -- "-$group" 2>/dev/null || true
  done
  left=$((SECONDS + $(budget 4 "$((end - 9))")))
  while groups_alive "$SIGNAL_GROUPS" && [[ "$SECONDS" -lt "$left" ]]; do sleep 0.1; done
  # KILL: one more table (3s), and only groups that still hold a member recorded above
  # with the same PID and start time, even when the group's leader has exited (1s).
  : > "$roots"
  : > "$decided.kill"
  if [[ -s "$decided" ]] && take_process_table "$table" 3 "$((end - 6))"; then
    job_processes "$table" "$roots" "$decided" "$$" 1 "$((end - 5))" > "$decided.kill" ||
      printf 'live jobs: could not decide which groups to KILL in time\n' >&2
  fi
  read_decision "$decided.kill"
  for group in $SIGNAL_GROUPS; do
    printf 'live jobs: KILL process group %s\n' "$group" >&2
    kill -KILL -- "-$group" 2>/dev/null || true
  done
  left=$((SECONDS + $(budget 4 "$((end - 1))")))
  while groups_alive "$SIGNAL_GROUPS" && [[ "$SECONDS" -lt "$left" ]]; do sleep 0.1; done
  FORCE_LEFT="$decided"
}
# A member recorded in file $2 for job index $1 that is still running.
member_alive() {
  local kind job pid rest
  while IFS=$'\t' read -r kind job pid rest; do
    [[ "$kind" == M && "$job" == "$1" ]] || continue
    kill -0 "$pid" 2>/dev/null && { printf '%s\n' "$pid"; return 0; }
  done < "$2"
  return 1
}

# Stops every job in list $1, all of them together, in at most 30s and never after
# STOP_BY, and then, for a job that did not stop, its processes in at most 20s more and
# never after FORCE_BY:
#   1. wait for each job's PID file, since dispatch prints the job id before it writes one
#      (until 25s before the end);
#   2. when a controller runs or a PID file is missing, record each job's processes from
#      one process table and one file-holder search (until 20s before the end);
#   3. cancel each job with no exit recorded (until 10s before the end);
#   4. wait for every controller and every recorded process to exit, also for a job whose
#      exit is recorded, since dispatch writes the exit file before the inbox record;
#   5. force_stop_jobs for the jobs with a process still running or no PID file;
#   6. fail by name for a job with no PID file or with a process still running.
# Only homes inside this run's root are touched.
stop_jobs() {
  local list="$1" home id dir rc deadline waiting failed=0 i end pid_by snap_by cancel_by
  local recorded table roots alive
  local -a homes=() ids=() cancels=() pending=()
  [[ -s "$list" ]] || return 0
  while IFS=$'\t' read -r home id <&3; do
    [[ -n "$id" ]] || continue
    case "$home" in
      "$LIVE_RUN_ROOT"/*) homes+=("$home"); ids+=("$id"); cancels+=("not run, exit recorded") ;;
      *) printf 'not stopping job %s: its home %s is outside this run'"'"'s root %s\n' "$id" "$home" "$LIVE_RUN_ROOT" >&2
         failed=1 ;;
    esac
  done 3< "$list"
  [[ "${#ids[@]}" -gt 0 ]] || return "$failed"
  end=$((SECONDS + 30))
  [[ "$end" -le "$STOP_BY" ]] || end="$STOP_BY"
  pid_by=$((end - 25)) snap_by=$((end - 20)) cancel_by=$((end - 10))
  recorded="$TEST_ROOT/members.$$"
  : > "$recorded"

  deadline=$((SECONDS + $(budget 5 "$pid_by")))
  while :; do
    waiting=0
    for i in "${!ids[@]}"; do
      dir="${homes[i]}/jobs/${ids[i]}"
      # A case removes a job's directory only after it confirmed the job stopped.
      [[ ! -d "$dir" || -s "$dir/pid" ]] || waiting=1
    done
    [[ "$waiting" -eq 1 && "$SECONDS" -lt "$deadline" ]] || break
    sleep 0.1
  done
  for i in "${!ids[@]}"; do
    dir="${homes[i]}/jobs/${ids[i]}"
    [[ -d "$dir" ]] || continue
    read_pid_file "$dir/pid"
    if [[ ! "$PID_READ" =~ ^[1-9][0-9]*$ ]] || kill -0 "$PID_READ" 2>/dev/null; then pending+=("$i"); fi
  done
  if [[ "${#pending[@]}" -gt 0 ]]; then
    table="$recorded.table" roots="$recorded.roots"
    if take_process_table "$table" 3 "$snap_by"; then
      write_roots "$roots" "$snap_by" "${pending[@]}"
      : > "$recorded.none"
      job_processes "$table" "$roots" "$recorded.none" "$$" 1 "$snap_by" > "$recorded" || : > "$recorded"
    fi
  fi
  for i in "${!ids[@]}"; do
    dir="${homes[i]}/jobs/${ids[i]}"
    [[ -d "$dir" && ! -e "$dir/exit" ]] || continue
    rc=0
    within 10 "$cancel_by" env OMNILANE_HOME="${homes[i]}" \
      "$ROOT/scripts/jobs.sh" cancel "${ids[i]}" </dev/null >/dev/null 2>&1 || rc=$?
    cancels[i]="rc $rc"
  done
  deadline=$((SECONDS + $(budget 30 "$end")))
  while :; do
    waiting=0
    for i in "${!ids[@]}"; do
      read_pid_file "${homes[i]}/jobs/${ids[i]}/pid"
      [[ ! "$PID_READ" =~ ^[1-9][0-9]*$ ]] || ! kill -0 "$PID_READ" 2>/dev/null || waiting=1
      [[ "$waiting" -eq 1 ]] || ! member_alive "$i" "$recorded" >/dev/null || waiting=1
    done
    [[ "$waiting" -eq 1 && "$SECONDS" -lt "$deadline" ]] || break
    sleep 0.1
  done

  pending=()
  for i in "${!ids[@]}"; do
    dir="${homes[i]}/jobs/${ids[i]}"
    [[ -d "$dir" ]] || continue
    read_pid_file "$dir/pid"
    if [[ ! "$PID_READ" =~ ^[1-9][0-9]*$ ]] || kill -0 "$PID_READ" 2>/dev/null ||
      member_alive "$i" "$recorded" >/dev/null; then
      pending+=("$i")
    fi
  done
  [[ "${#pending[@]}" -gt 0 ]] || return "$failed"
  printf 'live jobs: %s job(s) still running or without a PID file after cancel and wait; ending their process groups\n' \
    "${#pending[@]}" >&2
  force_stop_jobs "$recorded" "${pending[@]}"

  for i in "${pending[@]}"; do
    dir="${homes[i]}/jobs/${ids[i]}"
    read_pid_file "$dir/pid"
    if [[ ! "$PID_READ" =~ ^[1-9][0-9]*$ ]]; then
      printf 'live job %s: no PID file after waiting for it (cancel %s)\n' "${ids[i]}" "${cancels[i]}" >&2
      failed=1
    elif kill -0 "$PID_READ" 2>/dev/null; then
      printf 'live job %s: controller PID %s still running after cancel, wait, TERM and KILL (cancel %s)\n' \
        "${ids[i]}" "$PID_READ" "${cancels[i]}" >&2
      failed=1
    elif alive="$(member_alive "$i" "$FORCE_LEFT")" || alive="$(member_alive "$i" "$recorded")"; then
      printf 'live job %s: process %s still running after cancel, wait, TERM and KILL (cancel %s)\n' \
        "${ids[i]}" "$alive" "${cancels[i]}" >&2
      failed=1
    else
      # Its processes were ended here, not by cancel: the job did not stop on its own.
      printf 'live job %s: did not stop after cancel and wait; its process groups were ended (cancel %s)\n' \
        "${ids[i]}" "${cancels[i]}" >&2
      failed=1
    fi
  done
  return "$failed"
}
cleanup() {
  local rc=$?
  trap - EXIT
  # A signal now would skip stopping the job; every step below has a limit.
  trap '' INT TERM HUP
  if [[ "$rc" -eq 0 && "$test_completed" -ne 1 ]]; then rc=1; fi
  # The directory goes only after every job this process or its nested cases started has
  # stopped. Removing it gets at most 5s and nothing after FINAL_BY, and each rmdir a
  # second; running out of time is printed and does not fail the run.
  if stop_jobs "$LIVE_JOBS"; then
    within 5 "$FINAL_BY" /bin/rm -rf -- "$TEST_ROOT" || printf 'could not remove %s in time\n' "$TEST_ROOT" >&2
    [[ "$own_run_root" -ne 1 ]] || within 1 "$FINAL_BY" rmdir "$LIVE_RUN_ROOT" 2>/dev/null || true
    within 1 "$FINAL_BY" rmdir "$TEST_BASE" 2>/dev/null || true
  else
    printf 'kept %s for the job that may still run\n' "$TEST_ROOT" >&2
    rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

# Sets LIVE_PIDS to the PIDs whose command line contains $1, and fails when ps fails or
# takes over 5s (or runs past deadline $2, FINAL_BY by default), so a failed query never
# reads as "nothing left". awk reads the mark from the environment so that its own
# command line does not match.
processes_under() {
  local listing
  LIVE_PIDS=""
  listing="$(within 5 "${2:-$FINAL_BY}" ps -axo pid=,command=)" || return 1
  [[ -n "$listing" ]] || return 1
  LIVE_PIDS="$(printf '%s\n' "$listing" | within 5 "${2:-$FINAL_BY}" env MARK="$1" \
    awk 'index($0, ENVIRON["MARK"]) { printf "%s ", $1 }')" || return 1
}

# Every case runs under its own limit and all of them before RUN_DEADLINE, so a slow or
# stuck case fails with its name, its PID, its jobs and what is still running instead of
# being killed by CI with no case named.
CASE_LIMIT="${OMNILANE_LIVE_CASE_LIMIT:-330}"
run_case() {
  local name="$1" pid rc=0 deadline started="$SECONDS" listing case_jobs diag_by
  case_jobs="$TEST_ROOT/case-$name.jobs"
  : > "$case_jobs"
  deadline=$((SECONDS + CASE_LIMIT))
  [[ "$deadline" -le "$RUN_DEADLINE" ]] || deadline="$RUN_DEADLINE"
  # Its own process group, so what is left of the case can be listed and stopped as one.
  set -m
  LIVE_JOB_LISTS="$case_jobs" bash "$0" --omnilane-offline-child "$name" &
  pid=$!
  set +m
  while kill -0 "$pid" 2>/dev/null && [[ "$SECONDS" -lt "$deadline" ]]; do
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    diag_by=$((SECONDS + 5))
    [[ "$diag_by" -le "$STOP_BY" ]] || diag_by="$STOP_BY"
    listing="$(within 3 "$diag_by" ps -axo pid=,pgid=,etime=,command=)" &&
      listing="$(printf '%s\n' "$listing" | within 1 "$diag_by" awk -v group="$pid" '$2 == group')" ||
      listing="(could not list processes in time)"
    printf 'case %s (PID %s) still running after %ss (case limit %ss, run limit %ss); waiting on:\n%s\n' \
      "$name" "$pid" "$((SECONDS - started))" "$CASE_LIMIT" "$LIVE_RUN_LIMIT" "$listing" >&2
    printf 'background jobs the case registered:\n%s\n' "$(describe_jobs "$case_jobs" "$diag_by")" >&2
    # Their own groups are outside the case's: cancel them, wait, and end what is left of
    # them first, and only then end the case. Whatever fails here is printed, and the run
    # fails below in any case.
    stop_jobs "$case_jobs" || true
    kill -TERM "$pid" 2>/dev/null || true
    deadline=$((SECONDS + $(budget 3 "$((FINAL_BY - 7))")))
    while kill -0 "$pid" 2>/dev/null && [[ "$SECONDS" -lt "$deadline" ]]; do sleep 0.2; done
    kill -KILL -- "-$pid" 2>/dev/null || true
    deadline=$((SECONDS + $(budget 2 "$((FINAL_BY - 5))")))
    while kill -0 "$pid" 2>/dev/null && [[ "$SECONDS" -lt "$deadline" ]]; do sleep 0.1; done
    # Reaped only once gone, so this never waits on a case that outlived KILL.
    if kill -0 "$pid" 2>/dev/null; then
      printf 'case %s (PID %s) still running after KILL\n' "$name" "$pid" >&2
    else
      wait "$pid" 2>/dev/null || true
    fi
    fail "case $name timed out"
  fi
  # The loop above ended because the case exited, so this wait returns at once.
  wait "$pid" || rc=$?
  # A nested case killed before its own cleanup ran may have left a job behind.
  stop_jobs "$case_jobs" || rc=1
  [[ "$rc" -eq 0 ]] || exit "$rc"
}

wait_for_file() {
  local path="$1" tries=0
  while [[ ! -f "$path" && "$tries" -lt 100 ]]; do
    sleep 0.1
    tries=$((tries + 1))
  done
  [[ -f "$path" ]]
}

wait_for_lines() {
  local path="$1" expected="$2" tries=0 count=0
  while [[ "$tries" -lt 100 ]]; do
    if [[ -f "$path" ]]; then
      count="$(wc -l < "$path" | tr -d '[:space:]')"
      [[ "$count" =~ ^[0-9]+$ ]] || count=0
      [[ "$count" -ge "$expected" ]] && return 0
    fi
    sleep 0.1
    tries=$((tries + 1))
  done
  return 1
}

make_claude() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" > "${FAKE_CLAUDE_ARGS:?}"
if [[ " $* " == *" --input-format stream-json "* ]]; then
  while IFS= read -r line; do
    printf '%s\n' "$line" >> "${FAKE_CLAUDE_INPUT:?}"
    printf '{"type":"result","is_error":false,"result":"claude live ok"}\n'
  done
else
  printf 'claude single-shot ok\n'
fi
EOF
  chmod +x "$path"
}

case_live_fail_fast() {
  local home="$TEST_ROOT/fail-fast" bin="$TEST_ROOT/fail-fast/bin" out rc=0
  mkdir -p "$home" "$bin"
  cat > "$bin/qwen" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$bin/qwen"
  printf 'triage: qwen qwen-default low\n' > "$home/routing.local.yaml"
  out="$(OMNILANE_HOME="$home" QWEN_BIN="$bin/qwen" \
    "$ROOT/scripts/dispatch.sh" --dry-run --live --background --vendor qwen \
      triage x 2>&1)" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "--live silently accepted non-capable vendor"
  [[ "$out" == *"claude"* && "$out" == *"gemini"* && "$out" == *"codex"* && "$out" == *"grok"* ]] ||
    fail "--live error did not name live-capable vendors: $out"
  [[ ! -e "$home/jobs" ]] || fail "fail-fast created job state"
}

case_live_call_timeout() {
  # Keep watchdog/dispatch/completion integration single-turn. The two-turn
  # cases use FIFO/pipe rendezvous and the real runners at the worker boundary;
  # process startup and jobs send must not race a fixed three-second alarm.
  case "$1" in
    gemini-two)
      python3 "$ROOT/tests/test_job_worker_close.py" JobWorkerCloseTests.test_review_gemini_two_completed_turns_use_last_output
      return ;;
    gemini-incomplete)
      python3 "$ROOT/tests/test_job_worker_close.py" JobWorkerCloseTests.test_review_gemini_second_turn_incomplete_stays_142
      return ;;
  esac
  local scenario="$1" vendor=gemini timeout=3 idle=0 expected=0 turns=1
  case "$scenario" in
    gemini-empty) expected=142 ;;
    gemini-idle) timeout=10; idle=1 ;;
    claude-success) vendor=claude ;;
    claude-error) vendor=claude; expected=142 ;;
  esac
  local home="$TEST_ROOT/$scenario" bin="$TEST_ROOT/$scenario/bin"
  local provider_home="$TEST_ROOT/$scenario/provider-home" job job_dir record report notice
  mkdir -p "$bin" "$provider_home/.gemini"
  cat > "$bin/provider" <<'PY'
#!/usr/bin/env python3
import json
import os
import signal
import sys

scenario = os.environ["FAKE_TIMEOUT_SCENARIO"]
vendor = os.environ["FAKE_TIMEOUT_VENDOR"]

def emit(event):
    print(json.dumps(event), flush=True)

def stop(*_):
    if vendor == "gemini":
        emit({"event": "result", "result": {
            "status": "ERROR", "error": "stream input cancelled: context canceled"}})
    sys.exit(0)

signal.signal(signal.SIGTERM, stop)
for turn, line in enumerate(sys.stdin, 1):
    with open(os.environ["FAKE_TIMEOUT_INPUT"], "a") as captured:
        captured.write(line)
    if scenario == "gemini-empty":
        emit({"event": "step_update", "step": {"text": "still working"}})
    elif scenario == "claude-error":
        emit({"type": "result", "is_error": True, "result": "failed turn"})
    elif vendor == "gemini":
        emit({"event": "result", "result": {
            "status": "SUCCESS", "response": f"turn-{turn}"}})
    else:
        emit({"type": "result", "is_error": False, "result": f"turn-{turn}"})
PY
  chmod +x "$bin/provider"
  printf 'triage: %s %s -\n' "$vendor" "$vendor-default" > "$home/routing.local.yaml"
  job="$(HOME="$provider_home" OMNILANE_HOME="$home" AGY_BIN="$bin/provider" \
    CLAUDE_BIN="$bin/provider" OMNILANE_JOB_SUPERVISED=1 \
    FAKE_TIMEOUT_SCENARIO="$scenario" FAKE_TIMEOUT_VENDOR="$vendor" \
    FAKE_TIMEOUT_INPUT="$home/input" "$ROOT/scripts/dispatch.sh" \
    --background --live --timeout "$timeout" --idle-timeout "$idle" \
    --mode sysops --workdir "$ROOT" --vendor "$vendor" triage first)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/exit" || fail "$scenario: no exit recorded"
  [[ "$(cat "$job_dir/exit")" == "$expected" ]] || fail "$scenario: expected exit $expected, got $(cat "$job_dir/exit")"
  record="$home/inbox/$job.json"
  wait_for_file "$record" || fail "$scenario: no completion record"
  report="$(printf '{}' | OMNILANE_HOME="$home" CLAUDE_PROJECT_DIR="$ROOT" bash "$ROOT/hooks/report-completions.sh")"
  [[ "$report" == *"exit=$expected"* ]] || fail "$scenario: missing completion report: $report"
  if [[ "$expected" -eq 0 ]]; then
    notice="closed by per-call timeout after ${timeout}s; last turn completed"
    [[ "$idle" -eq 0 ]] || notice="closed by idle cap after ${idle}s"
    [[ "$(head -1 "$job_dir/out.txt")" == "turn-$turns" ]] || fail "$scenario: last completed turn not preserved"
    [[ "$(tail -1 "$job_dir/out.txt")" == "$notice" ]] || fail "$scenario: missing final notice"
    grep -Fq "$notice" "$job_dir/mode-notice.txt" || fail "$scenario: mode notice missing"
    [[ "$report" != *FAILED* && "$report" != *CALL_TIMEOUT* && "$report" == *"$notice"* ]] || fail "$scenario: success misreported: $report"
  else
    [[ "$report" == *"completion: CALL_TIMEOUT "* && "$report" != *FAILED* ]] || fail "$scenario: expected CALL_TIMEOUT completion: $report"
    if [[ "$scenario" == "gemini-empty" || "$scenario" == "claude-error" ]]; then
      [[ ! -s "$job_dir/out.txt" ]] || fail "$scenario: fabricated success output"
    fi
  fi
}

case_call_timeout_warnings() {
  local home="$TEST_ROOT/timeout-warnings" out idle
  local idles=("$@")
  [[ ${#idles[@]} -gt 0 ]] || idles=(5 0)
  mkdir -p "$home"
  printf '#!/bin/sh\nexit 0\n' > "$home/agy"
  chmod +x "$home/agy"
  printf 'triage: gemini gemini-default -\n' > "$home/routing.local.yaml"
  for idle in "${idles[@]}"; do
    out="$(OMNILANE_HOME="$home" AGY_BIN="$home/agy" "$ROOT/scripts/dispatch.sh" --dry-run --background \
      --vendor gemini --timeout 5 --idle-timeout "$idle" triage first 2>&1 >/dev/null)" || fail "warning dispatch failed: $out"
    [[ "$out" == *"jobs close ID"* && "$out" == *"--single-shot"* ]] || fail "idle=$idle: missing timeout guidance: $out"
    if [[ "$idle" -gt 0 ]]; then
      [[ "$out" == *"idle cap cannot close the session before the per-call timeout"* ]] || fail "idle=$idle: missing relative timeout warning: $out"
    else
      [[ "$out" == *"only the per-call timeout or jobs close ends it"* ]] || fail "idle=0: missing unlimited idle warning: $out"
    fi
  done
  out="$(OMNILANE_HOME="$home" AGY_BIN="$home/agy" "$ROOT/scripts/dispatch.sh" --dry-run --background \
    --vendor gemini --timeout 10 --idle-timeout 5 triage first 2>&1 >/dev/null)" || fail "warning dispatch failed: $out"
  [[ "$out" == *"omnilane: --timeout is 10s, but the independent live mailbox idle cap remains 5s; adjust it with --idle-timeout SECONDS"* ]] || fail "existing timeout warning changed: $out"
  for idle in 5 0; do
    out="$(OMNILANE_HOME="$home" AGY_BIN="$home/agy" "$ROOT/scripts/dispatch.sh" --dry-run --background --live \
      --vendor gemini --timeout 5 --idle-timeout "$idle" triage first 2>&1 >/dev/null)" || fail "explicit live dispatch failed: $out"
    [[ "$out" != *"the foreman should jobs close ID"* ]] || fail "explicit --live idle=$idle: unexpected auto-live guidance: $out"
  done
  out="$(OMNILANE_HOME="$home" AGY_BIN="$home/agy" "$ROOT/scripts/dispatch.sh" --dry-run --background --live \
    --vendor gemini --timeout 10 --idle-timeout 5 triage first 2>&1 >/dev/null)" || fail "explicit live dispatch failed: $out"
  [[ "$out" == *"omnilane: --timeout is 10s, but the independent live mailbox idle cap remains 5s; adjust it with --idle-timeout SECONDS"* ]] || fail "explicit --live lost original warning: $out"
}

make_codex() {
  local path="$1"
  cat > "$path" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


app_args = sys.argv[1:]
# Like the real CLI, take global -c / --disable / --enable pairs before the subcommand.
while app_args[:1] in (["-c"], ["--disable"], ["--enable"]) and len(app_args) >= 2:
    app_args = app_args[2:]
if app_args[:1] == ["app-server"]:
    counter_path = pathlib.Path(os.environ["FAKE_CODEX_COUNTER"])
    try:
        invocation = int(counter_path.read_text(encoding="utf-8")) + 1
    except (FileNotFoundError, ValueError):
        invocation = 1
    counter_path.write_text(str(invocation), encoding="utf-8")
    input_path = pathlib.Path(os.environ["FAKE_CODEX_INPUT"])
    if invocation == 1:
        input_path = input_path.with_name(input_path.name + ".probe")

    thread_id = "thread-live-1"
    turn_id = "turn-live-1"
    for raw in sys.stdin:
        with input_path.open("a", encoding="utf-8") as captured:
            captured.write(raw)
        request = json.loads(raw)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            emit({"id": request_id, "result": {"userAgent": "fake", "codexHome": "/fake"}})
        elif method == "thread/start":
            emit({"id": request_id, "result": {"thread": {
                "id": thread_id,
                "sessionId": "session-live-1",
                "model": request["params"]["model"],
                "status": {"type": "idle"},
            }}})
        elif method == "turn/start":
            emit({"id": request_id, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
            prompt = request["params"]["input"][0]["text"]
            emit({"method": "item/completed", "params": {
                "item": {"type": "userMessage", "content": [{"type": "text", "text": prompt}]},
                "threadId": thread_id,
                "turnId": turn_id,
            }})
            emit({"method": "item/completed", "params": {
                "item": {"type": "agentMessage", "text": "initial agent text"},
                "threadId": thread_id,
                "turnId": turn_id,
            }})
        elif method == "turn/steer":
            emit({"id": request_id, "result": {"turnId": turn_id}})
            emit({"method": "item/completed", "params": {
                "item": {"type": "agentMessage", "text": os.environ["FAKE_CODEX_REPLY"]},
                "threadId": thread_id,
                "turnId": turn_id,
            }})
            emit({"method": "turn/completed", "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed", "error": None},
            }})
    raise SystemExit(0)

if len(sys.argv) >= 2 and sys.argv[1] == "exec":
    output_path = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])
    output_path.write_text("codex single-shot fallback\n", encoding="utf-8")
    print('{"type":"thread.started","thread_id":"fallback-thread"}')
    raise SystemExit(0)

raise SystemExit(2)
PY
  chmod +x "$path"
}

case_codex_live_rpc() {
  local home="$TEST_ROOT/codex-live" bin="$TEST_ROOT/codex-live/bin"
  local fake="$bin/codex" input="$home/codex.input" counter="$home/codex.counter"
  local reply job job_dir close_out
  mkdir -p "$home" "$bin"
  make_codex "$fake"
  printf 'triage: codex gpt-5.6-luna medium\n' > "$home/routing.local.yaml"
  printf -v reply 'path\to "x" 測試'

  job="$(OMNILANE_HOME="$home" CODEX_BIN="$fake" \
    FAKE_CODEX_INPUT="$input" FAKE_CODEX_COUNTER="$counter" FAKE_CODEX_REPLY="$reply" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
    --workdir "$ROOT" --vendor codex --model gpt-5.6-luna --effort medium \
    triage 'initial prompt echo marker')"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/inbox.ready" || fail "Codex live mailbox did not become ready"
  wait_for_lines "$job_dir/out.txt" 1 || fail "Codex agentMessage was not written incrementally"
  grep -Fxq 'initial agent text' "$job_dir/out.txt" || fail "Codex initial agentMessage missing"
  ! grep -Fq 'initial prompt echo marker' "$job_dir/out.txt" || fail "Codex userMessage echo leaked into output"

  OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$job" "$reply" >/dev/null
  wait_for_lines "$job_dir/out.txt" 2 || fail "Codex steered agentMessage did not reach output"
  grep -Fxq "$reply" "$job_dir/out.txt" || fail "Codex steered reply was not preserved verbatim"

  local rules
  rules="$(source "$ROOT/scripts/lib/common.sh"; printf '%s\n%s\n\n' "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES"; printf x)"
  INPUT="$input" ROOT="$ROOT" REPLY="$reply" RULES="${rules%x}" python3 - <<'PY' || fail "Codex JSON-RPC request sequence mismatch"
import json
import os
import pathlib

requests = [
    json.loads(line)
    for line in pathlib.Path(os.environ["INPUT"]).read_text(encoding="utf-8").splitlines()
]
assert [item["method"] for item in requests[:4]] == [
    "initialize", "thread/start", "turn/start", "turn/steer",
]
thread = requests[1]["params"]
assert thread["cwd"] == os.environ["ROOT"]
assert thread["model"] == "gpt-5.6-luna"
assert thread["sandbox"] == "read-only"
turn = requests[2]["params"]
assert turn["threadId"] == "thread-live-1"
assert turn["effort"] == "medium"
# The first message carries the same worker rules as single-shot mode; a later
# send is the operator's text only.
assert turn["input"] == [{"type": "text", "text": os.environ["RULES"] + "initial prompt echo marker"}], turn["input"]
steer = requests[3]["params"]
assert steer["threadId"] == "thread-live-1"
assert steer["expectedTurnId"] == "turn-live-1"
assert steer["input"] == [{"type": "text", "text": os.environ["REPLY"]}]
assert "[omnilane worker rules]" not in steer["input"][0]["text"]
PY
  grep -Fxq 'thread-live-1' "$job_dir/out.txt.session-id" || fail "Codex thread id was not recorded"

  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "Codex live close failed: $close_out"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Codex live job did not exit zero"
}

case_codex_live_large_task() {
  # A live task over the 102400-byte cap: only what is sent is truncated;
  # task.txt, the preserved record, keeps every byte. This checks the first
  # turn/start the fake Codex actually received, not task.txt.worker: run-codex.sh
  # rebuilds that file from the full task after job-worker.sh has read it.
  # The fixture is ASCII, so LC_ALL=C does not change the bytes sent. json_escape
  # hands a ~100 KB first message to Python, so it is not slow in any locale.
  local home="$TEST_ROOT/codex-live-large" bin="$TEST_ROOT/codex-live-large/bin"
  local fake="$bin/codex" input="$home/codex.input" task="$home/task.src" job job_dir close_out tries=0
  mkdir -p "$home" "$bin"
  make_codex "$fake"
  printf 'triage: codex gpt-5.6-luna medium\n' > "$home/routing.local.yaml"
  python3 - "$task" <<'PY'
import pathlib
import sys
body = "".join(f"{i:06d} large live task line\n" for i in range(4400))
pathlib.Path(sys.argv[1]).write_bytes(("HEAD-MARKER\n" + body + "TAIL-MARKER\n").encode())
PY
  [[ "$(wc -c < "$task")" -gt 102400 && "$(wc -c < "$task")" -lt 140000 ]] ||
    fail "fixture task must sit between the live cap and the runner cap"

  job="$(LC_ALL=C OMNILANE_HOME="$home" CODEX_BIN="$fake" \
    FAKE_CODEX_INPUT="$input" FAKE_CODEX_COUNTER="$home/codex.counter" FAKE_CODEX_REPLY="done" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
    --workdir "$ROOT" --vendor codex --model gpt-5.6-luna --effort medium \
    triage - < "$task")"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  if [[ -n "${LIVE_LARGE_HOME_FILE:-}" ]]; then printf '%s\n' "$home" > "$LIVE_LARGE_HOME_FILE"; fi
  while ! grep -Fq '"method":"turn/start"' "$input" 2>/dev/null && [[ "$tries" -lt 1800 ]]; do
    sleep 0.1; tries=$((tries + 1))
  done
  grep -Fq '"method":"turn/start"' "$input" 2>/dev/null || fail "fake Codex did not receive the first turn"
  wait_for_lines "$job_dir/out.txt" 1 || fail "Codex agentMessage was not written"
  # The fake completes the turn only after a steer; finish it so close exits 0.
  local send_job="$job"
  # codex-live-large-task-cleanup makes this step fail on purpose.
  if [[ "${LIVE_LARGE_FAIL_SEND:-0}" == 1 ]]; then send_job="no-such-job"; fi
  OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$send_job" "done" >/dev/null
  wait_for_lines "$job_dir/out.txt" 2 || fail "Codex steered reply did not complete the turn"
  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "Codex live close failed: $close_out"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Codex large live job did not exit zero"
  local copy
  for copy in "$job_dir/task.txt.worker" "$job_dir/task.txt.runner" \
    "$job_dir/task.txt.runner.worker" "$job_dir"/task.txt.copies.*; do
    [[ ! -e "$copy" && ! -L "$copy" ]] || fail "Codex live close left prompt copy: $copy"
  done

  cmp -s "$task" "$job_dir/task.txt" ||
    fail "task.txt was rewritten by live truncation ($(wc -c < "$job_dir/task.txt") of $(wc -c < "$task") bytes left)"
  TASK="$task" INPUT="$input" python3 - <<'PY' || fail "Codex large live first message mismatch"
import json
import os
import pathlib

marker = "[omnilane worker rules]"
requests = [json.loads(l) for l in pathlib.Path(os.environ["INPUT"]).read_text(encoding="utf-8").splitlines()]
turns = [r for r in requests if r.get("method") == "turn/start"]
assert len(turns) == 1, len(turns)
sent = turns[0]["params"]["input"][0]["text"]
assert sent.startswith(marker), sent[:80]
assert sent.count(marker) == 1, sent.count(marker)
assert "--- TRUNCATED (original: " in sent, "first message is not truncated"
assert "HEAD-MARKER" in sent and "TAIL-MARKER" in sent
assert len(sent.encode()) <= 102400 + 200, len(sent.encode())
assert len(sent.encode()) < pathlib.Path(os.environ["TASK"]).stat().st_size
PY
}

case_codex_live_large_task_cleanup() {
  # The large task's job has no idle timeout, so a step that fails must not leave it running.
  local marker="$TEST_ROOT/large-task-home" out="$TEST_ROOT/large-task.out" rc=0 home left
  LIVE_LARGE_FAIL_SEND=1 LIVE_LARGE_HOME_FILE="$marker" \
    bash "$0" --omnilane-offline-child codex-live-large-task >"$out" 2>&1 || rc=$?
  [[ "$rc" -ne 0 ]] || fail "the large live task passed although send failed"
  grep -Fq 'invalid job id' "$out" || fail "the large live task did not fail at the injected send: $(tail -n 5 "$out")"
  home="$(cat "$marker" 2>/dev/null)" || fail "the large live task did not record its job"
  processes_under "$home/" || fail "could not list processes after the large live task"
  left="$LIVE_PIDS"
  if [[ -n "$left" ]]; then
    # Signal only the PIDs that still name this task's private home right now.
    if processes_under "$home/" && [[ -n "$LIVE_PIDS" ]]; then
      # shellcheck disable=SC2086
      kill -KILL $LIVE_PIDS 2>/dev/null || true
    fi
    fail "a large live task that failed at send left processes: $left"
  fi
}

case_codex_live_fallback() {
  local home="$TEST_ROOT/codex-fallback" bin="$TEST_ROOT/codex-fallback/bin"
  local fake="$bin/codex" job job_dir doctor_out
  mkdir -p "$home" "$bin"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
while [[ "${1:-}" == "-c" || "${1:-}" == "--disable" || "${1:-}" == "--enable" ]]; do shift 2; done
if [[ "${1:-}" == "app-server" ]]; then
  IFS= read -r _ || true
  printf '{"id":1,"error":{"message":"app-server unavailable"}}\n'
  exit 1
fi
output=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then output="$2"; shift 2; else shift; fi
done
printf 'codex single-shot fallback\n' > "$output"
printf '{"type":"thread.started","thread_id":"fallback-thread"}\n'
EOF
  chmod +x "$fake"
  printf 'triage: codex gpt-5.6-luna medium\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" CODEX_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
    --workdir "$ROOT" --vendor codex triage fallback)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/exit" || fail "Codex live fallback job did not finish"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Codex live fallback failed"
  grep -Fxq 'codex single-shot fallback' "$job_dir/out.txt" || fail "Codex fallback did not run single-shot"
  grep -Fq 'live surface unavailable' "$job_dir/mode-notice.txt" || fail "Codex fallback notice missing"

  doctor_out="$(OMNILANE_HOME="$home" CODEX_BIN="$fake" "$ROOT/scripts/doctor.sh" 2>&1 || true)"
  [[ "$doctor_out" == *"codex-live"* && "$doctor_out" == *"upgrade codex"* ]] ||
    fail "doctor did not explain failed Codex live handshake: $doctor_out"
  [[ "$doctor_out" == *"live-unavailable"*"codex"* ]] ||
    fail "doctor did not classify Codex live surface unavailable: $doctor_out"
}

case_single_shot_claude() {
  local home="$TEST_ROOT/single-shot" bin="$TEST_ROOT/single-shot/bin"
  local fake="$bin/claude" args="$home/claude.args" input="$home/claude.input"
  local job job_dir
  mkdir -p "$home" "$bin"
  make_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"
  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" FAKE_CLAUDE_ARGS="$args" \
    FAKE_CLAUDE_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background \
      --single-shot --vendor claude triage x)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/exit" || fail "single-shot Claude job did not finish"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "single-shot Claude job failed"
  grep -Fq '"session_mode":"single-shot"' "$job_dir/meta.json" ||
    fail "single-shot mode missing from metadata"
  grep -Fxq 'claude single-shot ok' "$job_dir/out.txt" ||
    fail "Claude did not run through one-shot path"
  [[ ! -e "$job_dir/inbox.fifo" && ! -e "$job_dir/events.jsonl" ]] ||
    fail "single-shot Claude created live mailbox artifacts"
  ! grep -q -- '--input-format stream-json' "$args" ||
    fail "single-shot Claude received stream arguments"
}

case_idle_cap() {
  local home="$TEST_ROOT/idle" bin="$TEST_ROOT/idle/bin"
  local fake="$bin/claude" args="$home/claude.args" input="$home/claude.input"
  local job job_dir record
  mkdir -p "$home" "$bin"
  make_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"
  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" FAKE_CLAUDE_ARGS="$args" \
    FAKE_CLAUDE_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background --live \
      --idle-timeout 1 --vendor claude triage x)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/exit" || fail "idle-capped Claude job did not close"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "idle-capped Claude job failed"
  grep -Fq '"session_mode":"live"' "$job_dir/meta.json" ||
    fail "live mode missing from metadata"
  grep -Fq '"idle_timeout":1' "$job_dir/meta.json" ||
    fail "idle timeout missing from metadata"
  record="$home/inbox/$job.json"
  wait_for_file "$record" || fail "idle-capped job lacks completion record"
  grep -Fq 'closed by idle cap after 1s' "$record" ||
    fail "idle close note missing from completion tail"
  [[ ! -e "$job_dir/inbox.ready" && ! -e "$job_dir/inbox.fifo" ]] ||
    fail "idle-capped mailbox was not cleaned up"
}

case_claude_close_recovers_result_output() {
  local home="$TEST_ROOT/claude-close-recovery" bin="$TEST_ROOT/claude-close-recovery/bin"
  local fake="$bin/claude" job job_dir close_out
  mkdir -p "$home" "$bin"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
trap '' TERM
printf '%s\n' "$$" > "$FAKE_CLAUDE_PIDS"
while IFS= read -r _; do
  printf '%s\n' '{"type":"system","model":"claude-opus-5"}'
  printf '%s\n' '{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"text","text":"claude recovered close output"}]}}'
  printf '%s\n' '{"type":"result","is_error":false,"result":"claude recovered close output"}'
done
sleep 60 &
child=$!
printf '%s\n' "$child" >> "$FAKE_CLAUDE_PIDS"
wait "$child"
EOF
  chmod +x "$fake"
  printf 'triage: claude claude-opus-5 high\n' > "$home/routing.local.yaml"
  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" FAKE_CLAUDE_PIDS="$home/claude.pids" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
    --mode work --workdir "$ROOT" --vendor claude triage 'recover completed output')"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/inbox.ready" || fail "stubborn Claude mailbox did not become ready"
  wait_for_lines "$job_dir/events.jsonl" 3 || fail "stubborn Claude did not emit a successful result"
  close_out="$(OMNILANE_HOME="$home" python3 - "$ROOT/scripts/jobs.sh" "$job" "$home/claude.pids" <<'PY'
import pathlib
import subprocess
import sys
import time

started = time.monotonic()
closed = subprocess.run(['bash', sys.argv[1], 'close', sys.argv[2]],
                        text=True, capture_output=True, timeout=12)
elapsed = time.monotonic() - started
assert closed.returncode == 0, (closed.returncode, closed.stdout, closed.stderr)
assert elapsed < 11.0, ('close exceeded its documented bound', elapsed)
pids = pathlib.Path(sys.argv[3]).read_text().splitlines()
assert len(pids) == 2, ('fake Claude and its child were not both recorded', pids)
for pid in pids:
    state = subprocess.run(['ps', '-o', 'stat=', '-p', pid], text=True,
                           capture_output=True, timeout=5).stdout.strip()
    assert not state or state.startswith('Z'), ('close returned with a live descendant', pid, state)
print(closed.stdout, end='')
PY
)" ||
    fail "stubborn Claude close failed: $close_out"
  wait_for_file "$job_dir/exit" || fail "stubborn Claude close did not record exit"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "stubborn Claude close was not successful"
  grep -Fxq 'claude recovered close output' "$job_dir/out.txt" ||
    fail "successful Claude result event was not recovered into out.txt"
}

case_gemini_schema() {
  local home="$TEST_ROOT/gemini" bin="$TEST_ROOT/gemini/bin"
  local fake="$bin/agy" args="$home/agy.args" input="$home/agy.input"
  local provider_home="$home/provider-home"
  local job job_dir second close_out
  # The real runner resolves GeminiDir before starting agy. Keep the fake CLI
  # independent of an operator's installed/authenticated ~/.gemini directory.
  mkdir -p "$home" "$bin" "$provider_home/.gemini"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
: > "${FAKE_AGY_ARGS:?}"
for arg in "$@"; do printf '[%s]\n' "$arg" >> "$FAKE_AGY_ARGS"; done
printf '{"event":"init","conversation_id":"fake-conversation"}\n'
while IFS= read -r line; do
  printf '%s\n' "$line" >> "${FAKE_AGY_INPUT:?}"
  printf '{"event":"result","result":{"status":"SUCCESS","response":"agy live ok","conversation_id":"fake-conversation"}}\n'
done
EOF
  chmod +x "$fake"
  printf 'triage: gemini "Gemini Fake" -\n' > "$home/routing.local.yaml"
  job="$(HOME="$provider_home" OMNILANE_HOME="$home" AGY_BIN="$fake" FAKE_AGY_ARGS="$args" \
    FAKE_AGY_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background --live \
      --idle-timeout 0 --mode sysops --vendor gemini triage first)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/inbox.ready" || fail "Gemini live mailbox did not become ready"
  wait_for_lines "$input" 1 || fail "Gemini did not receive initial turn"
  OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$job" second >/dev/null
  wait_for_lines "$input" 2 || fail "Gemini did not receive follow-up turn"
  second="$(sed -n '2p' "$input")"
  SECOND="$second" python3 - <<'PY' || fail "Gemini follow-up schema mismatch: $second"
import json
import os
message = json.loads(os.environ["SECOND"])
assert message == {
    "event": "user",
    "message": {
        "role": "user",
        "content": [{"type": "text", "text": "second"}],
    },
}
PY
  awk 'previous == "[-p]" && $0 == "[]" { found=1 } { previous=$0 } END { exit !found }' \
    "$args" || fail "agy live invocation did not pass -p with an empty argument"
  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "Gemini live close failed: $close_out"
  [[ "$close_out" == *"result_events=2"* ]] ||
    fail "Gemini result events were not counted: $close_out"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Gemini live job failed"
  grep -Fq '"session_mode":"live"' "$job_dir/meta.json" ||
    fail "Gemini live mode missing from metadata"
}

case_json_escape_round_trip() {
  local home="$TEST_ROOT/json-escape" bin="$TEST_ROOT/json-escape/bin"
  local fake="$bin/claude" args="$home/claude.args" input="$home/claude.input"
  local expected="$home/expected" decoded="$home/decoded"
  local job job_dir close_out prompt

  mkdir -p "$home" "$bin"
  make_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"
  printf -v prompt 'lone \\ quote " tab\t control \001 multibyte 測試—…→'
  # The first live message carries the worker rules ahead of the task.
  (source "$ROOT/scripts/lib/common.sh"
   printf '%s\n%s\n\n%s' "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES" "$prompt") > "$expected"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" FAKE_CLAUDE_ARGS="$args" \
    FAKE_CLAUDE_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background --live \
    --idle-timeout 0 --vendor claude triage "$prompt")"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/inbox.ready" || fail "JSON escape live mailbox did not become ready"
  wait_for_lines "$input" 1 || fail "JSON escape prompt did not reach Claude input"
  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "JSON escape live close failed: $close_out"
  wait_for_file "$job_dir/exit" || fail "JSON escape live job did not finish"

  perl -MJSON::PP -e '
    local $/;
    my $event = decode_json(<STDIN>);
    my $text = $event->{message}{content}[0]{text};
    utf8::encode($text);
    print $text;
  ' < "$input" > "$decoded" || fail "live prompt was not valid JSON"
  cmp -s "$expected" "$decoded" || fail "live prompt did not round-trip exactly"
}

case_jobs_send_json_escape_round_trip() {
  local home="$TEST_ROOT/jobs-send-json-escape" bin="$TEST_ROOT/jobs-send-json-escape/bin"
  local fake="$bin/claude" args="$home/claude.args" input="$home/claude.input"
  local expected="$home/expected" job job_dir close_out follow_up

  mkdir -p "$home" "$bin"
  make_claude "$fake"
  printf 'triage: claude claude-default high\n' > "$home/routing.local.yaml"
  printf -v follow_up 'lone \\ quote " multibyte 測試—…→'
  printf '%s' "$follow_up" > "$expected"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" FAKE_CLAUDE_ARGS="$args" \
    FAKE_CLAUDE_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background --live \
    --idle-timeout 0 --vendor claude triage initial)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/inbox.ready" || fail "jobs.sh send JSON escape mailbox did not become ready"
  wait_for_lines "$input" 1 || fail "jobs.sh send JSON escape initial prompt did not arrive"

  OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$job" "$follow_up" >/dev/null
  wait_for_lines "$input" 2 || fail "jobs.sh send JSON escape follow-up did not arrive"

  INPUT="$input" EXPECTED="$expected" perl -MJSON::PP -e '
    open my $input, "<", $ENV{INPUT} or die $!;
    scalar <$input>;
    my $line = <$input>;
    my $event = decode_json($line);
    my $text = $event->{message}{content}[0]{text};
    utf8::encode($text);
    open my $expected_fh, "<", $ENV{EXPECTED} or die $!;
    local $/;
    my $expected = <$expected_fh>;
    die "jobs.sh send text mismatch\n" unless $text eq $expected;
  ' || fail "jobs.sh send line did not decode to exact input"

  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "jobs.sh send JSON escape close failed: $close_out"
  wait_for_file "$job_dir/exit" || fail "jobs.sh send JSON escape job did not finish"
}

case_codex_killed_runner_reaps_app_server() {
 local case_dir="$TEST_ROOT/codex-killed-runner" fake="$TEST_ROOT/codex-killed-runner/codex"
 mkdir -p "$case_dir"
 : > "$case_dir/inbox"
 cat > "$fake" <<'EOF'
#!/usr/bin/env bash
trap '' TERM
sleep 30 &
printf '%s %s\n' "$$" "$!" > "${FAKE_CODEX_PID_FILE:?}"
wait
EOF
 chmod +x "$fake"

 if ! ROOT="$ROOT" CASE_DIR="$case_dir" FAKE_CODEX="$fake" \
  FAKE_CODEX_PID_FILE="$case_dir/codex-pids" python3 - <<'PY'
import os
import pathlib
import signal
import subprocess
import time

root = pathlib.Path(os.environ["ROOT"])
case_dir = pathlib.Path(os.environ["CASE_DIR"])
pidfile = pathlib.Path(os.environ["FAKE_CODEX_PID_FILE"])
command = [
    str(root / "scripts/runners/run-codex-live.py"),
    "--codex-bin", os.environ["FAKE_CODEX"],
    "--cwd", str(case_dir),
    "--model", "fake-model",
    "--effort", "low",
    "--sandbox", "read-only",
    "--inbox", str(case_dir / "inbox"),
    "--events", str(case_dir / "events.jsonl"),
    "--output", str(case_dir / "out.txt"),
    "--progress", str(case_dir / "progress.json"),
    "--session-id-file", str(case_dir / "session-id"),
    "--rpc-timeout", "30",
]
stderr = (case_dir / "stderr").open("w", encoding="utf-8")
runner = subprocess.Popen(
    command,
    stdout=subprocess.DEVNULL,
    stderr=stderr,
    start_new_session=True,
)
server_pids: list[int] = []
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.05)
    if not pidfile.exists():
        raise AssertionError("fake app-server did not start")
    server_pids = [int(value) for value in pidfile.read_text().split()]

    # Model the outer watchdog's uncatchable escalation. The app-server and
    # its child must remain in this group even though the runner's finally
    # block cannot execute after SIGKILL.
    os.killpg(runner.pid, signal.SIGKILL)
    runner.wait(timeout=2)

    deadline = time.monotonic() + 2
    survivors = server_pids
    while survivors and time.monotonic() < deadline:
        current = []
        for pid in survivors:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            current.append(pid)
        survivors = current
        if survivors:
            time.sleep(0.05)
    if survivors:
        raise AssertionError(f"surviving app-server pids: {survivors}")
finally:
    stderr.close()
    if runner.poll() is None:
        try:
            os.killpg(runner.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        runner.wait(timeout=2)
    for pid in server_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
PY
 then
  fail "killed Codex live runner left its app-server process group alive"
 fi
}

case_codex_app_server_eof_cleanup() {
  local case_dir="$TEST_ROOT/codex-app-server-close" fake="$TEST_ROOT/codex-app-server-close/codex"
  mkdir -p "$case_dir"
  cat > "$fake" <<'PY'
#!/usr/bin/env python3
import os
import pathlib
import signal
import subprocess
import sys
import time

args = sys.argv[1:]
while args[:1] in (["-c"], ["--disable"], ["--enable"]) and len(args) >= 2:
    args = args[2:]
if args != ["app-server"]:
    raise SystemExit(2)

prefix = pathlib.Path(os.environ["FAKE_CLOSE_PREFIX"])
mode = os.environ["FAKE_CLOSE_MODE"]
sidecar = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
prefix.with_suffix(".sidecar-pid").write_text(str(sidecar.pid), encoding="utf-8")

def on_term(_signum, _frame):
    prefix.with_suffix(".term").write_text("TERM\n", encoding="utf-8")
    if mode == "ignore-eof-term":
        return
    raise SystemExit(143)

signal.signal(signal.SIGTERM, on_term)
sys.stdin.read()
if mode == "eof-clean":
    time.sleep(0.15)
    sidecar.terminate()
    sidecar.wait(timeout=2)
    prefix.with_suffix(".eof-clean").write_text("clean\n", encoding="utf-8")
    raise SystemExit(0)

while True:
    time.sleep(0.05)
PY
  chmod +x "$fake"

  ROOT="$ROOT" CASE_DIR="$case_dir" FAKE_CODEX="$fake" python3 - <<'PY' \
    || fail "Codex app-server EOF cleanup/escalation oracle failed"
import importlib.util
import os
import pathlib
import signal
import time
from types import SimpleNamespace

root = pathlib.Path(os.environ["ROOT"])
case_dir = pathlib.Path(os.environ["CASE_DIR"])
spec = importlib.util.spec_from_file_location(
    "omnilane_codex_live", root / "scripts/runners/run-codex-live.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False

def stop_sidecar(prefix):
    pid_path = prefix.with_suffix(".sidecar-pid")
    if not pid_path.exists():
        return
    pid = int(pid_path.read_text(encoding="utf-8"))
    if alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

def start(mode):
    prefix = case_dir / mode
    os.environ["FAKE_CLOSE_MODE"] = mode
    os.environ["FAKE_CLOSE_PREFIX"] = str(prefix)
    args = SimpleNamespace(
        codex_bin=os.environ["FAKE_CODEX"],
        cwd=str(case_dir),
        app_server_eof_grace=0.5,
        app_server_term_grace=0.2,
        app_server_kill_grace=0.2,
    )
    client = module.CodexLiveClient(args)
    client.start_server()
    deadline = time.monotonic() + 2
    pid_path = prefix.with_suffix(".sidecar-pid")
    while time.monotonic() < deadline and not pid_path.exists():
        time.sleep(0.02)
    assert pid_path.exists(), f"{mode}: sidecar did not start"
    return client, prefix, int(pid_path.read_text(encoding="utf-8"))

normal, normal_prefix, normal_sidecar = start("eof-clean")
try:
    normal.close()
    assert normal_prefix.with_suffix(".eof-clean").exists(), "normal EOF cleanup did not run"
    assert not normal_prefix.with_suffix(".term").exists(), "normal EOF path received TERM"
    assert not alive(normal_sidecar), f"normal EOF left sidecar {normal_sidecar}"
    assert normal.process is not None and normal.process.returncode == 0, normal.process
finally:
    stop_sidecar(normal_prefix)

stuck, stuck_prefix, _ = start("ignore-eof-term")
started = time.monotonic()
try:
    stuck.close()
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"stuck close was not bounded: {elapsed:.3f}s"
    assert stuck_prefix.with_suffix(".term").exists(), "stuck process never received TERM"
    assert stuck.process is not None and stuck.process.returncode == -signal.SIGKILL, stuck.process
finally:
    stop_sidecar(stuck_prefix)
PY
}

case_codex_close_grace_precedes_worker_escalation() {
 if ! ROOT="$ROOT" python3 - <<'PY'
import ast
import os
import pathlib
import re

root = pathlib.Path(os.environ["ROOT"])
runner_path = root / "scripts/runners/run-codex-live.py"
worker_path = root / "scripts/lib/job-worker.sh"

tree = ast.parse(runner_path.read_text(encoding="utf-8"))
close_grace = None
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
        continue
    if not isinstance(node.args[0], ast.Constant) or node.args[0].value != "--close-grace":
        continue
    for keyword in node.keywords:
        if keyword.arg == "default":
            close_grace = float(ast.literal_eval(keyword.value))

worker = worker_path.read_text(encoding="utf-8")
grace = re.search(r'^CLOSE_RUNNER_GRACE=([0-9.]+)$', worker, re.MULTILINE)
if close_grace is None or grace is None:
    raise AssertionError("could not resolve close timing constants")
outer_grace = float(grace.group(1))

# close() itself may spend one more second waiting after TERM. Keep a real
# margin instead of merely making the two nominal deadlines equal.
if not close_grace + 1.0 < outer_grace:
    raise AssertionError(
        f"inner={close_grace}s plus cleanup is not below outer={outer_grace}s"
    )
PY
 then
  fail "Codex close grace does not expire before worker escalation"
 fi
}

case_codex_full_close_budget_precedes_worker_escalation() {
  if ! ROOT="$ROOT" python3 - <<'PY'
import ast
import os
import pathlib
import re

root = pathlib.Path(os.environ["ROOT"])
runner_path = root / "scripts/runners/run-codex-live.py"
worker_path = root / "scripts/lib/job-worker.sh"
tree = ast.parse(runner_path.read_text(encoding="utf-8"))
wanted = {
    "--close-grace",
    "--app-server-eof-grace",
    "--app-server-term-grace",
    "--app-server-kill-grace",
}
defaults = {}
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
        continue
    if not isinstance(node.args[0], ast.Constant) or node.args[0].value not in wanted:
        continue
    for keyword in node.keywords:
        if keyword.arg == "default":
            defaults[node.args[0].value] = float(ast.literal_eval(keyword.value))

worker = worker_path.read_text(encoding="utf-8")
grace = re.search(r'^CLOSE_RUNNER_GRACE=([0-9.]+)$', worker, re.MULTILINE)
if set(defaults) != wanted or grace is None:
    raise AssertionError(f"could not resolve close budget: {defaults}")
inner_budget = sum(defaults.values())
outer_budget = float(grace.group(1))
if not inner_budget + 0.25 < outer_budget:
    raise AssertionError(
        f"turn+EOF+TERM+KILL={inner_budget}s lacks margin below worker={outer_budget}s"
    )
timeouts = {name: float(re.search(r'^' + name + r'=([0-9.]+)$', worker, re.MULTILINE).group(1))
            for name in ('CLOSE_DRAIN_TIMEOUT', 'CLOSE_TERM_GRACE', 'CLOSE_KILL_GRACE')}
supervisor = (root / 'scripts/lib/job-timeout.pl').read_text(encoding='utf-8')
supervisor_grace = float(re.search(r'my \$TERM_GRACE_SECONDS = ([0-9.]+);', supervisor).group(1))
jobs = (root / 'scripts/jobs.sh').read_text(encoding='utf-8')
# jobs close waits on a sub-second Perl clock; the argument after the exit path is the wait in seconds.
close_waits = re.findall(r"""^\s*' "\$JOB_DIR/exit" ([0-9]+(?:\.[0-9]+)?) "\$holder_path" "\$late_signal" "\$PS_QUERY_LIMIT"\)""",
                         jobs, re.MULTILINE)
if len(close_waits) != 1:
    raise AssertionError(f"could not resolve the one jobs close wait: {close_waits}")
close_wait = float(close_waits[0])
if close_wait > 11.0:
    raise AssertionError("jobs close must keep its documented 11-second upper bound")
if not 1.05 + sum(timeouts.values()) + outer_budget + supervisor_grace < close_wait - 1:
    raise AssertionError("worker close plus supervisor cleanup must precede jobs close's quantized deadline")
cleanup_limit = float(re.search(r'^PROMPT_CLEANUP_LIMIT=([0-9.]+)$', worker, re.MULTILINE).group(1))
# 0.06 is the 0.02s clock shutdown of each of the three close phases.
if not 1.05 + sum(timeouts.values()) + outer_budget + supervisor_grace + 0.06 + cleanup_limit < close_wait:
    raise AssertionError("prompt copy cleanup must fit between the worker close budget and jobs close")
PY
  then
    fail "Codex full close budget does not expire before worker escalation"
  fi
}

case_codex_shutdown_grace() {
  local fixture="$TEST_ROOT/codex-shutdown-fixture"
  local runner="$fixture/scripts/runners/run-codex.sh"
  local probe="$fixture/codex"
  mkdir -p "$fixture/scripts/lib" "$fixture/scripts/runners"
  ln -s "$ROOT/scripts/lib/job-worker.sh" "$fixture/scripts/lib/job-worker.sh"
  ln -s "$ROOT/scripts/lib/common.sh" "$fixture/scripts/lib/common.sh"
  ln -s "$ROOT/scripts/lib/live-protocol.sh" "$fixture/scripts/lib/live-protocol.sh"
  # job-worker runs the AA policy gate from OMNILANE_REPO before starting the runner.
  ln -s "$ROOT/scripts/lib/aa_policy.py" "$fixture/scripts/lib/aa_policy.py"
  ln -s "$ROOT/config" "$fixture/config"
  make_codex "$probe"
  cat > "$runner" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$$" > "${FAKE_RUNNER_PID_FILE:?}"
trap 'printf "TERM\n" > "${FAKE_TERM_FILE:?}"; exit 143' TERM
case "${FAKE_RUNNER_MODE:?}" in
  already-gone)
    IFS= read -r _ < "${OMNILANE_INBOX:?}" || true
    ;;
  grace-exit)
    while IFS= read -r _; do :; done < "${OMNILANE_INBOX:?}"
    /bin/sleep 0.2
    ;;
  require-term)
    while IFS= read -r _; do :; done < "${OMNILANE_INBOX:?}"
    # Block in wait: a trapped TERM interrupts it and the trap runs at once. Behind a
    # foreground sleep Bash defers the trap, which a slow host stretches past the 0.1s TERM grace.
    while :; do /bin/sleep 0.05 & wait "$!" || true; done
    ;;
  *)
    exit 2
    ;;
esac
EOF
  chmod +x "$runner"

  local mode case_dir worker_pid runner_pid holder_pid tries worker_rc
  for mode in already-gone grace-exit require-term; do
    case_dir="$TEST_ROOT/codex-shutdown-$mode"
    mkdir -p "$case_dir/job" "$case_dir/workdir"
    printf 'shutdown test\n' > "$case_dir/prompt.txt"
    OMNILANE_HOME="$case_dir/home" OMNILANE_REPO="$fixture" CODEX_BIN="$probe" \
      OMNILANE_SESSION_MODE=live OMNILANE_LIVE_REQUIRED=1 \
      OMNILANE_IDLE_TIMEOUT=0 FAKE_RUNNER_MODE="$mode" \
      FAKE_CODEX_COUNTER="$case_dir/probe.counter" FAKE_CODEX_INPUT="$case_dir/probe.input" \
      FAKE_RUNNER_PID_FILE="$case_dir/runner.pid" FAKE_TERM_FILE="$case_dir/term" \
      /bin/bash "$fixture/scripts/lib/job-worker.sh" codex advise "$case_dir/workdir" fake medium \
      "$case_dir/prompt.txt" "$case_dir/job/out.txt" \
      > "$case_dir/stdout" 2> "$case_dir/stderr" &
    worker_pid=$!

    wait_for_file "$case_dir/runner.pid" \
      || fail "Codex $mode runner did not start: $(cat "$case_dir/stderr")"
    if [[ "$mode" != "already-gone" ]]; then
      wait_for_file "$case_dir/job/inbox.ready" || fail "Codex $mode mailbox did not become ready"
      holder_pid="$(cat "$case_dir/job/inbox.holder.pid")"
      kill -USR1 "$holder_pid"
    fi

    tries=0
    while kill -0 "$worker_pid" 2>/dev/null && [[ "$tries" -lt 120 ]]; do
      sleep 0.1
      tries=$((tries + 1))
    done
    if kill -0 "$worker_pid" 2>/dev/null; then
      runner_pid="$(cat "$case_dir/runner.pid")"
      kill -TERM "$worker_pid" "$runner_pid" 2>/dev/null || true
      wait "$worker_pid" 2>/dev/null || true
      fail "Codex $mode shutdown hung"
    fi
    set +e
    wait "$worker_pid"
    worker_rc=$?
    set -e

    if [[ "$mode" == "already-gone" ]]; then
      [[ "$worker_rc" -eq 0 ]] || fail "already-gone Codex runner returned $worker_rc"
      [[ ! -e "$case_dir/term" ]] || fail "already-gone Codex runner was TERMed"
    elif [[ "$mode" == "grace-exit" ]]; then
      [[ ! -e "$case_dir/term" ]] || fail "Codex runner exiting in grace window was TERMed"
    else
      [[ -e "$case_dir/term" ]] || fail "stuck Codex runner was not TERMed after grace window"
    fi
    ! grep -Fq 'invalid timeout specification' "$case_dir/stderr" \
      || fail "Codex $mode close used a Bash 3.2-incompatible FIFO timeout"
  done
}

case_grok_live_acp() {
  local home="$TEST_ROOT/grok-live" bin="$TEST_ROOT/grok-live/bin"
  local fake="$bin/grok" input="$home/grok.input" counter="$home/grok.counter"
  local job job_dir close_out send_out tries reply
  mkdir -p "$home" "$bin"

  cat > "$fake" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


if sys.argv[1:] not in (
    ["agent", "stdio"],
    ["agent", "--model", "grok-4.6", "stdio"],
    ["--no-memory", "--no-subagents", "--no-plan", "--verbatim", "--sandbox", "off", "agent", "--always-approve", "stdio"],
    ["--no-memory", "--no-subagents", "--no-plan", "--verbatim", "--sandbox", "off", "agent", "--always-approve", "--model", "grok-4.6", "stdio"],
):
    print("grok single-shot")
    raise SystemExit(0)

counter_path = pathlib.Path(os.environ["FAKE_GROK_COUNTER"])
try:
    invocation = int(counter_path.read_text(encoding="utf-8")) + 1
except (FileNotFoundError, ValueError):
    invocation = 1
counter_path.write_text(str(invocation), encoding="utf-8")
input_path = pathlib.Path(os.environ["FAKE_GROK_INPUT"])
session_id = "grok-session-live-1"
prompt_count = 0

for raw in sys.stdin:
    if invocation > 1:
        with input_path.open("a", encoding="utf-8") as captured:
            captured.write(raw)
    request = json.loads(raw)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        emit({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "sessionCapabilities": {"list": {}, "resume": {}, "close": {}}
                },
            },
        })
    elif method == "session/new":
        emit({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"sessionId": session_id, "models": {}},
        })
    elif method == "session/prompt":
        prompt_count += 1
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        if prompt_count == 1:
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "PRIVATE THOUGHT"},
                    },
                },
            })
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "initial visible "},
                    },
                },
            })
        else:
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": os.environ["FAKE_GROK_REPLY"]},
                    },
                },
            })
            emit({
                "jsonrpc": "2.0",
                "method": "_x.ai/session/prompt_complete",
                "params": {"sessionId": session_id, "stopReason": "end_turn"},
            })
    elif method == "session/cancel":
        emit({
            "jsonrpc": "2.0",
            "method": "_x.ai/session/prompt_complete",
            "params": {"sessionId": session_id, "stopReason": "cancelled"},
        })
    elif method == "session/close":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        raise SystemExit(0)
PY
  chmod +x "$fake"
  printf 'live-search: grok grok-4.6 high\n' > "$home/routing.local.yaml"
  printf -v reply 'path\\to "x" 測試'

  local advise_dry advise_live_out advise_live_rc=0
  advise_dry="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --dry-run --background \
      --workdir "$ROOT" --vendor grok --model grok-4.6 \
      live-search 'read-only Grok advice')"
  [[ "$advise_dry" == *"session_mode=single-shot"* ]] ||
    fail "Grok advise did not stay single-shot: $advise_dry"

  set +e
  advise_live_out="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --dry-run --background --live \
      --workdir "$ROOT" --vendor grok --model grok-4.6 \
      live-search 'read-only Grok advice' 2>&1)"
  advise_live_rc=$?
  set -e
  [[ "$advise_live_rc" -eq 2 ]] ||
    fail "explicit Grok advise --live should fail with exit 2: $advise_live_out"
  [[ "$advise_live_out" == *"only explicit --mode sysops"* ]] ||
    fail "explicit Grok advise --live lacked sysops-only guidance: $advise_live_out"

  job="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    FAKE_GROK_INPUT="$input" FAKE_GROK_COUNTER="$counter" \
    FAKE_GROK_REPLY="$reply" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
      --mode sysops --workdir "$ROOT" --vendor grok --model grok-4.6 \
      --effort - live-search 'initial Grok prompt')"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/inbox.ready" || fail "Grok live mailbox did not become ready"

  tries=0
  until grep -Fq 'initial visible ' "$job_dir/out.txt" 2>/dev/null; do
    [[ ! -e "$job_dir/exit" ]] || fail "Grok live job exited before incremental output"
    [[ "$tries" -lt 100 ]] || fail "Grok agent_message_chunk was not written incrementally"
    sleep 0.1
    tries=$((tries + 1))
  done
  ! grep -Fq 'PRIVATE THOUGHT' "$job_dir/out.txt" || \
    fail "Grok agent_thought_chunk leaked into output"

  send_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$job" "$reply")" ||
    fail "Grok live send failed: $send_out"
  [[ "$send_out" == *"cancels an active turn"*"in-progress work is lost"* ]] ||
    fail "jobs.sh send did not surface Grok cancellation semantics: $send_out"
  tries=0
  until grep -Fq "$reply" "$job_dir/out.txt" 2>/dev/null; do
    [[ ! -e "$job_dir/exit" ]] || fail "Grok live job exited before steered reply"
    [[ "$tries" -lt 100 ]] || fail "Grok steered reply did not reach output"
    sleep 0.1
    tries=$((tries + 1))
  done

  local rules
  rules="$(source "$ROOT/scripts/lib/common.sh"; printf '%s\n%s\n\n' "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES"; printf x)"
  INPUT="$input" ROOT="$ROOT" REPLY="$reply" RULES="${rules%x}" python3 - <<'PY'
import json
import os
import pathlib

requests = [
    json.loads(line)
    for line in pathlib.Path(os.environ["INPUT"]).read_text(encoding="utf-8").splitlines()
]
methods = [item["method"] for item in requests]
assert methods[:5] == [
    "initialize",
    "session/new",
    "session/prompt",
    "session/cancel",
    "session/prompt",
], methods
initialize = requests[0]
assert initialize["params"] == {
    "protocolVersion": 1,
    "clientCapabilities": {
        "fs": {"readTextFile": False, "writeTextFile": False}
    },
}
new_session = requests[1]
assert new_session["params"] == {"cwd": os.environ["ROOT"], "mcpServers": []}
first_prompt = requests[2]
cancel = requests[3]
second_prompt = requests[4]
assert first_prompt["params"]["sessionId"] == "grok-session-live-1"
assert first_prompt["params"]["prompt"] == [
    {"type": "text", "text": os.environ["RULES"] + "initial Grok prompt"}
], first_prompt["params"]["prompt"]
assert "id" not in cancel, cancel
assert cancel["params"] == {"sessionId": "grok-session-live-1"}
assert second_prompt["params"]["sessionId"] == "grok-session-live-1"
assert second_prompt["params"]["prompt"] == [
    {"type": "text", "text": os.environ["REPLY"]}
]
PY
  grep -Fxq 'grok-session-live-1' "$job_dir/out.txt.session-id" || \
    fail "Grok session id was not recorded"
  grep -Fq 'no permission-mode field' "$job_dir/out.txt.stderr.log" || \
    fail "Grok ACP permission limitation was not surfaced"
  grep -Fq 'in-progress work is lost' "$job_dir/out.txt.stderr.log" || \
    fail "Grok cancellation semantics were not surfaced"

  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" || \
    fail "Grok live close failed: $close_out"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Grok live job did not exit zero"
  INPUT="$input" python3 - <<'PY'
import json
import os
import pathlib

requests = [
    json.loads(line)
    for line in pathlib.Path(os.environ["INPUT"]).read_text(encoding="utf-8").splitlines()
]
assert requests[-1]["method"] == "session/close", requests[-1]
assert requests[-1]["params"] == {"sessionId": "grok-session-live-1"}
PY
}

case_grok_live_fallback() {
  local home="$TEST_ROOT/grok-fallback" bin="$TEST_ROOT/grok-fallback/bin"
  local fake="$bin/grok" job job_dir doctor_out
  mkdir -p "$home" "$bin"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "agent" && "${2:-}" == "stdio" ]]; then
  IFS= read -r _ || true
  printf '{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"agent stdio unavailable"}}\n'
  exit 1
fi
printf 'grok single-shot fallback\n'
EOF
  chmod +x "$fake"
  printf 'live-search: grok grok-4.6 high\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
      --mode sysops --workdir "$ROOT" --vendor grok --model grok-4.6 live-search fallback)"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/exit" || fail "Grok fallback job did not finish"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Grok fallback job failed"
  grep -Fxq 'grok single-shot fallback' "$job_dir/out.txt" || \
    fail "Grok failed handshake did not run single-shot"
  grep -Fxq 'omnilane: grok live surface unavailable; ran in single-shot mode' \
    "$job_dir/mode-notice.txt" || fail "Grok fallback notice was not recorded"

  doctor_out="$(OMNILANE_HOME="$home" GROK_BIN="$fake" "$ROOT/scripts/doctor.sh" 2>&1)"
  [[ "$doctor_out" == *"grok-live"*"agent stdio initialize handshake failed"* ]] || \
    fail "doctor did not report Grok handshake failure"
  [[ "$doctor_out" == *"live-unavailable"*"grok"* ]] || \
    fail "doctor did not list Grok as live-unavailable"
}

case_grok_close_grace_precedes_worker_escalation() {
  if ! ROOT="$ROOT" python3 - <<'PY'
import ast
import os
import pathlib
import re

root = pathlib.Path(os.environ["ROOT"])
runner_path = root / "scripts/runners/run-grok-live.py"
worker_path = root / "scripts/lib/job-worker.sh"
tree = ast.parse(runner_path.read_text(encoding="utf-8"))
close_grace = None
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
        continue
    if not isinstance(node.args[0], ast.Constant) or node.args[0].value != "--close-grace":
        continue
    for keyword in node.keywords:
        if keyword.arg == "default":
            close_grace = float(ast.literal_eval(keyword.value))

worker = worker_path.read_text(encoding="utf-8")
grace = re.search(r'^CLOSE_RUNNER_GRACE=([0-9.]+)$', worker, re.MULTILINE)
if close_grace is None or grace is None:
    raise AssertionError("could not read Grok close timing constants")
outer_grace = float(grace.group(1))
if not close_grace + 1.0 < outer_grace:
    raise AssertionError(
        f"Grok inner={close_grace}s plus TERM wait is not below outer={outer_grace}s"
    )
PY
  then
    fail "Grok close grace does not expire before worker escalation"
  fi
}

case_grok_killed_runner_reaps_agent() {
  local case_dir="$TEST_ROOT/grok-killed-runner" fake="$TEST_ROOT/grok-killed-runner/grok"
  mkdir -p "$case_dir"
  : > "$case_dir/inbox"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
trap '' TERM
sleep 30 &
printf '%s %s\n' "$$" "$!" > "${FAKE_GROK_PID_FILE:?}"
wait
EOF
  chmod +x "$fake"

  if ! ROOT="$ROOT" CASE_DIR="$case_dir" FAKE_GROK="$fake" \
    FAKE_GROK_PID_FILE="$case_dir/grok-pids" python3 - <<'PY'
import os
import pathlib
import signal
import subprocess
import time

root = pathlib.Path(os.environ["ROOT"])
case_dir = pathlib.Path(os.environ["CASE_DIR"])
pidfile = pathlib.Path(os.environ["FAKE_GROK_PID_FILE"])
command = [
    str(root / "scripts/runners/run-grok-live.py"),
    "--grok-bin", os.environ["FAKE_GROK"],
    "--cwd", str(case_dir),
    "--model", "fake-model",
    "--mode", "sysops",
    "--inbox", str(case_dir / "inbox"),
    "--events", str(case_dir / "events.jsonl"),
    "--output", str(case_dir / "out.txt"),
    "--progress", str(case_dir / "progress.json"),
    "--session-id-file", str(case_dir / "session-id"),
    "--rpc-timeout", "30",
]
stderr = (case_dir / "stderr").open("w", encoding="utf-8")
runner = subprocess.Popen(
    command,
    cwd=case_dir,
    stdout=subprocess.DEVNULL,
    stderr=stderr,
    start_new_session=True,
)
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.05)
    if not pidfile.exists():
        raise AssertionError("fake Grok ACP pid file was not created")
    pids = [int(value) for value in pidfile.read_text().split()]
    os.killpg(runner.pid, signal.SIGKILL)
    runner.wait(timeout=2)
    deadline = time.monotonic() + 2
    survivors = pids
    while time.monotonic() < deadline:
        current = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            current.append(pid)
        survivors = current
        if not survivors:
            break
        time.sleep(0.05)
    if survivors:
        raise AssertionError(f"surviving Grok ACP processes: {survivors}")
finally:
    stderr.close()
    if runner.poll() is None:
        try:
            os.killpg(runner.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        runner.wait(timeout=2)
    if pidfile.exists():
        for value in pidfile.read_text().split():
            try:
                os.kill(int(value), signal.SIGKILL)
            except ProcessLookupError:
                pass
PY
  then
    fail "Grok live runner left ACP process group alive"
  fi
}

make_eof_sensitive_probe() {
  local path="$1"
  cat > "$path" <<'PY'
#!/usr/bin/env python3
import json
import select
import sys
import time

raw = sys.stdin.readline()
if not raw:
    raise SystemExit(3)
request = json.loads(raw)
readable, _, _ = select.select([sys.stdin], [], [], 0.25)
if readable and sys.stdin.read(1) == "":
    raise SystemExit(42)
if sys.argv[1:] == ["app-server"]:
    result = {"userAgent": "eof-sensitive", "codexHome": "/fake"}
elif sys.argv[1:] == ["agent", "stdio"]:
    result = {"protocolVersion": 1, "agentCapabilities": {}}
else:
    raise SystemExit(4)
print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
time.sleep(0.1)
PY
  chmod +x "$path"
}

case_codex_probe_stdin_open() {
  local case_dir="$TEST_ROOT/codex-probe-stdin" fake legacy
  fake="$case_dir/fake-live-cli.py"
  legacy="$case_dir/live-protocol-legacy.sh"
  mkdir -p "$case_dir"
  make_eof_sensitive_probe "$fake"

  (
    # shellcheck source=../scripts/lib/live-protocol.sh
    source "$ROOT/scripts/lib/live-protocol.sh"
    codex_live_surface_available "$fake"
  ) || fail "Codex probe closed stdin before initialize response"

  SOURCE="$ROOT/scripts/lib/live-protocol.sh" LEGACY="$legacy" python3 - <<'PY'
import os
import pathlib

source = pathlib.Path(os.environ["SOURCE"]).read_text(encoding="utf-8")
needle = "process.stdin.flush()\n"
if needle not in source:
    raise SystemExit("Codex probe flush marker missing")
source = source.replace(needle, needle + "    process.stdin.close()\n", 1)
pathlib.Path(os.environ["LEGACY"]).write_text(source, encoding="utf-8")
PY
  if (
    # shellcheck source=/dev/null
    source "$legacy"
    codex_live_surface_available "$fake"
  ); then
    fail "Codex EOF-sensitive oracle accepted the legacy early-close probe"
  fi
}

case_grok_probe_stdin_open() {
  local case_dir="$TEST_ROOT/grok-probe-stdin" fake legacy
  fake="$case_dir/fake-live-cli.py"
  legacy="$case_dir/live-protocol-legacy.sh"
  mkdir -p "$case_dir"
  make_eof_sensitive_probe "$fake"

  (
    # shellcheck source=../scripts/lib/live-protocol.sh
    source "$ROOT/scripts/lib/live-protocol.sh"
    grok_live_surface_available "$fake"
  ) || fail "Grok probe closed stdin before initialize response"

  SOURCE="$ROOT/scripts/lib/live-protocol.sh" LEGACY="$legacy" python3 - <<'PY'
import os
import pathlib

source = pathlib.Path(os.environ["SOURCE"]).read_text(encoding="utf-8")
head, marker, tail = source.partition("grok_live_surface_available() {")
if not marker or "process.stdin.flush()\n" not in tail:
    raise SystemExit("Grok probe flush marker missing")
tail = tail.replace(
    "process.stdin.flush()\n",
    "process.stdin.flush()\n    process.stdin.close()\n",
    1,
)
pathlib.Path(os.environ["LEGACY"]).write_text(head + marker + tail, encoding="utf-8")
PY
  if (
    # shellcheck source=/dev/null
    source "$legacy"
    grok_live_surface_available "$fake"
  ); then
    fail "Grok EOF-sensitive oracle accepted the legacy early-close probe"
  fi
}

case_grok_model_and_prompt_errors() {
  local case_dir="$TEST_ROOT/grok-model-errors" fake model="grok-nondefault-regression"
  local runner="${GROK_LIVE_RUNNER_UNDER_TEST:-$ROOT/scripts/runners/run-grok-live.py}"
  mkdir -p "$case_dir"
  fake="$case_dir/fake-grok.py"
  cat > "$fake" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


pathlib.Path(os.environ["FAKE_GROK_ARGV"]).write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8"
)
pathlib.Path(os.environ["FAKE_GROK_PID"]).write_text(str(os.getpid()), encoding="utf-8")
input_path = pathlib.Path(os.environ["FAKE_GROK_INPUT"])
mode = os.environ["FAKE_GROK_MODE"]
session_id = "grok-regression-session"
first_prompt_id = None
prompt_count = 0
for raw in sys.stdin:
    with input_path.open("a", encoding="utf-8") as captured:
        captured.write(raw)
    request = json.loads(raw)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session_id}})
    elif method == "session/prompt":
        prompt_count += 1
        if prompt_count == 1:
            first_prompt_id = request_id
        if mode == "error-empty":
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "prompt failed", "data": "private-detail"}})
        elif mode == "error-partial":
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial before error"}}}})
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "prompt failed", "data": "private-detail"}})
        elif prompt_count == 1:
            emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "first turn running"}}}})
        else:
            emit({"jsonrpc": "2.0", "id": first_prompt_id, "error": {"code": -32800, "message": "old cancelled prompt"}})
            emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "replacement succeeded"}}}})
            emit({"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {"sessionId": session_id, "stopReason": "end_turn"}})
    elif method == "session/cancel":
        emit({"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {"sessionId": session_id, "stopReason": "cancelled"}})
    elif method == "session/close":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        break
PY
  chmod +x "$fake"

  run_error_case() {
    local mode="$1" expected_text="$2" pid rc=0 tries=0
    local dir="$case_dir/$mode"
    mkdir -p "$dir"
    mkfifo "$dir/inbox"
    FAKE_GROK_ARGV="$dir/argv.json" FAKE_GROK_PID="$dir/pid" \
      FAKE_GROK_INPUT="$dir/input.jsonl" FAKE_GROK_MODE="$mode" \
      python3 "$runner" --grok-bin "$fake" --cwd "$ROOT" --model "$model" \
      --mode sysops --inbox "$dir/inbox" --events "$dir/events.jsonl" \
      --output "$dir/out.txt" --progress "$dir/progress.json" \
      --session-id-file "$dir/session-id" --rpc-timeout 1 --close-grace 0.5 \
      >"$dir/stdout" 2>"$dir/stderr" &
    pid=$!
    exec 7>"$dir/inbox"
    printf '{"type":"grok-user","text":"trigger error"}\n' >&7
    while kill -0 "$pid" 2>/dev/null && [[ "$tries" -lt 30 ]]; do
      sleep 0.1
      tries=$((tries + 1))
    done
    exec 7>&-
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      fail "Grok $mode prompt error did not fail quickly"
    fi
    wait "$pid" || rc=$?
    [[ "$rc" -ne 0 ]] || fail "Grok $mode prompt error returned success"
    grep -Fq "Grok ACP rejected session/prompt: code=-32001 message='prompt failed'" "$dir/stderr" ||
      fail "Grok $mode prompt error was not observable"
    ! grep -Fq 'private-detail' "$dir/stderr" ||
      fail "Grok $mode prompt error leaked private data"
    if [[ -n "$expected_text" ]]; then
      grep -Fq "$expected_text" "$dir/out.txt" || fail "Grok $mode lost partial output"
    else
      [[ ! -s "$dir/out.txt" ]] || fail "Grok $mode unexpectedly wrote output"
    fi
    if kill -0 "$(cat "$dir/pid")" 2>/dev/null; then
      fail "Grok $mode left fake ACP running"
    fi
  }

  run_error_case error-empty ""
  run_error_case error-partial "partial before error"

  local live="$case_dir/delayed" pid rc=0 tries=0
  mkdir -p "$live"
  mkfifo "$live/inbox"
  FAKE_GROK_ARGV="$live/argv.json" FAKE_GROK_PID="$live/pid" \
    FAKE_GROK_INPUT="$live/input.jsonl" FAKE_GROK_MODE=delayed-cancel \
    python3 "$runner" --grok-bin "$fake" --cwd "$ROOT" --model "$model" \
    --mode sysops --inbox "$live/inbox" --events "$live/events.jsonl" \
    --output "$live/out.txt" --progress "$live/progress.json" \
    --session-id-file "$live/session-id" --rpc-timeout 1 --close-grace 0.5 \
    >"$live/stdout" 2>"$live/stderr" &
  pid=$!
  exec 7>"$live/inbox"
  printf '{"type":"grok-user","text":"first"}\n' >&7
  while ! grep -Fq 'first turn running' "$live/out.txt" 2>/dev/null; do
    [[ "$tries" -lt 30 ]] || fail "Grok delayed-cancel first turn did not start"
    sleep 0.1
    tries=$((tries + 1))
  done
  printf '{"type":"grok-user","text":"replacement"}\n' >&7
  tries=0
  while ! grep -Fq 'replacement succeeded' "$live/out.txt" 2>/dev/null; do
    [[ "$tries" -lt 30 ]] || fail "Grok delayed old error poisoned replacement turn"
    sleep 0.1
    tries=$((tries + 1))
  done
  exec 7>&-
  wait "$pid" || rc=$?
  [[ "$rc" -eq 0 ]] || fail "Grok replacement turn failed on delayed old error"
  ARGV="$live/argv.json" MODEL="$model" python3 - <<'PY'
import json
import os
import pathlib

argv = json.loads(pathlib.Path(os.environ["ARGV"]).read_text(encoding="utf-8"))
assert argv == ["--no-memory", "--no-subagents", "--no-plan", "--verbatim", "--sandbox", "off", "agent", "--always-approve", "--model", os.environ["MODEL"], "stdio"], argv
PY
  grep -Fq '"method":"session/cancel"' "$live/input.jsonl" ||
    fail "Grok replacement turn did not send cancellation"
}

case_grok_advise_fail_closed() {
  local home="$TEST_ROOT/grok-advise" bin="$TEST_ROOT/grok-advise/bin"
  local fake="$bin/grok" args="$home/args.txt" job job_dir
  local direct="$TEST_ROOT/grok-advise-direct" rc=0 before
  local worker="${GROK_JOB_WORKER_UNDER_TEST:-$ROOT/scripts/lib/job-worker.sh}"
  local grok_runner="${GROK_RUNNER_UNDER_TEST:-$ROOT/scripts/runners/run-grok.sh}"
  mkdir -p "$home" "$bin" "$direct/job" "$direct/workdir"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "${FAKE_GROK_ARGS:?}"
printf 'grok advise single-shot\n'
EOF
  chmod +x "$fake"
  printf 'live-search: grok grok-advise-model high\n' > "$home/routing.local.yaml"
  job="$(OMNILANE_HOME="$home" GROK_BIN="$fake" FAKE_GROK_ARGS="$args" \
    "$ROOT/scripts/dispatch.sh" --background --workdir "$ROOT" --vendor grok \
    --model grok-advise-model live-search 'advise dispatch')"
  job_dir="$home/jobs/$job"
  register_job "$home" "$job"
  wait_for_file "$job_dir/exit" || fail "Grok advise single-shot dispatch did not finish"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Grok advise single-shot dispatch failed"
  grep -Fq '"session_mode":"single-shot"' "$job_dir/meta.json" ||
    fail "Grok advise metadata claimed a live session"
  grep -Fxq 'grok advise single-shot' "$job_dir/out.txt" ||
    fail "Grok advise did not execute the fake single-shot CLI"
  ARGS="$args" python3 - <<'PY'
import os
import pathlib

args = pathlib.Path(os.environ["ARGS"]).read_text(encoding="utf-8").splitlines()
index = args.index("--permission-mode")
assert args[index + 1] == "dontAsk", args
assert args[args.index("--tools") + 1] == "Bash,Read,Glob,Grep,web_search,web_fetch", args
assert args[args.index("--reasoning-effort") + 1] == "high", args
assert [args[i + 1] for i, item in enumerate(args[:-1]) if item == "--deny"] == [
    "Bash", "Edit", "MCPTool"
], args
PY
  [[ ! -e "$job_dir/inbox.fifo" && ! -e "$job_dir/runner-inbox.fifo" && ! -e "$job_dir/events.jsonl" ]] ||
    fail "Grok advise single-shot dispatch created live FIFO artifacts"

  printf 'direct advise\n' > "$direct/prompt.txt"
  FAKE_GROK_ARGS="$direct/args.txt" GROK_BIN="$fake" OMNILANE_REPO="$ROOT" \
    OMNILANE_SESSION_MODE=live OMNILANE_LIVE_REQUIRED=0 \
    "$worker" grok advise "$direct/workdir" \
    grok-advise-model high "$direct/prompt.txt" "$direct/job/out.txt"
  grep -Fq -- '--permission-mode' "$direct/args.txt" ||
    fail "Internal Grok advise worker did not downgrade to single-shot restricted mode"
  [[ ! -e "$direct/job/inbox.fifo" && ! -e "$direct/job/runner-inbox.fifo" ]] ||
    fail "Internal Grok advise worker created live FIFOs"

  mkfifo "$direct/live.fifo"
  before="$(shasum -a 256 "$direct/args.txt")"
  set +e
  OMNILANE_INBOX="$direct/live.fifo" FAKE_GROK_ARGS="$direct/args.txt" GROK_BIN="$fake" \
    "$grok_runner" advise "$direct/workdir" grok-advise-model \
    high "$direct/prompt.txt" "$direct/direct-out.txt" >"$direct/direct-stdout" 2>"$direct/direct-stderr"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "Direct Grok advise runner accepted a live FIFO"
  [[ "$(shasum -a 256 "$direct/args.txt")" == "$before" ]] ||
    fail "Direct Grok advise runner invoked CLI after rejecting live FIFO"

  # ACP mailbox behavior uses unspecified effort; an explicit scored effort
  # must still fail before provider launch even in the supported sysops mode.
  rc=0
  OMNILANE_INBOX="$direct/live.fifo" FAKE_GROK_ARGS="$direct/args.txt" GROK_BIN="$fake" \
    "$grok_runner" sysops "$direct/workdir" grok-advise-model \
      high "$direct/prompt.txt" "$direct/effort-out.txt" \
      >"$direct/effort-stdout" 2>"$direct/effort-stderr" || rc=$?
  [[ "$rc" -eq 2 ]] || fail "Direct Grok sysops runner accepted live high effort"
  grep -q 'reasoning effort' "$direct/effort-stderr" ||
    fail "Grok live high effort lacked selector verification diagnostic"
  [[ "$(shasum -a 256 "$direct/args.txt")" == "$before" ]] ||
    fail "Grok runner invoked CLI after rejecting live high effort"
}

# Forced stop of live jobs, offline: each fake job is a controller in a session of its
# own (as dispatch's background subshell is in a group of its own), whose parent exits
# at once, so like a real job it is not this case's child. FS_GROUPS and FS_SESSIONS keep
# every group started here, and the case ends them itself, whatever the code under test did.
FS_GROUPS="" FS_SESSIONS=""
force_stop_fixture_end() {
  local group
  for group in $FS_GROUPS $FS_SESSIONS; do kill -KILL -- "-$group" 2>/dev/null || true; done
}
# Starts fake job $2 in home $1. $3: "ignore" (the controller ignores TERM) or "exit"
# (it exits on TERM). Its child in its group and a grandchild in a session of its own both
# ignore TERM. $4 "nopid" writes no PID file. Sets FS_LAST to the controller PID.
force_stop_fixture_job() {
  local home="$1" id="$2" mode="$3" pidfile="${4:-pid}" dir tries=0 pid session
  dir="$home/jobs/$id"
  mkdir -p "$dir"
  (
    exec perl -MPOSIX=setsid -e 'setsid() or die "setsid: $!"; exec { $ARGV[0] } @ARGV' \
      /bin/bash -c '
        dir="$1" mode="$2" pidfile="$3" real_ps="$4"
        if [[ "$mode" == ignore ]]; then trap "" TERM; else trap "exit 0" TERM; fi
        (trap "" TERM; exec sleep 300) &
        perl -MPOSIX=setsid -e '"'"'setsid(); $SIG{TERM} = "IGNORE"; exec "sleep", "300"'"'"' &
        printf "%s\n" "$!" > "$dir/session"
        if [[ "$pidfile" == pid ]]; then
          start="$(TZ=UTC LC_ALL=C "$real_ps" -o lstart= -p "$$")"
          start="${start#"${start%%[![:space:]]*}"}"
          printf "%s\n" "${start%"${start##*[![:space:]]}"}" > "$dir/pid.start"
          printf "%s\n" "$$" > "$dir/pid.tmp" && mv "$dir/pid.tmp" "$dir/pid"
        fi
        printf "%s\n" "$$" > "$dir/ready"
        while :; do sleep 1; done' fake-controller "$dir" "$mode" "$pidfile" "$FS_REAL_PS" \
      > "$dir/worker.log" 2>&1 < /dev/null &
  )
  while [[ ! -s "$dir/ready" && "$tries" -lt 100 ]]; do sleep 0.1; tries=$((tries + 1)); done
  read -r pid < "$dir/ready" || fail "fake job $id did not start"
  read -r session < "$dir/session" || fail "fake job $id did not record its grandchild"
  # Both children are started before ready is written; give them a moment to exec.
  sleep 0.3
  FS_GROUPS="$FS_GROUPS $pid"
  FS_SESSIONS="$FS_SESSIONS $session"
  FS_LAST="$pid"
}
# Every process of group $1 and of the sessions its members started is gone.
force_stop_group_gone() {
  local listing
  listing="$(TZ=UTC LC_ALL=C "$FS_REAL_PS" -axo pid=,ppid=,pgid=)" || return 1
  printf '%s\n' "$listing" | awk -v g="$1" '$3 == g { found = 1 } END { exit found ? 1 : 0 }'
}
force_stop_alive_count() {
  local group n=0
  for group in $FS_GROUPS $FS_SESSIONS; do kill -0 -- "-$group" 2>/dev/null && n=$((n + 1)); done
  printf '%s\n' "$n"
}
# A ps that answers like ps after FAKE_PS_DELAY seconds for process tables with start
# times, and from call FAKE_PS_REUSE_FROM on lists PID FAKE_PS_REUSE_PID and every
# process of its group with another start time, as reused PIDs would be.
force_stop_fake_ps() {
  local bin="$1"
  mkdir -p "$bin"
  cat > "$bin/ps" <<'EOF'
#!/bin/bash
case " $* " in
  *lstart=*) ;;
  *) exec "$FS_REAL_PS" "$@" ;;
esac
calls=0
read -r calls < "$FAKE_PS_DIR/calls" 2>/dev/null || calls=0
calls=$((calls + 1))
printf '%s\n' "$calls" > "$FAKE_PS_DIR/calls"
sleep "${FAKE_PS_DELAY:-0}"
if [[ -n "${FAKE_PS_REUSE_PID:-}" && "$calls" -ge "${FAKE_PS_REUSE_FROM:-1}" ]]; then
  "$FS_REAL_PS" "$@" | awk -v pid="$FAKE_PS_REUSE_PID" \
    '$1 == pid || $3 == pid { print $1, $2, $3, "Thu Jan  1 00:00:00 1970"; next } { print }'
  exit "${PIPESTATUS[0]}"
fi
exec "$FS_REAL_PS" "$@"
EOF
  cat > "$bin/lsof" <<'EOF'
#!/bin/bash
exit 1
EOF
  chmod +x "$bin/ps" "$bin/lsof"
}

case_force_stop() {
  local scenario="$1" dir="$TEST_ROOT/force-$1" home log start elapsed i pid pids recorded
  local -a homes=() ids=()
  FS_REAL_PS="$(command -v ps)"
  export FS_REAL_PS
  # Under TEST_ROOT, which is inside this run's root, so cleanup removes it.
  home="$TEST_ROOT/force-$scenario-home"
  mkdir -p "$dir" "$home/jobs"
  log="$dir/stderr"
  # set +e first: under set -e, (exit 1) would end the trap before cleanup runs.
  trap 'fs_rc=$?; force_stop_fixture_end; set +e; (exit "$fs_rc"); cleanup' EXIT
  force_stop_fake_ps "$dir/bin"
  export FAKE_PS_DIR="$dir"
  export LIVE_LSOF="$dir/bin/lsof"
  PATH="$dir/bin:$PATH"
  recorded="$dir/recorded"
  : > "$recorded"
  case "$scenario" in
    batch)
      # Four jobs, a process table that takes 2s each time it is taken: one table for all
      # of them leaves time to TERM and KILL every one; a table per job would not.
      for i in 0 1 2 3; do
        force_stop_fixture_job "$home" "job$i" ignore
        homes+=("$home") ids+=("job$i")
      done
      pids="$FS_GROUPS"
      export FAKE_PS_DELAY=2
      FORCE_BY=$((SECONDS + 20))
      start="$SECONDS"
      force_stop_jobs "$recorded" 0 1 2 3 2> "$log"
      elapsed=$((SECONDS - start))
      cat "$log" >&2
      # Each job has two groups: the controller's and its grandchild's session.
      for pid in $pids; do
        grep -q "TERM process group $pid\$" "$log" || fail "batch: the group of controller $pid got no TERM"
        grep -q "KILL process group $pid\$" "$log" || fail "batch: the group of controller $pid got no KILL"
      done
      [[ "$(grep -c 'TERM process group' "$log")" -eq 8 ]] || fail "batch: not every job's groups got TERM"
      [[ "$(grep -c 'KILL process group' "$log")" -eq 8 ]] || fail "batch: not every job's groups got KILL"
      [[ "$(force_stop_alive_count)" -eq 0 ]] || fail "batch: a job group is still running"
      [[ "$elapsed" -le 21 ]] || fail "batch: took ${elapsed}s, over its 20s"
      ;;
    leader-exits)
      # The controller exits on TERM, its child and a grandchild in a session of its own
      # do not: KILL goes by the members recorded, not by the leader.
      force_stop_fixture_job "$home" job0 exit; pid="$FS_LAST"
      homes+=("$home") ids+=("job0")
      FORCE_BY=$((SECONDS + 20))
      force_stop_jobs "$recorded" 0 2> "$log"
      cat "$log" >&2
      grep -q "TERM process group $pid\$" "$log" || fail "leader-exits: no TERM for the job's group"
      grep -q "KILL process group $pid\$" "$log" || fail "leader-exits: the group was not KILLed after its leader exited"
      [[ "$(grep -c 'KILL process group' "$log")" -eq 2 ]] || fail "leader-exits: the grandchild's session was not KILLed"
      force_stop_group_gone "$pid" || fail "leader-exits: a member of the job's group is still running"
      [[ "$(force_stop_alive_count)" -eq 0 ]] || fail "leader-exits: a job group is still running"
      ;;
    reuse-term | reuse-kill)
      # The controller's PID is listed with another start time: from the first table it is
      # never signalled; from the second, it gets TERM but no KILL.
      force_stop_fixture_job "$home" job0 ignore; pid="$FS_LAST"
      homes+=("$home") ids+=("job0")
      export FAKE_PS_REUSE_PID="$pid"
      if [[ "$scenario" == reuse-term ]]; then export FAKE_PS_REUSE_FROM=1; else export FAKE_PS_REUSE_FROM=2; fi
      FORCE_BY=$((SECONDS + 20))
      force_stop_jobs "$recorded" 0 2> "$log"
      cat "$log" >&2
      grep -q "PID $pid now started Thu Jan 1 00:00:00 1970, not .* as recorded; reused, not signalled" "$log" ||
        fail "$scenario: the reused PID was not reported"
      if [[ "$scenario" == reuse-term ]]; then
        ! grep -q "TERM process group $pid\$" "$log" || fail "reuse-term: a reused PID's group got TERM"
      else
        grep -q "TERM process group $pid\$" "$log" || fail "reuse-kill: the job's group got no TERM"
      fi
      ! grep -q "KILL process group $pid\$" "$log" || fail "$scenario: a reused PID's group got KILL"
      kill -0 "$pid" 2>/dev/null || fail "$scenario: the process listed as reused was signalled"
      ;;
    no-pid)
      # No PID file: the controller is found as the holder of the job's worker.log.
      unset LIVE_LSOF
      force_stop_fixture_job "$home" job0 ignore nopid; pid="$FS_LAST"
      homes+=("$home") ids+=("job0")
      FORCE_BY=$((SECONDS + 20))
      force_stop_jobs "$recorded" 0 2> "$log"
      cat "$log" >&2
      grep -q "KILL process group $pid\$" "$log" || fail "no-pid: the holder of worker.log was not ended"
      [[ "$(force_stop_alive_count)" -eq 0 ]] || fail "no-pid: a job group is still running"
      ;;
    tight)
      # FORCE_BY is 4s away and every process table takes 2s: force_stop_jobs keeps to
      # FORCE_BY, not to its own 20s (which would take it to about 8s here), and gives up
      # on what no longer fits.
      for i in 0 1; do
        force_stop_fixture_job "$home" "job$i" ignore
        homes+=("$home") ids+=("job$i")
      done
      export FAKE_PS_DELAY=2
      FORCE_BY=$((SECONDS + 4))
      force_stop_jobs "$recorded" 0 1 2> "$log"
      cat "$log" >&2
      [[ "$SECONDS" -le $((FORCE_BY + 1)) ]] || fail "tight: returned at ${SECONDS}s, past FORCE_BY ${FORCE_BY}s + 1"
      ;;
    stop-flow)
      # The whole stop: the exit is recorded but the controller keeps running and ignores
      # TERM. The job is named as not stopping, every process is gone, and it all ends
      # before FORCE_BY.
      force_stop_fixture_job "$home" job0 exit; pid="$FS_LAST"
      printf '0\n' > "$home/jobs/job0/exit"
      printf '%s\t%s\n' "$home" job0 > "$dir/jobs"
      STOP_BY=$((SECONDS + 30)) FORCE_BY=$((SECONDS + 50))
      start="$SECONDS"
      if stop_jobs "$dir/jobs" 2> "$log"; then cat "$log" >&2; fail "stop-flow: a job that did not stop passed"; fi
      elapsed=$((SECONDS - start))
      cat "$log" >&2
      grep -q 'live job job0: did not stop after cancel and wait; its process groups were ended' "$log" ||
        fail "stop-flow: the job was not named"
      grep -q "KILL process group $pid\$" "$log" || fail "stop-flow: the leader-exited group was not KILLed"
      [[ "$(force_stop_alive_count)" -eq 0 ]] || fail "stop-flow: a job group is still running"
      [[ "$SECONDS" -le $((FORCE_BY + 1)) ]] || fail "stop-flow: ended ${elapsed}s after it started, past FORCE_BY"
      ;;
    holders-proc)
      # The /proc search, on a stand-in tree, reports the PID holding a file.
      mkdir -p "$dir/proc/self/fd" "$dir/proc/123/fd" "$dir/proc/456/fd"
      ln -s "$dir/worker.log" "$dir/proc/123/fd/1"
      ln -s /dev/null "$dir/proc/456/fd/1"
      [[ "$(LIVE_PROC_ROOT="$dir/proc" file_holders 3 "$((SECONDS + 5))" "$dir/worker.log")" == "123 $dir/worker.log" ]] ||
        fail "holders-proc: the holder in /proc was not found"
      ;;
    interrupt)
      # A slow holder search, a hung lsof and a hung command are ended halfway through by
      # their limit, not after.
      mkdir -p "$dir/proc/self/fd"
      for i in $(seq 100 130); do mkdir -p "$dir/proc/$i/fd"; ln -s /dev/null "$dir/proc/$i/fd/1"; done
      start="$SECONDS"
      if LIVE_PROC_ROOT="$dir/proc" LIVE_HOLDER_SCAN_DELAY=1 file_holders 2 "$((SECONDS + 30))" "$dir/worker.log"; then
        fail "interrupt: a /proc search that needs 31s finished within its 2s"
      fi
      [[ $((SECONDS - start)) -le 3 ]] || fail "interrupt: the /proc search ran past its limit"
      # A hung lsof whose helper outlives it, holding lsof's stdout open for 8s.
      printf '#!/bin/bash\nsleep 8 &\nprintf "%%s\\n" "$!" > "%s/lsof-helper"\nexec sleep 30\n' "$dir" > "$dir/bin/lsof"
      start="$SECONDS"
      if LIVE_PROC_ROOT="$dir/no-proc" file_holders 2 "$((SECONDS + 30))" "$dir/worker.log"; then
        fail "interrupt: a hung lsof passed"
      fi
      [[ $((SECONDS - start)) -le 3 ]] || fail "interrupt: lsof ran past its limit"
      read -r i < "$dir/lsof-helper" && kill "$i" 2>/dev/null || true
      start="$SECONDS"
      if within 2 "$((SECONDS + 30))" sleep 30; then fail "interrupt: a hung command passed"; fi
      [[ $((SECONDS - start)) -le 3 ]] || fail "interrupt: a command ran past its limit"
      ;;
    *) fail "unknown force-stop scenario: $scenario" ;;
  esac
  force_stop_fixture_end
  FS_GROUPS="" FS_SESSIONS=""
  trap cleanup EXIT
}

case "$CASE" in
 "")
    for scenario in gemini-success gemini-incomplete gemini-two gemini-empty gemini-idle claude-success claude-error; do
      run_case "call-timeout-$scenario"
      printf 'ok - live per-call timeout %s\n' "$scenario"
    done
    run_case call-timeout-warnings
    printf 'ok - live per-call timeout dispatch warnings\n'
    run_case codex-killed-runner
    printf 'ok - killed Codex live runner reaps app-server process group\n'
    run_case codex-app-server-eof-cleanup
    printf 'ok - Codex app-server prefers EOF cleanup and bounds TERM/KILL escalation\n'
    run_case codex-close-grace-invariant
    printf 'ok - Codex close grace precedes worker escalation\n'
    run_case codex-full-close-budget-invariant
    printf 'ok - Codex turn plus app-server close budget precedes worker escalation\n'
    run_case codex-shutdown-grace
    printf 'ok - Codex live shutdown waits gracefully then escalates\n'
    run_case codex-close-deadline
    printf 'ok - bounded close preserves queued input and precedes jobs timeout\n'
    run_case live-fail-fast
    printf 'ok - live mode rejects non-capable vendor\n'
    run_case single-shot-claude
    printf 'ok - single-shot mode forces Claude one-shot\n'
    run_case idle-cap
    printf 'ok - live mailbox idle cap closes session\n'
    run_case claude-close-recovery
    printf 'ok - Claude close recovers completed result output\n'
    run_case gemini-schema
    printf 'ok - Gemini live mailbox uses agy schema\n'
    run_case json-escape
    printf 'ok - live prompt JSON escaping round-trips exact text\n'
  run_case jobs-send-json-escape
  printf 'ok - jobs.sh send JSON escaping round-trips exact text\n'
  run_case codex-live-rpc
  printf 'ok - Codex live JSON-RPC mailbox and incremental output\n'
  run_case codex-live-large-task
  printf 'ok - Codex live large task truncates only the worker copy\n'
  run_case codex-live-large-task-cleanup
  printf 'ok - Codex live large task stops its job when a step fails\n'
    run_case codex-live-fallback
    printf 'ok - Codex failed handshake degrades to single-shot\n'
    run_case grok-killed-runner
    printf 'ok - killed Grok live runner reaps ACP process group\n'
    run_case grok-close-grace-invariant
    printf 'ok - Grok close grace precedes worker escalation\n'
    run_case grok-live-acp
    printf 'ok - Grok live ACP mailbox, cancellation, and incremental output\n'
    run_case grok-model-errors
    printf 'ok - Grok model argv and prompt error correlation\n'
    run_case grok-advise-fail-closed
    printf 'ok - Grok advise dispatch and internal FIFO fail closed\n'
    run_case grok-live-fallback
    printf 'ok - Grok failed handshake degrades to single-shot\n'
    run_case codex-probe-stdin-open
    printf 'ok - Codex initialize probe keeps stdin open\n'
    run_case grok-probe-stdin-open
    printf 'ok - Grok initialize probe keeps stdin open\n'
    ;;
  live-fail-fast) case_live_fail_fast ;;
  call-timeout-gemini-success) case_live_call_timeout gemini-success ;;
  call-timeout-gemini-incomplete) case_live_call_timeout gemini-incomplete ;;
  call-timeout-gemini-two) case_live_call_timeout gemini-two ;;
  call-timeout-gemini-empty) case_live_call_timeout gemini-empty ;;
  call-timeout-gemini-idle) case_live_call_timeout gemini-idle ;;
  call-timeout-claude-success) case_live_call_timeout claude-success ;;
  call-timeout-claude-error) case_live_call_timeout claude-error ;;
  call-timeout-warnings) case_call_timeout_warnings ;;
  single-shot-claude) case_single_shot_claude ;;
  idle-cap) case_idle_cap ;;
  claude-close-recovery) case_claude_close_recovers_result_output ;;
  gemini-schema) case_gemini_schema ;;
  json-escape) case_json_escape_round_trip ;;
  jobs-send-json-escape) case_jobs_send_json_escape_round_trip ;;
  codex-live-rpc) case_codex_live_rpc ;;
  codex-live-large-task) case_codex_live_large_task ;;
  codex-live-large-task-cleanup) case_codex_live_large_task_cleanup ;;
    codex-live-fallback) case_codex_live_fallback ;;
    codex-killed-runner) case_codex_killed_runner_reaps_app_server ;;
    codex-app-server-eof-cleanup) case_codex_app_server_eof_cleanup ;;
    codex-close-grace-invariant) case_codex_close_grace_precedes_worker_escalation ;;
    codex-full-close-budget-invariant) case_codex_full_close_budget_precedes_worker_escalation ;;
  codex-shutdown-grace) case_codex_shutdown_grace ;;
  codex-close-deadline) /bin/bash "$ROOT/tests/test_live_close_deadline.sh" --omnilane-offline-child ;;
    grok-killed-runner) case_grok_killed_runner_reaps_agent ;;
    grok-close-grace-invariant) case_grok_close_grace_precedes_worker_escalation ;;
    grok-live-acp) case_grok_live_acp ;;
    grok-model-errors) case_grok_model_and_prompt_errors ;;
    grok-advise-fail-closed) case_grok_advise_fail_closed ;;
    grok-live-fallback) case_grok_live_fallback ;;
    codex-probe-stdin-open) case_codex_probe_stdin_open ;;
    grok-probe-stdin-open) case_grok_probe_stdin_open ;;
  force-stop-*) case_force_stop "${CASE#force-stop-}" ;;
  *) fail "unknown live mailbox test case: $CASE" ;;
esac
test_completed=1
