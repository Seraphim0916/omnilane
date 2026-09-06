#!/usr/bin/env bash
set -euo pipefail

# Internal worker boundary for one dispatch. An optional whole-job supervisor
# wraps this process so lock wait, retries, and vote rounds share one budget.

# A per-job worker snapshot lives outside the repository tree. The dispatcher
# pins its library root explicitly; direct invocations remain runtime-relative.
JOB_WORKER_REPO="${OMNILANE_JOB_WORKER_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck disable=SC1091
source "$JOB_WORKER_REPO/scripts/lib/common.sh"
if [[ -n "${OMNILANE_JOB_WORKER_EXPECTED_SHA256:-}" ]]; then
  JOB_WORKER_STARTUP_SHA256="$(file_sha256 "${BASH_SOURCE[0]}")" || exit 2
  if [[ "$JOB_WORKER_STARTUP_SHA256" != "$OMNILANE_JOB_WORKER_EXPECTED_SHA256" ]]; then
    echo "omnilane: job worker snapshot SHA changed before startup" >&2
    exit 2
  fi
fi
unset OMNILANE_JOB_WORKER_REPO OMNILANE_JOB_WORKER_EXPECTED_SHA256
# shellcheck disable=SC1091
source "$OMNILANE_REPO/scripts/lib/live-protocol.sh"

[[ $# -eq 7 ]] || { echo "omnilane: internal job worker received invalid arguments" >&2; exit 2; }
VENDOR="$1"; MODE="$2"; WORKDIR="$3"; MODEL="$4"; EFFORT="$5"
PROMPT_FILE="$6"; OUTPUT_FILE="$7"

[[ "$VENDOR" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid worker vendor" >&2; exit 2; }
RUNNER="$OMNILANE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omnilane: no runner for vendor '$VENDOR'" >&2; exit 2; }

# Two concurrent codex execs in one target dir corrupt its job index — serialize.
[[ "$VENDOR" == "codex" ]] && acquire_cwd_lock codex "$WORKDIR"

emit_mode_notice() {
  local notice="$1" notice_file="${OUTPUT_FILE%/*}/mode-notice.txt"
  printf '%s\n' "$notice" >&2
  if [[ -L "$notice_file" || ( -e "$notice_file" && ! -f "$notice_file" ) ]]; then
    echo "omnilane: unsafe mode notice path" >&2
    return 1
  fi
  (umask 077; printf '%s\n' "$notice" > "$notice_file")
}

run_single_shot() {
  local notice="$1" rc
  aa_policy_gate "$VENDOR" "$MODEL" "$EFFORT" || return $?
  set +e
  (
    unset OMNILANE_INBOX
    "$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE"
  )
  rc=$?
  set -e
  emit_mode_notice "$notice"
  return "$rc"
}

SESSION_MODE="${OMNILANE_SESSION_MODE:-auto}"
IDLE_TIMEOUT="${OMNILANE_IDLE_TIMEOUT:-900}"
LIVE_REQUIRED="${OMNILANE_LIVE_REQUIRED:-0}"
[[ "$SESSION_MODE" == "auto" || "$SESSION_MODE" == "live" || "$SESSION_MODE" == "single-shot" ]] || {
  echo "omnilane: invalid worker session mode" >&2; exit 2
}
[[ "$IDLE_TIMEOUT" =~ ^(0|[1-9][0-9]*)$ ]] || {
  echo "omnilane: invalid worker idle timeout" >&2; exit 2
}
[[ "$LIVE_REQUIRED" == "0" || "$LIVE_REQUIRED" == "1" ]] || {
  echo "omnilane: invalid worker live requirement" >&2; exit 2
}
if [[ "$VENDOR" == "grok" && "$MODE" != "sysops" && "$SESSION_MODE" != "single-shot" ]]; then
  if [[ "$LIVE_REQUIRED" -eq 1 ]]; then
    echo "omnilane: Grok --live supports only explicit --mode sysops; restricted ACP policy is unavailable" >&2
    exit 2
  fi
  SESSION_MODE="single-shot"
fi
if [[ "$SESSION_MODE" == "auto" ]]; then
 if [[ "$VENDOR" == "codex" || "$VENDOR" == "grok" ]]; then
    SESSION_MODE="single-shot"
  elif live_vendor_capable "$VENDOR"; then
    SESSION_MODE="live"
  else
    SESSION_MODE="single-shot"
  fi
fi

LIVE_SURFACE_FALLBACK=""
if [[ "$VENDOR" == "codex" && "$SESSION_MODE" != "single-shot" ]]; then
  if ! codex_live_surface_available "${CODEX_BIN:-codex}"; then
    SESSION_MODE="single-shot"
    LIVE_SURFACE_FALLBACK="codex"
  fi
elif [[ "$VENDOR" == "grok" && "$SESSION_MODE" != "single-shot" ]]; then
  if ! grok_live_surface_available "${GROK_BIN:-grok}"; then
    SESSION_MODE="single-shot"
    LIVE_SURFACE_FALLBACK="grok"
  fi
fi

if [[ "$SESSION_MODE" == "single-shot" ]]; then
  set +e
  if [[ -n "$LIVE_SURFACE_FALLBACK" ]]; then
    run_single_shot "omnilane: $LIVE_SURFACE_FALLBACK live surface unavailable; ran in single-shot mode"
  elif live_vendor_capable "$VENDOR"; then
    run_single_shot "omnilane: vendor '$VENDOR' was resolved to single-shot mode"
  else
    run_single_shot "omnilane: vendor '$VENDOR' is not live-capable; ran in single-shot mode"
  fi
  rc=$?
  set -e
  exit "$rc"
fi

if ! live_vendor_capable "$VENDOR"; then
  echo "omnilane: vendor '$VENDOR' cannot run required live session; live-capable vendors: $(live_capable_vendors)" >&2
  exit 2
fi

JOB_DIR="${OUTPUT_FILE%/*}"
JOB_ID="${JOB_DIR##*/}"
INBOX_FIFO="$JOB_DIR/inbox.fifo"
RUNNER_INBOX_FIFO="$JOB_DIR/runner-inbox.fifo"
HOLDER_PID_FILE="$JOB_DIR/inbox.holder.pid"
READY_FILE="$JOB_DIR/inbox.ready"
EVENTS_FILE="${OUTPUT_FILE}.events.jsonl"
EVENTS_ALIAS="$JOB_DIR/events.jsonl"
close_requested=0
close_reason=""
close_drain_failed=0
close_partial=""
natural_runner_exit=0
CLOSE_DRAIN_TIMEOUT=0.1
CLOSE_RUNNER_GRACE=7.5
CLOSE_TERM_GRACE=0.1
CLOSE_KILL_GRACE=0.1
runner_writer_open=0
inbox_open=0
events_reader_open=0
forward_spool_open=0
runner_pid=""

# Invoked by the EXIT trap below.
# shellcheck disable=SC2329
cleanup_live_mailbox() {
  if [[ -n "${forward_spool:-}" ]]; then rm "$forward_spool" 2>/dev/null || true; fi
  if [[ "$forward_spool_open" -eq 1 ]]; then exec 6<&-; exec 7>&-; forward_spool_open=0; fi
  if [[ "$runner_writer_open" -eq 1 ]]; then exec 3>&-; runner_writer_open=0; fi
  if [[ "$inbox_open" -eq 1 ]]; then exec 4>&-; inbox_open=0; fi
  if [[ "$events_reader_open" -eq 1 ]]; then exec 5<&-; events_reader_open=0; fi
  rm "$READY_FILE" "$HOLDER_PID_FILE" "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" 2>/dev/null || true
}

prepare_live_mailbox() {
  local path old_umask rc=0
  for path in "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" "$HOLDER_PID_FILE" \
    "$READY_FILE" "$EVENTS_FILE" "$EVENTS_ALIAS"; do
    [[ ! -e "$path" && ! -L "$path" ]] || return 1
  done
  old_umask="$(umask)"
  umask 077
  mkfifo "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    : > "$EVENTS_FILE" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    ln "$EVENTS_FILE" "$EVENTS_ALIAS" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    chmod 600 "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" "$EVENTS_FILE" "$EVENTS_ALIAS" || rc=$?
  fi
  umask "$old_umask"
  if [[ "$rc" -ne 0 ]]; then
    rm "$EVENTS_ALIAS" "$EVENTS_FILE" "$INBOX_FIFO" "$RUNNER_INBOX_FIFO" 2>/dev/null || true
  fi
  return "$rc"
}

# The Claude normalizer appends a result event of its own when it recovers a
# partial transcript, so that marker is what separates "the turn finished" from
# "we salvaged what the model had written so far".
live_event_is_vendor_result() {
  local event="$1"
  live_event_is_result "$VENDOR" "$event" || return 1
  [[ "$event" != *'"normalized_by"'* ]]
}

last_result_status() {
  local event last_result="" vendor_only="${1:-0}"
  while IFS= read -r event || [[ -n "$event" ]]; do
    if [[ "$vendor_only" -eq 1 ]]; then
      if live_event_is_vendor_result "$event"; then last_result="$event"; fi
    elif live_event_is_result "$VENDOR" "$event"; then
      last_result="$event"
    fi
  done < "$EVENTS_FILE"
  [[ -n "$last_result" ]] || return 1
  live_event_is_success "$VENDOR" "$last_result"
}

close_had_result() {
  local vendor_only="$1"
  if [[ -n "$last_result_event" ]] \
    && { [[ "$vendor_only" -eq 0 ]] || live_event_is_vendor_result "$last_result_event"; } \
    && live_event_is_success "$VENDOR" "$last_result_event"; then
    return 0
  fi
  last_result_status "$vendor_only"
}

recover_close_result_output() {
  # Claude's stream-json process may remain resident after stdin EOF. The
  # worker then enforces its bounded close deadline, so the runner's signal
  # trap is not guaranteed enough time to normalize the already-completed
  # result. Preserve that successful vendor result before declaring the job
  # done; never synthesize output for an incomplete turn or overwrite output
  # the runner already committed.
  [[ "$VENDOR" == "claude" ]] || return 0
  [[ ! -s "$OUTPUT_FILE" ]] || return 0
  command -v python3 >/dev/null 2>&1 || return 1
  python3 "$OMNILANE_REPO/scripts/lib/normalize-claude-stream.py" \
    "$EVENTS_FILE" "$OUTPUT_FILE"
}

if ! prepare_live_mailbox; then
  if [[ "$LIVE_REQUIRED" -eq 1 ]]; then
    echo "omnilane: required $VENDOR live mailbox unavailable because FIFO setup failed" >&2
    exit 1
  fi
  set +e
  run_single_shot "omnilane: $VENDOR live mailbox unavailable because FIFO setup failed; ran in single-shot mode"
  rc=$?
  set -e
  exit "$rc"
fi

trap 'close_requested=1' USR1
trap 'close_requested=1' PIPE
trap cleanup_live_mailbox EXIT
if [[ "$VENDOR" == "codex" ]]; then
  # Allocate once before publishing the holder PID. Reader and writer offsets
  # are independent; unlink immediately and keep this 0600 file private to us.
  forward_spool="$(mktemp "$JOB_DIR/.forward.XXXXXX")" || exit 1
  exec 6< "$forward_spool"
  forward_spool_open=1
  exec 7> "$forward_spool"
  rm "$forward_spool"
  forward_spool=""
fi
truncate_payload "$PROMPT_FILE" 102400
# Codex input must not be consumed by Bash read: an interrupted partial read
# can discard bytes before the close trap runs. Pump raw bytes instead. The
# anonymous file holds at most one 64 KiB chunk, with shared read offset on FD 6
# and an independent append/reset offset on FD 7. A close drains its remaining
# suffix first; successful sends truncate/reset it instead of growing a spool.
pump_codex_input() {
  python3 - <<'PYPUMP'
import os
import select
import sys
import time

pending = b""
progress = False

def reset_spool():
    os.ftruncate(7, 0)
    os.lseek(6, 0, os.SEEK_SET)
    os.lseek(7, 0, os.SEEK_SET)

os.set_blocking(3, False)
os.set_blocking(4, False)
try:
    pending = os.read(6, 65536)
    if not pending:
        reset_spool()
        readable, _, _ = select.select([4], [], [], 1)
        if not readable:
            sys.exit(3)
        chunk = os.read(4, 65536)
        if not chunk:
            sys.exit(1)
        # FD 7 is a private regular file, never the backpressured FIFO.
        while chunk:
            chunk = chunk[os.write(7, chunk):]
        pending = os.read(6, 65536)
        progress = True
    deadline = time.monotonic() + 0.05
    while pending and time.monotonic() < deadline:
        _, writable, _ = select.select([], [3], [], max(0, deadline - time.monotonic()))
        if writable:
            pending = pending[os.write(3, pending):]
            progress = True
    if not pending:
        reset_spool()
except OSError:
    if pending:
        os.lseek(6, -len(pending), os.SEEK_CUR)
    sys.exit(1)
if pending:
    os.lseek(6, -len(pending), os.SEEK_CUR)
sys.exit((4 if progress else 2) if pending else 0)
PYPUMP
}

INITIAL_TEXT="$(cat "$PROMPT_FILE")"
if [[ "${FOREMAN_SESSION+x}" == "x" ]]; then
  FOREMAN_SESSION_VALUE="$FOREMAN_SESSION"
else
  FOREMAN_SESSION_VALUE="${foreman_session-}"
fi

write_current_pid_file "$HOLDER_PID_FILE"
export OMNILANE_INBOX="$RUNNER_INBOX_FIFO"
aa_policy_gate "$VENDOR" "$MODEL" "$EFFORT" || exit $?
"$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE" 6<&- 7>&- &
runner_pid=$!

# Open the runner writer only after its FIFO reader starts.
exec 3> "$RUNNER_INBOX_FIFO"
runner_writer_open=1
exec 4<> "$INBOX_FIFO"
inbox_open=1
exec 5< "$EVENTS_FILE"
events_reader_open=1
initial_payload="$(live_encode_message "$VENDOR" "$JOB_ID" "$FOREMAN_SESSION_VALUE" "$INITIAL_TEXT")"
if ! printf '%s\n' "$initial_payload" >&3; then close_requested=1; fi
(umask 077; : > "$READY_FILE")

last_activity=$SECONDS
last_result_event=""
# A dead Codex reader can leave accepted bytes in the public FIFO even when
# the previous pump was idle. Always take one final bounded drain in that case.
while kill -0 "$runner_pid" 2>/dev/null || [[ "$VENDOR" == "codex" || "$close_requested" -ne 0 ]]; do
  if [[ "$VENDOR" == "codex" && "$close_requested" -eq 0 ]] && ! kill -0 "$runner_pid" 2>/dev/null; then
    natural_runner_exit=1
    close_requested=1
  fi
  if [[ "$close_requested" -ne 0 ]]; then
    rm "$READY_FILE" 2>/dev/null || true
    if [[ "$VENDOR" == "codex" ]]; then
      # Bash 3.2 has no fractional read timeout. Bound the *whole* drain,
      # including backpressure, rather than granting each line another second.
      # Retain unforwarded bytes and report failure instead of silently dropping
      # an accepted follow-up when the runner stalls or a writer never stops.
      if ! CLOSE_PARTIAL="$close_partial" FORWARD_PENDING="$forward_spool_open" python3 - "$JOB_DIR/close-pending.jsonl" "$CLOSE_DRAIN_TIMEOUT" <<'PY'
import array
import fcntl
import os
import select
import sys
import termios
import time

deadline = time.monotonic() + float(sys.argv[2])
pending = b""
if os.environ.get("FORWARD_PENDING") == "1":
    while True:
        chunk = os.read(6, 65536)
        if not chunk:
            break
        pending += chunk
pending += os.environ.get("CLOSE_PARTIAL", "").encode()
for fd in (3, 4):
    os.set_blocking(fd, False)
try:
    while time.monotonic() < deadline:
        readable, writable, _ = select.select([4], [3] if pending else [], [], 0)
        if not readable and not pending:
            sys.exit(0)
        if readable and len(pending) < 65536:
            pending += os.read(4, 65536 - len(pending))
        if pending:
            _, writable, _ = select.select([], [3], [], max(0, deadline - time.monotonic()))
            if writable:
                pending = pending[os.write(3, pending):]
except (BrokenPipeError, OSError):
    pass
# Snapshot only bytes already queued at the deadline; a continuous writer
# cannot extend this phase. New send callers no longer see inbox.ready.
available = array.array("i", [0])
fcntl.ioctl(4, termios.FIONREAD, available, True)
remaining = available[0]
while remaining:
    chunk = os.read(4, min(remaining, 65536))
    if not chunk:
        break
    pending += chunk
    remaining -= len(chunk)
if pending:
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as saved:
        saved.write(pending)
    sys.exit(1)
PY
      then
        close_drain_failed=1
        emit_mode_notice "omnilane: close drain incomplete; unforwarded input retained in $JOB_DIR/close-pending.jsonl" || true
      fi
    fi
    break
  fi
  if [[ "$VENDOR" == "codex" ]]; then
    if pump_codex_input; then
      last_activity=$SECONDS
    else
      pump_status=$?
      # 2/4 retain a suffix (4 made progress); 3 is an idle poll. A reader
      # that dies with pending bytes still enters the close drain, even after
      # its PID disappears, so those bytes are retained rather than dropped.
      if [[ "$pump_status" -eq 2 || "$pump_status" -eq 4 ]]; then
        [[ "$pump_status" -ne 4 ]] || last_activity=$SECONDS
        kill -0 "$runner_pid" 2>/dev/null || close_requested=1
      elif [[ "$pump_status" -ne 3 ]]; then
        close_requested=1
      fi
    fi
  else
    incoming=""
    if IFS= read -r -t 1 incoming <&4; then
      if ! printf '%s\n' "$incoming" >&3; then close_requested=1; fi
      last_activity=$SECONDS
    elif [[ "$close_requested" -ne 0 && -n "$incoming" ]]; then
      close_partial="$incoming"
    fi
  fi
  while IFS= read -r event <&5; do
    [[ "$close_requested" -eq 0 ]] || break
    if live_event_is_valid "$event"; then
      last_activity=$SECONDS
      if live_event_is_result "$VENDOR" "$event"; then
        last_result_event="$event"
      fi
    fi
  done
  if [[ "$IDLE_TIMEOUT" -gt 0 && $((SECONDS - last_activity)) -ge "$IDLE_TIMEOUT" ]]; then
    close_reason="closed by idle cap after ${IDLE_TIMEOUT}s"
    close_requested=1
  fi
done

if [[ "$forward_spool_open" -eq 1 ]]; then exec 6<&-; exec 7>&-; forward_spool_open=0; fi
if [[ "$runner_writer_open" -eq 1 ]]; then exec 3>&-; runner_writer_open=0; fi
if [[ "$inbox_open" -eq 1 ]]; then exec 4>&-; inbox_open=0; fi
if [[ "$events_reader_open" -eq 1 ]]; then exec 5<&-; events_reader_open=0; fi

set +e
if [[ "$close_requested" -eq 1 ]]; then
  # 0.1s drain + 7.5s grace + 0.1s TERM + 0.1s KILL = 7.8s.
  # With the input pump's 1s read + 0.05s write, 8.85s also precedes
  # the shortest ~9s interval of jobs close's integer SECONDS + 10 deadline.
  # Codex retains its full 3+2+1+1=7s normal shutdown budget.
  # A monotonic timer avoids accumulating 75 shell/sleep launch overheads.
  if ! perl -MTime::HiRes=clock_gettime,CLOCK_MONOTONIC,sleep -e '
    my ($pid, $grace, $term, $kill) = @ARGV;
    sub wait_until {
      my ($pid, $duration) = @_;
      my $deadline = clock_gettime(CLOCK_MONOTONIC) + $duration;
      while (kill 0, $pid) {
        my $left = $deadline - clock_gettime(CLOCK_MONOTONIC);
        return 0 if $left <= 0;
        sleep($left < 0.02 ? $left : 0.02);
      }
      return 1;
    }
    exit 0 if wait_until($pid, $grace);
    kill "TERM", $pid;
    exit 0 if wait_until($pid, $term);
    kill "KILL", $pid;
    exit(wait_until($pid, $kill) ? 0 : 1);
  ' "$runner_pid" "$CLOSE_RUNNER_GRACE" "$CLOSE_TERM_GRACE" "$CLOSE_KILL_GRACE"; then
    close_drain_failed=1
  fi
fi
if [[ "$close_requested" -eq 1 ]] && kill -0 "$runner_pid" 2>/dev/null; then
  runner_rc=1
  close_drain_failed=1
  emit_mode_notice "omnilane: runner did not stop within close deadline" || true
else
  wait "$runner_pid" 2>/dev/null
  runner_rc=$?
fi
set -e

if [[ -n "$close_reason" ]]; then
  printf '\n%s\n' "$close_reason" >> "$OUTPUT_FILE"
  emit_mode_notice "$close_reason" || true
fi

if [[ "$natural_runner_exit" -eq 1 ]]; then
  # Draining after a natural/failed reader exit must not turn its exit status
  # into success because an earlier turn happened to emit a result event.
  rc="$runner_rc"
  if [[ "$rc" -eq 0 && "$close_drain_failed" -ne 0 ]]; then rc=1; fi
elif [[ "$close_requested" -eq 1 ]]; then
  # A vendor-emitted result always stands. A transcript the normalizer recovered
  # stands only for an idle-cap close whose runner still exited on its own: that
  # is the case the idle-cap recovery was written for, and out.txt carries the
  # cap notice next to it. An operator close is an abort, so its recovered
  # transcript is written but must not be reported as a finished turn.
  recovered_counts=0
  if [[ -n "$close_reason" && "$runner_rc" -eq 0 ]]; then recovered_counts=1; fi
    if [[ "$close_drain_failed" -eq 0 ]] && { close_had_result 1 \
      || { [[ "$recovered_counts" -eq 1 ]] && close_had_result 0; }; }; then
      if recover_close_result_output; then
        rc=0
      else
        rc=1
        emit_mode_notice "omnilane: Claude live result completed but output recovery failed" || true
      fi
  else
    rc=1
    emit_mode_notice "omnilane: $VENDOR live mailbox closed without a successful result event" || true
  fi
else
  rc="$runner_rc"
fi

trap - USR1 PIPE
exit "$rc"
