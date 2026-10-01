#!/usr/bin/env bash
set -euo pipefail

# Foreman-driven goal ledger around normal background dispatch.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

DISPATCH="$REPO/scripts/dispatch.sh"
DEFAULT_BUDGET_JOBS=""
DEFAULT_BUDGET_SECONDS=""
GOAL_ID_PATTERN='^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$'
LOCK_HELD=0
GOAL_DIR=""

usage() {
  cat >&2 <<'EOF'
usage: omnilane goal open "TEXT" [--budget-jobs N] [--budget-seconds S] [--workdir DIR]
       omnilane goal dispatch GOAL_ID [dispatch.sh args...]
       omnilane goal note GOAL_ID "TEXT"
       omnilane goal status GOAL_ID
       omnilane goal close GOAL_ID [--summary "TEXT"]

goal dispatch does not support --dry-run; use omnilane dispatch --dry-run
[flags] LANE "TASK" for a routing preview (pass --workdir DIR when needed).
EOF
  exit 2
}

die() {
  local rc="$1"
  shift
  printf 'omnilane goal: %s\n' "$*" >&2
  exit "$rc"
}

require_python() {
  command -v python3 >/dev/null 2>&1 || die 1 "Python 3 required for goal records"
}

validate_positive_integer() {
  local label="$1" value="$2"
  [[ "$value" =~ ^[1-9][0-9]{0,8}$ ]] ||
    die 2 "invalid $label value (want 1..999999999)"
}

load_goal() {
  local goal_id="$1" goals_root="$OMNILANE_HOME/goals"
  [[ "$goal_id" =~ $GOAL_ID_PATTERN ]] || die 2 "invalid goal id"
  GOAL_DIR="$goals_root/$goal_id"
  [[ -d "$GOAL_DIR" && ! -L "$GOAL_DIR" ]] || die 1 "no such goal: $goal_id"
  [[ -f "$GOAL_DIR/goal.txt" && ! -L "$GOAL_DIR/goal.txt" ]] ||
    die 1 "goal text is missing or unsafe: $goal_id"
  [[ -f "$GOAL_DIR/budget.json" && ! -L "$GOAL_DIR/budget.json" ]] ||
    die 1 "goal budget is missing or unsafe: $goal_id"
}

release_lock() {
  if [[ "$LOCK_HELD" -eq 1 ]]; then
    [[ ! -e "$GOAL_DIR/.lock/pid" ]] || rm "$GOAL_DIR/.lock/pid" 2>/dev/null || true
    rmdir "$GOAL_DIR/.lock" 2>/dev/null || true
    LOCK_HELD=0
  fi
}

report_lock_contention() {
  # Diagnostic only: a PID alone cannot identify an owner across PID reuse,
  # and a pathname-based stale-lock removal can race a replacement owner.
  printf 'omnilane goal: lock path: %s\n' "$GOAL_DIR/.lock" >&2
  python3 - "$GOAL_DIR/.lock" <<'PY' >&2 ||
import contextlib
import os
import re
import stat
import sys

def inspect_lock():
    # Open without following lock/PID symlinks; reject special files before
    # reading. O_NONBLOCK also prevents a substituted FIFO from hanging us.
    with contextlib.ExitStack() as stack:
        try:
            lock_fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY |
                              os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return "lock is missing, unsafe, or unreadable; ownership is unknown"
        stack.callback(os.close, lock_fd)
        if os.fstat(lock_fd).st_uid != os.geteuid():
            return "lock directory has a different owner; ownership is unknown"
        try:
            pid_fd = os.open("pid", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=lock_fd)
        except FileNotFoundError:
            return "owner PID is missing; acquisition may be incomplete or interrupted"
        except OSError:
            return "owner PID metadata is unsafe or unreadable; ownership is unknown"
        stack.callback(os.close, pid_fd)
        info = os.fstat(pid_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1):
            return "owner PID metadata is unsafe; ownership is unknown"
        if not 1 <= info.st_size <= 11:
            return "owner PID metadata is invalid; ownership is unknown"
        raw = os.read(pid_fd, 12)
        if not re.fullmatch(rb"[1-9][0-9]{0,9}\n?", raw):
            return "owner PID metadata is invalid; ownership is unknown"
        pid = int(raw)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return f"recorded PID {pid} does not exist; lock may be stale"
        except PermissionError:
            return f"recorded PID {pid} cannot be checked (permission denied); ownership is unknown"
        except (OSError, OverflowError, ValueError):
            return f"recorded PID {pid} cannot be checked; ownership is unknown"
        return f"recorded PID {pid} exists; PID reuse means lock ownership is unverified"

try:
    diagnostic = inspect_lock()
except (OSError, AttributeError, NotImplementedError):
    diagnostic = "owner metadata cannot be inspected safely; ownership is unknown"
print("omnilane goal: lock diagnostic (snapshot): " + diagnostic)
PY
    printf 'omnilane goal: lock diagnostic unavailable; ownership is unknown\n' >&2
  printf 'omnilane goal: lock left unchanged; manual verification is required before any recovery\n' >&2
}

acquire_lock() {
  local tries=0
  while ! mkdir -m 700 "$GOAL_DIR/.lock" 2>/dev/null; do
    tries=$((tries + 1))
    if [[ "$tries" -ge 50 ]]; then
      report_lock_contention
      die 75 "goal is busy: $(basename "$GOAL_DIR")"
    fi
    sleep 0.1
  done
  LOCK_HELD=1
  printf '%s\n' "$$" > "$GOAL_DIR/.lock/pid"
  chmod 600 "$GOAL_DIR/.lock/pid"
  trap release_lock EXIT
}

refresh_goal() {
  python3 - "$GOAL_DIR" "$OMNILANE_HOME" "$(date +%s)" "$REPO/scripts/jobs.sh" <<'PY'
import datetime
import glob
import json
import os
import re
import subprocess
import sys

goal_dir, home, now_text, jobs_script = sys.argv[1:]
now = int(now_text)
sys.path.insert(0, os.path.join(os.path.dirname(jobs_script), "lib"))
import goal_dispatch
from goal_budget import MAX_COUNTER, validate_budget

def load_regular_json(path, limit):
    if not os.path.isfile(path) or os.path.islink(path):
        raise ValueError(f"unsafe or missing JSON record: {path}")
    if os.path.getsize(path) > limit:
        raise ValueError(f"oversized JSON record: {path}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)

def write_json(path, value):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, separators=(",", ":"), ensure_ascii=False)
        handle.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def recorded_terminal_history(record):
    if record.get("state") != "done":
        return False
    if (type(record.get("exit")) is not int or not 0 <= record["exit"] <= 255
            or type(record.get("seconds")) is not int
            or not 0 <= record["seconds"] <= MAX_COUNTER
            or not isinstance(record.get("finished"), str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                                record["finished"])):
        raise ValueError("invalid recorded terminal job history")
    datetime.datetime.strptime(record["finished"], "%Y-%m-%dT%H:%M:%SZ")
    return True

def incomplete_job_state(job_id):
    # Reuse the public, read-only status contract rather than maintaining a
    # second PID interpretation. Never turn an unreadable/timed-out result into
    # a claim that work is still running. Only actual exit files count failures.
    try:
        result = subprocess.run(["bash", jobs_script, "--json", "status", job_id],
                                capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return "unknown"
        payload = json.loads(result.stdout)
        job = payload.get("job", {})
        if (payload.get("schema_version") != 1 or payload.get("command") != "status"
                or payload.get("ok") is not True or not isinstance(job, dict)
                or job.get("id") != job_id or job.get("exit_code") is not None):
            return "unknown"
        state = job.get("state")
        return state if state in {"running", "dead", "pending", "cancelled"} else "unknown"
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return "unknown"

budget_path = os.path.join(goal_dir, "budget.json")
failures_path = os.path.join(goal_dir, "failures.json")
budget = validate_budget(load_regular_json(budget_path, 16384))
failures = load_regular_json(failures_path, 1048576)
if not isinstance(budget, dict) or not isinstance(failures, dict):
    raise ValueError("invalid goal state")
# Reject invalid fields before reconciliation or any ledger rewrite.
_, reserved_jobs, _ = goal_dispatch.reconcile(home, os.path.basename(goal_dir))

# The per-job markers are the durable source of truth. Rebuild this derived
# index on every refresh: a crash can occur after a job marker is published
# but before failures.json is replaced. Incremental counting loses that failure.
failures = {}
records = []
pattern = os.path.join(goal_dir, "jobs", "job-*.json")
for record_path in sorted(glob.glob(pattern)):
    record = load_regular_json(record_path, 262144)
    job_id = record.get("job_id", "")
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+", job_id):
        raise ValueError(f"invalid recorded job id: {record_path}")
    job_dir = os.path.join(home, "jobs", job_id)
    if os.path.isdir(job_dir) and not os.path.islink(job_dir):
        record.pop("artifacts_missing", None)
        meta_path = os.path.join(job_dir, "meta.json")
        if os.path.isfile(meta_path) and not os.path.islink(meta_path):
            meta = load_regular_json(meta_path, 16384)
            for key in ("lane", "vendor", "model", "mode", "workdir", "started"):
                if key in meta:
                    record[key] = meta[key]
        exit_path = os.path.join(job_dir, "exit")
        if os.path.isfile(exit_path) and not os.path.islink(exit_path):
            if os.path.getsize(exit_path) > 32:
                raise ValueError(f"oversized job exit record: {job_id}")
            with open(exit_path, encoding="ascii") as handle:
                exit_text = handle.read().strip()
            if not re.fullmatch(r"[0-9]+", exit_text):
                raise ValueError(f"invalid job exit record: {job_id}")
            record["exit"] = int(exit_text)
            record["state"] = "done"
            record["finished"] = datetime.datetime.fromtimestamp(
                os.path.getmtime(exit_path), datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            submitted = int(record.get("submitted_epoch", now))
            record["seconds"] = max(0, int(os.path.getmtime(exit_path)) - submitted)
            inbox_path = os.path.join(home, "inbox", f"{job_id}.json")
            if os.path.isfile(inbox_path) and not os.path.islink(inbox_path):
                completion = load_regular_json(inbox_path, 1048576)
                if isinstance(completion.get("finished"), str):
                    record["finished"] = completion["finished"]
                if isinstance(completion.get("tail"), str):
                    record["tail"] = completion["tail"][-2000:]
            if record["exit"] != 0:
                record["failure_counted"] = True
        else:
            record["state"] = incomplete_job_state(job_id)
            record["exit"] = None
            submitted = int(record.get("submitted_epoch", now))
            record["seconds"] = max(0, now - submitted)
    else:
        if recorded_terminal_history(record):
            # Artifact cleanup does not undo an observed completion. Keep its
            # recorded outcome and elapsed-to-finish duration, not a new age.
            record["artifacts_missing"] = True
        else:
            record["state"] = "missing"
            record["exit"] = None
            submitted = int(record.get("submitted_epoch", now))
            record["seconds"] = max(0, now - submitted)
    # Retain counted failures even if the completed job was subsequently pruned.
    if record.get("failure_counted", False):
        fingerprint = record.get("fingerprint", "")
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            failures[fingerprint] = failures.get(fingerprint, 0) + 1
    write_json(record_path, record)
    records.append(record)

budget["spent_jobs"] = len(records)
budget["reserved_jobs"] = reserved_jobs
if budget.get("status") == "open":
    budget["spent_seconds"] = max(0, now - int(budget["started_epoch"]))
validate_budget(budget)
write_json(failures_path, failures)
write_json(budget_path, budget)
PY
}

state_fields() {
  python3 - "$GOAL_DIR/budget.json" "$SCRIPT_DIR" <<'PY'
import json
import os
import sys
sys.path.insert(0, sys.argv[2])
from goal_budget import validate_budget

path = sys.argv[1]
if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) > 16384:
    raise SystemExit("invalid goal budget")
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
validate_budget(state)
def field_value(key):
    value = state.get(key, 0)
    if key in {"budget_jobs", "budget_seconds"} and value is None:
        return "unlimited"
    return str(value)

print("\t".join(field_value(key) for key in (
    "status", "spent_jobs", "budget_jobs", "spent_seconds",
    "budget_seconds", "fuse_trips", "workdir", "reserved_jobs",
)))
PY
}

failure_count() {
  local fingerprint="$1"
  python3 - "$GOAL_DIR/failures.json" "$fingerprint" <<'PY'
import json
import os
import sys

path, fingerprint = sys.argv[1:]
if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) > 1048576:
    raise SystemExit("invalid failure record")
with open(path, encoding="utf-8") as handle:
    failures = json.load(handle)
print(int(failures.get(fingerprint, 0)))
PY
}

record_fuse_trip() {
  local fingerprint="$1" lane="$2" task="$3" failures="$4"
  GOAL_FINGERPRINT="$fingerprint" GOAL_LANE="$lane" GOAL_TASK="$task" \
    GOAL_FAILURES="$failures" python3 - "$GOAL_DIR" "$SCRIPT_DIR" <<'PY'
import datetime
import json
import os
import sys
sys.path.insert(0, sys.argv[2])
from goal_budget import validate_budget

goal_dir = sys.argv[1]
budget_path = os.path.join(goal_dir, "budget.json")
with open(budget_path, encoding="utf-8") as handle:
    budget = json.load(handle)
validate_budget(budget)
budget["fuse_trips"] += 1
validate_budget(budget)
tmp = f"{budget_path}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(budget, handle, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, budget_path)
record = {
    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "fingerprint": os.environ["GOAL_FINGERPRINT"],
    "lane": os.environ["GOAL_LANE"],
    "task": os.environ["GOAL_TASK"][:2000],
    "failures": int(os.environ["GOAL_FAILURES"]),
}
path = os.path.join(goal_dir, "fuses.jsonl")
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
os.chmod(path, 0o600)
PY
}


open_goal() {
  local goal_text="${1:-}" budget_jobs="$DEFAULT_BUDGET_JOBS"
  local budget_seconds="$DEFAULT_BUDGET_SECONDS" workdir="$PWD"
  local goals_root goal_id goal_dir started
  [[ $# -ge 1 && -n "$goal_text" ]] || usage
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --budget-jobs)
        [[ $# -ge 2 ]] || usage
        budget_jobs="$2"
        shift 2 ;;
      --budget-seconds)
        [[ $# -ge 2 ]] || usage
        budget_seconds="$2"
        shift 2 ;;
      --workdir)
        [[ $# -ge 2 ]] || usage
        workdir="$2"
        shift 2 ;;
      *) usage ;;
    esac
  done
  [[ -z "$budget_jobs" ]] || validate_positive_integer "--budget-jobs" "$budget_jobs"
  [[ -z "$budget_seconds" ]] || validate_positive_integer "--budget-seconds" "$budget_seconds"
  case "$workdir" in
    *$'\t'*|*$'\r'*|*$'\n'*) die 2 "invalid --workdir: field separators are unsupported" ;;
  esac
  [[ -d "$workdir" ]] || die 2 "workdir is not a directory: $workdir"
  workdir="$(cd "$workdir" && pwd -P)"
  # Normalization can change the string; validate the actual persisted value
  # before creating any goal directory or budget record.
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from goal_budget import validate_workdir; validate_workdir(sys.argv[2])' \
    "$SCRIPT_DIR" "$workdir" || die 2 "invalid normalized --workdir"
  [[ -x "$DISPATCH" ]] || die 1 "dispatch helper unavailable"
  goals_root="$OMNILANE_HOME/goals"
  prepare_private_store "$goals_root" "goals store" || die 1 "could not prepare goals store"
  goal_id="$(date +%Y%m%d-%H%M%S)-$$-$RANDOM"
  goal_dir="$goals_root/$goal_id"
  mkdir -m 700 "$goal_dir"
  mkdir -m 700 "$goal_dir/jobs"
  printf '%s\n' "$goal_text" > "$goal_dir/goal.txt"
  printf '{}\n' > "$goal_dir/failures.json"
  : > "$goal_dir/notes.jsonl"
  chmod 600 "$goal_dir/goal.txt" "$goal_dir/failures.json" "$goal_dir/notes.jsonl"
  started="$(date +%s)"
  GOAL_WORKDIR="$workdir" python3 - "$goal_dir/budget.json" "$budget_jobs" \
    "$budget_seconds" "$started" <<'PY'
import json
import os
import sys

path, jobs, seconds, started = sys.argv[1:]
state = {
    "schema_version": 3,
    "budget_jobs": None if jobs == "" else int(jobs),
    "budget_seconds": None if seconds == "" else int(seconds),
    "spent_jobs": 0,
    "reserved_jobs": 0,
    "spent_seconds": 0,
    "fuse_trips": 0,
    "status": "open",
    "started_epoch": int(started),
    "workdir": os.environ["GOAL_WORKDIR"],
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(state, handle, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
os.chmod(path, 0o600)
PY
  printf '%s\n' "$goal_id"
}

relay_goal_thread_notice() {
  # Diagnostic only, after exact claim-ID verification. Never replay arbitrary
  # captured stderr or let a missing notice turn a launched job into failure.
  python3 - "$OMNILANE_HOME/jobs/$1/meta.json" "$2" <<'PY' 2>/dev/null || true
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    meta = json.loads(handle.read(65536))
thread, turn, vendor = meta.get("thread"), meta.get("thread_turn"), meta.get("vendor")
if (not isinstance(thread, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", thread)
        or type(turn) is not int or not 1 <= turn <= 1000000000
        or vendor not in ("claude", "codex", "grok", "gemini")):
    sys.exit(0)
prefix = f"omnilane: thread {thread} turn {turn} ({vendor}"
mode = "new" if turn == 1 else "resume"
pattern = re.escape(prefix) + r" session [A-Za-z0-9._:-]{1,256}, " + mode + r"\)\n"
try:
    with open(sys.argv[2], encoding="utf-8", errors="replace") as handle:
        captured = handle.read(65536)
except OSError:
    captured = ""
for line in captured.splitlines(keepends=True):
    if re.fullmatch(pattern, line):
        print(line, end="")
        break
else:
    # The verified job still has useful visibility if its full notice was not
    # captured within the diagnostic bound. Session details are optional.
    print(prefix + ")")
PY
}

reject_goal_dry_run() {
  # Match dispatch.sh's option boundaries without changing its parser: options
  # end at the first positional lane, and a flag's value may be "--dry-run".
  # Keep the value-taking arm in sync (covered by test_goal_dry_run.py).
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        die 2 "goal dispatch does not support --dry-run; use omnilane dispatch --dry-run for a routing preview" ;;
      --background|--live|--single-shot|--inherit|--operator-asserted-human)
        shift ;;
      --mode|--workdir|--vendor|--model|--effort|--timeout|--job-timeout|--idle-timeout|--thread|--executor|--native-context|--caller-context|--aa-policy|--target-config|--transport-overlay)
        [[ $# -ge 2 ]] || return 0
        shift 2 ;;
      *) return 0 ;;
    esac
  done
}

dispatch_goal() {
  local goal_id="${1:-}" lane task task_value fingerprint failures
  local status spent_jobs budget_jobs spent_seconds budget_seconds workdir lane_index
  local dispatch_output dispatch_rc error_path intent_ref reserved_jobs
  [[ $# -ge 3 ]] || usage
  shift
  reject_goal_dry_run "$@"
  load_goal "$goal_id"
  acquire_lock
  refresh_goal
  IFS=$'\t' read -r status spent_jobs budget_jobs spent_seconds budget_seconds \
    _ workdir reserved_jobs < <(state_fields)
  [[ "$status" == "open" ]] || die 75 "goal is closed: $goal_id"
  [[ "$reserved_jobs" -eq 0 ]] || die 75 "unresolved dispatch reservations: $reserved_jobs; no new jobs launched"
  if [[ "$budget_jobs" != "unlimited" && "$((spent_jobs + reserved_jobs))" -ge "$budget_jobs" ]]; then
    die 75 "jobs budget exhausted: $spent_jobs/$budget_jobs"
  fi
  if [[ "$budget_seconds" != "unlimited" && "$spent_seconds" -ge "$budget_seconds" ]]; then
    die 75 "seconds budget exhausted: ${spent_seconds}s/${budget_seconds}s"
  fi

  lane_index=$(($# - 1))
  lane="${!lane_index}"
  task="${!#}"
  task_value="$task"
  if [[ "$task" == "-" ]]; then
    task_value="$(cat)"
  fi
  fingerprint="$(python3 - "$lane" "$task_value" <<'PY'
import hashlib
import sys
print(hashlib.sha256((sys.argv[1] + "\0" + sys.argv[2]).encode("utf-8")).hexdigest())
PY
)"
  failures="$(failure_count "$fingerprint")"
  if [[ "$failures" -ge 2 ]]; then
    record_fuse_trip "$fingerprint" "$lane" "$task_value" "$failures"
    die 75 "failure fuse tripped: lane=$lane failures=$failures fingerprint=$fingerprint"
  fi

  intent_ref="$(python3 "$SCRIPT_DIR/goal_dispatch.py" prepare "$OMNILANE_HOME" "$goal_id" \
    "$lane" "$fingerprint" "$task_value")"
  error_path="$GOAL_DIR/dispatch-error-$(date +%s)-$$.txt"
  set +e
  if [[ "$task" == "-" ]]; then
    dispatch_output="$(printf '%s' "$task_value" | OMNILANE_GOAL_INTENT="$intent_ref" OMNILANE_HOME="$OMNILANE_HOME" \
      "$DISPATCH" --background --workdir "$workdir" "$@" 2>"$error_path")"
    dispatch_rc=$?
  else
    dispatch_output="$(OMNILANE_GOAL_INTENT="$intent_ref" OMNILANE_HOME="$OMNILANE_HOME" "$DISPATCH" --background \
      --workdir "$workdir" "$@" 2>"$error_path")"
    dispatch_rc=$?
  fi
  set -e
  if [[ "$dispatch_rc" -ne 0 ]]; then
    python3 "$SCRIPT_DIR/goal_dispatch.py" abort "$OMNILANE_HOME" "$intent_ref" || true
    refresh_goal
    chmod 600 "$error_path" 2>/dev/null || true
    die "$dispatch_rc" "dispatch failed (exit $dispatch_rc; details: $error_path)"
  fi
  if [[ ! "$dispatch_output" =~ $GOAL_ID_PATTERN ]] ||
    ! python3 "$SCRIPT_DIR/goal_dispatch.py" verify "$OMNILANE_HOME" "$intent_ref" "$dispatch_output"; then
    python3 "$SCRIPT_DIR/goal_dispatch.py" abort "$OMNILANE_HOME" "$intent_ref" || true
    refresh_goal
    chmod 600 "$error_path" 2>/dev/null || true
    die 1 "dispatch returned invalid job id (details: $error_path)"
  fi
  [[ ! -s "$error_path" ]] || chmod 600 "$error_path"
  [[ -s "$error_path" ]] || rm "$error_path"
  refresh_goal
  release_lock
  trap - EXIT
  relay_goal_thread_notice "$dispatch_output" "$error_path" >&2
  printf '%s\n' "$dispatch_output"
}

note_goal() {
  local goal_id="${1:-}" note_text="${2:-}"
  [[ $# -eq 2 && -n "$note_text" ]] || usage
  load_goal "$goal_id"
  acquire_lock
  refresh_goal
  GOAL_NOTE="$note_text" python3 - "$GOAL_DIR/notes.jsonl" <<'PY'
import datetime
import json
import os
import sys

path = sys.argv[1]
record = {
    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "text": os.environ["GOAL_NOTE"],
}
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
os.chmod(path, 0o600)
PY
  release_lock
  trap - EXIT
}

status_goal() {
  local goal_id="${1:-}"
  [[ $# -eq 1 ]] || usage
  load_goal "$goal_id"
  acquire_lock
  refresh_goal
  python3 - "$GOAL_DIR" "$SCRIPT_DIR" <<'PY'
import glob
import json
import os
import sys
sys.path.insert(0, sys.argv[2])
from goal_budget import validate_budget

try:
    goal_dir = sys.argv[1]
    with open(os.path.join(goal_dir, "budget.json"), encoding="utf-8") as handle:
        budget = validate_budget(json.load(handle))

    def budget_limit(value):
        return "unlimited" if value is None else str(value)

    print(f"status: {budget['status']}")
    print(f"jobs: {budget['spent_jobs']} / {budget_limit(budget['budget_jobs'])}")
    print(f"reserved jobs: {budget.get('reserved_jobs', 0)}")
    print(f"seconds: {budget['spent_seconds']} / {budget_limit(budget['budget_seconds'])}")
    print(f"fuse trips: {budget.get('fuse_trips', 0)}")
    for path in sorted(glob.glob(os.path.join(goal_dir, "jobs", "job-*.json"))):
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        exit_value = record.get("exit")
        exit_text = record.get("state", "unknown") if exit_value is None else str(exit_value)
        availability = " artifacts=missing" if record.get("artifacts_missing") else ""
        print(
            f"job {record['job_id']}: lane={record.get('lane', 'unknown')} "
            f"vendor={record.get('vendor', 'unknown')} exit={exit_text} "
            f"seconds={record.get('seconds', 0)}{availability}"
        )
    sys.stdout.flush()
except BrokenPipeError:
    # Prevent Python's shutdown flush from reporting the same closed pipe again.
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        os.dup2(devnull.fileno(), sys.stdout.fileno())
    sys.exit(0)
PY
  release_lock
  trap - EXIT
}

close_goal() {
  local goal_id="${1:-}" summary="" report_path
  [[ $# -ge 1 ]] || usage
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --summary)
        [[ $# -ge 2 ]] || usage
        summary="$2"
        shift 2 ;;
      *) usage ;;
    esac
  done
  load_goal "$goal_id"
  acquire_lock
  refresh_goal
  GOAL_SUMMARY="$summary" python3 - "$GOAL_DIR" "$goal_id" "$(date +%s)" "$SCRIPT_DIR" <<'PY'
import glob
import json
import os
import re
import sys
sys.path.insert(0, sys.argv[4])
from goal_budget import validate_budget

goal_dir, goal_id, closed_text = sys.argv[1:4]

def read_text(name):
    path = os.path.join(goal_dir, name)
    if not os.path.isfile(path) or os.path.islink(path):
        raise ValueError(f"unsafe or missing goal record: {name}")
    with open(path, encoding="utf-8") as handle:
        return handle.read().rstrip("\n")

def data_block(value):
    longest = max((len(item) for item in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    suffix = "" if value.endswith("\n") else "\n"
    return f"{fence}text\n{value}{suffix}{fence}"

def budget_limit(value):
    return "unlimited" if value is None else str(value)

budget_path = os.path.join(goal_dir, "budget.json")
with open(budget_path, encoding="utf-8") as handle:
    budget = json.load(handle)
validate_budget(budget)
if budget.get("reserved_jobs", 0):
    print("omnilane goal: unresolved dispatch reservations; goal remains open", file=sys.stderr)
    raise SystemExit(75)
if budget.get("status") != "open":
    raise SystemExit("goal is already closed")
budget["status"] = "closed"
budget["spent_seconds"] = max(0, int(closed_text) - int(budget["started_epoch"]))
budget["closed_epoch"] = int(closed_text)
validate_budget(budget)

notes = []
notes_path = os.path.join(goal_dir, "notes.jsonl")
with open(notes_path, encoding="utf-8") as handle:
    for line in handle:
        if line.strip():
            notes.append(json.loads(line))
summary = os.environ.get("GOAL_SUMMARY", "")
if not summary:
    summary = "\n".join(f"[{note['timestamp']}] {note['text']}" for note in notes)
if not summary:
    summary = "No foreman summary provided."
summary_path = os.path.join(goal_dir, "summary.txt")
with open(summary_path, "w", encoding="utf-8") as handle:
    handle.write(summary + "\n")
os.chmod(summary_path, 0o600)

jobs = []
for path in sorted(glob.glob(os.path.join(goal_dir, "jobs", "job-*.json"))):
    with open(path, encoding="utf-8") as handle:
        jobs.append(json.load(handle))
job_lines = []
for job in jobs:
    exit_value = job.get("exit")
    exit_text = job.get("state", "unknown") if exit_value is None else str(exit_value)
    availability = " artifacts=missing" if job.get("artifacts_missing") else ""
    job_lines.append(
        f"job {job['job_id']}: lane={job.get('lane', 'unknown')} "
        f"vendor={job.get('vendor', 'unknown')} exit={exit_text} "
        f"seconds={job.get('seconds', 0)}{availability} task={job.get('task', '')}"
    )
if not job_lines:
    job_lines.append("No jobs recorded.")
note_lines = [f"[{note['timestamp']}] {note['text']}" for note in notes]
if not note_lines:
    note_lines.append("No foreman notes recorded.")

artifacts = []
for value in (summary, *(note["text"] for note in notes)):
    for candidate in re.findall(r"`([^`\r\n]+)`", value):
        if candidate.startswith(("/", "./", "../", "~/")) or "/" in candidate:
            if candidate not in artifacts:
                artifacts.append(candidate)

lines = [
    f"# Goal report: {goal_id}",
    "",
    "## Goal",
    "",
    data_block(read_text("goal.txt")),
    "",
    "## Outcome",
    "",
    "- Status: closed",
    "",
    "## Budget",
    "",
    f"- Jobs: {budget['spent_jobs']} / {budget_limit(budget['budget_jobs'])}",
    f"- Seconds: {budget['spent_seconds']} / {budget_limit(budget['budget_seconds'])}",
    f"- Fuse trips: {budget.get('fuse_trips', 0)}",
    "",
    "## Jobs",
    "",
    data_block("\n".join(job_lines)),
    "",
    "## Foreman notes (data)",
    "",
    data_block("\n".join(note_lines)),
    "",
    "## Foreman summary (data)",
    "",
    data_block(summary),
    "",
    "## Artifact paths named by foreman",
    "",
]
lines.append(data_block("\n".join(artifacts)) if artifacts else "None recorded.")
lines.append("")
report_path = os.path.join(goal_dir, "report.md")
report_tmp = f"{report_path}.tmp.{os.getpid()}"
with open(report_tmp, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines))
os.chmod(report_tmp, 0o600)
os.replace(report_tmp, report_path)

# Seal only after every report input validated and the report was published.
# A failed/interrupted publication leaves the goal open and safely retryable.
tmp = f"{budget_path}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(budget, handle, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, budget_path)
PY
  report_path="$GOAL_DIR/report.md"
  release_lock
  trap - EXIT
  printf '%s\n' "$report_path"
}

require_python
subcommand="${1:-}"
shift || true
case "$subcommand" in
  open) open_goal "$@" ;;
  dispatch) dispatch_goal "$@" ;;
  note) note_goal "$@" ;;
  status) status_goal "$@" ;;
  close) close_goal "$@" ;;
  *) usage ;;
esac
