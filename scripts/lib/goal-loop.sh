#!/usr/bin/env bash
set -euo pipefail

# Foreman-driven goal ledger around normal background dispatch.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

DISPATCH="$REPO/scripts/dispatch.sh"
DEFAULT_BUDGET_JOBS=8
DEFAULT_BUDGET_SECONDS=900
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

acquire_lock() {
  local tries=0
  while ! mkdir -m 700 "$GOAL_DIR/.lock" 2>/dev/null; do
    tries=$((tries + 1))
    [[ "$tries" -lt 50 ]] || die 75 "goal is busy: $(basename "$GOAL_DIR")"
    sleep 0.1
  done
  LOCK_HELD=1
  printf '%s\n' "$$" > "$GOAL_DIR/.lock/pid"
  chmod 600 "$GOAL_DIR/.lock/pid"
  trap release_lock EXIT
}

refresh_goal() {
  python3 - "$GOAL_DIR" "$OMNILANE_HOME" "$(date +%s)" <<'PY'
import datetime
import glob
import json
import os
import re
import sys

goal_dir, home, now_text = sys.argv[1:]
now = int(now_text)

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

budget_path = os.path.join(goal_dir, "budget.json")
failures_path = os.path.join(goal_dir, "failures.json")
budget = load_regular_json(budget_path, 16384)
failures = load_regular_json(failures_path, 1048576)
if not isinstance(budget, dict) or not isinstance(failures, dict):
    raise ValueError("invalid goal state")

records = []
pattern = os.path.join(goal_dir, "jobs", "job-*.json")
for record_path in sorted(glob.glob(pattern)):
    record = load_regular_json(record_path, 262144)
    job_id = record.get("job_id", "")
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+", job_id):
        raise ValueError(f"invalid recorded job id: {record_path}")
    job_dir = os.path.join(home, "jobs", job_id)
    if os.path.isdir(job_dir) and not os.path.islink(job_dir):
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
            if record["exit"] != 0 and not record.get("failure_counted", False):
                fingerprint = record.get("fingerprint", "")
                if re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                    failures[fingerprint] = int(failures.get(fingerprint, 0)) + 1
                record["failure_counted"] = True
        else:
            record["state"] = "running"
            record["exit"] = None
            submitted = int(record.get("submitted_epoch", now))
            record["seconds"] = max(0, now - submitted)
    else:
        record["state"] = "missing"
        record["exit"] = None
        submitted = int(record.get("submitted_epoch", now))
        record["seconds"] = max(0, now - submitted)
    write_json(record_path, record)
    records.append(record)

budget["spent_jobs"] = len(records)
if budget.get("status") == "open":
    budget["spent_seconds"] = max(0, now - int(budget["started_epoch"]))
write_json(failures_path, failures)
write_json(budget_path, budget)
PY
}

state_fields() {
  python3 - "$GOAL_DIR/budget.json" <<'PY'
import json
import os
import sys

path = sys.argv[1]
if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) > 16384:
    raise SystemExit("invalid goal budget")
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
required = {
    "status", "budget_jobs", "budget_seconds", "spent_jobs",
    "spent_seconds", "fuse_trips", "started_epoch", "workdir",
}
if not isinstance(state, dict) or not required.issubset(state):
    raise SystemExit("invalid goal budget")
print("\t".join(str(state[key]) for key in (
    "status", "spent_jobs", "budget_jobs", "spent_seconds",
    "budget_seconds", "fuse_trips", "workdir",
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
    GOAL_FAILURES="$failures" python3 - "$GOAL_DIR" <<'PY'
import datetime
import json
import os
import sys

goal_dir = sys.argv[1]
budget_path = os.path.join(goal_dir, "budget.json")
with open(budget_path, encoding="utf-8") as handle:
    budget = json.load(handle)
budget["fuse_trips"] = int(budget.get("fuse_trips", 0)) + 1
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

record_dispatch() {
  local job_id="$1" lane="$2" fingerprint="$3" task="$4"
  GOAL_JOB_ID="$job_id" GOAL_LANE="$lane" GOAL_FINGERPRINT="$fingerprint" \
    GOAL_TASK="$task" python3 - "$GOAL_DIR" "$OMNILANE_HOME" "$(date +%s)" <<'PY'
import datetime
import json
import os
import sys

goal_dir, home, submitted_text = sys.argv[1:]
budget_path = os.path.join(goal_dir, "budget.json")
with open(budget_path, encoding="utf-8") as handle:
    budget = json.load(handle)
ordinal = int(budget["spent_jobs"]) + 1
job_id = os.environ["GOAL_JOB_ID"]
record = {
    "ordinal": ordinal,
    "job_id": job_id,
    "lane": os.environ["GOAL_LANE"],
    "vendor": "unknown",
    "model": "",
    "mode": "unknown",
    "workdir": budget["workdir"],
    "task": os.environ["GOAL_TASK"][:2000],
    "fingerprint": os.environ["GOAL_FINGERPRINT"],
    "submitted_epoch": int(submitted_text),
    "submitted": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "state": "running",
    "exit": None,
    "seconds": 0,
    "failure_counted": False,
}
meta_path = os.path.join(home, "jobs", job_id, "meta.json")
if os.path.isfile(meta_path) and not os.path.islink(meta_path) and os.path.getsize(meta_path) <= 16384:
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)
    for key in ("lane", "vendor", "model", "mode", "workdir", "started"):
        if key in meta:
            record[key] = meta[key]
jobs_dir = os.path.join(goal_dir, "jobs")
record_path = os.path.join(jobs_dir, f"job-{ordinal:08d}.json")
if os.path.exists(record_path):
    raise SystemExit("goal job record collision")
with open(record_path, "x", encoding="utf-8") as handle:
    json.dump(record, handle, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
os.chmod(record_path, 0o600)
budget["spent_jobs"] = ordinal
budget["spent_seconds"] = max(0, int(submitted_text) - int(budget["started_epoch"]))
tmp = f"{budget_path}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(budget, handle, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, budget_path)
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
  validate_positive_integer "--budget-jobs" "$budget_jobs"
  validate_positive_integer "--budget-seconds" "$budget_seconds"
  [[ -d "$workdir" ]] || die 2 "workdir is not a directory: $workdir"
  workdir="$(cd "$workdir" && pwd -P)"
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
    "budget_jobs": int(jobs),
    "budget_seconds": int(seconds),
    "spent_jobs": 0,
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

dispatch_goal() {
  local goal_id="${1:-}" lane task task_value fingerprint failures
  local status spent_jobs budget_jobs spent_seconds budget_seconds workdir lane_index
  local dispatch_output dispatch_rc error_path
  [[ $# -ge 3 ]] || usage
  shift
  load_goal "$goal_id"
  acquire_lock
  refresh_goal
  IFS=$'\t' read -r status spent_jobs budget_jobs spent_seconds budget_seconds \
    _ workdir < <(state_fields)
  [[ "$status" == "open" ]] || die 75 "goal is closed: $goal_id"
  [[ "$spent_jobs" -lt "$budget_jobs" ]] ||
    die 75 "jobs budget exhausted: $spent_jobs/$budget_jobs"
  [[ "$spent_seconds" -lt "$budget_seconds" ]] ||
    die 75 "seconds budget exhausted: ${spent_seconds}s/${budget_seconds}s"

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

  error_path="$GOAL_DIR/dispatch-error-$(date +%s)-$$.txt"
  set +e
  if [[ "$task" == "-" ]]; then
    dispatch_output="$(printf '%s' "$task_value" | OMNILANE_HOME="$OMNILANE_HOME" \
      "$DISPATCH" --background --workdir "$workdir" "$@" 2>"$error_path")"
    dispatch_rc=$?
  else
    dispatch_output="$(OMNILANE_HOME="$OMNILANE_HOME" "$DISPATCH" --background \
      --workdir "$workdir" "$@" 2>"$error_path")"
    dispatch_rc=$?
  fi
  set -e
  if [[ "$dispatch_rc" -ne 0 ]]; then
    chmod 600 "$error_path" 2>/dev/null || true
    die "$dispatch_rc" "dispatch failed (exit $dispatch_rc; details: $error_path)"
  fi
  if [[ ! "$dispatch_output" =~ $GOAL_ID_PATTERN ]]; then
    chmod 600 "$error_path" 2>/dev/null || true
    die 1 "dispatch returned invalid job id (details: $error_path)"
  fi
  [[ ! -s "$error_path" ]] || chmod 600 "$error_path"
  [[ -s "$error_path" ]] || rm "$error_path"
  record_dispatch "$dispatch_output" "$lane" "$fingerprint" "$task_value"
  release_lock
  trap - EXIT
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
  python3 - "$GOAL_DIR" <<'PY'
import glob
import json
import os
import sys

goal_dir = sys.argv[1]
with open(os.path.join(goal_dir, "budget.json"), encoding="utf-8") as handle:
    budget = json.load(handle)
print(f"status: {budget['status']}")
print(f"jobs: {budget['spent_jobs']}/{budget['budget_jobs']}")
print(f"seconds: {budget['spent_seconds']}/{budget['budget_seconds']}")
print(f"fuse trips: {budget.get('fuse_trips', 0)}")
for path in sorted(glob.glob(os.path.join(goal_dir, "jobs", "job-*.json"))):
    with open(path, encoding="utf-8") as handle:
        record = json.load(handle)
    exit_value = record.get("exit")
    exit_text = "running" if exit_value is None else str(exit_value)
    print(
        f"job {record['job_id']}: lane={record.get('lane', 'unknown')} "
        f"vendor={record.get('vendor', 'unknown')} exit={exit_text} "
        f"seconds={record.get('seconds', 0)}"
    )
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
  GOAL_SUMMARY="$summary" python3 - "$GOAL_DIR" "$goal_id" "$(date +%s)" <<'PY'
import glob
import json
import os
import re
import sys

goal_dir, goal_id, closed_text = sys.argv[1:]

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

budget_path = os.path.join(goal_dir, "budget.json")
with open(budget_path, encoding="utf-8") as handle:
    budget = json.load(handle)
if budget.get("status") != "open":
    raise SystemExit("goal is already closed")
budget["status"] = "closed"
budget["spent_seconds"] = max(0, int(closed_text) - int(budget["started_epoch"]))
budget["closed_epoch"] = int(closed_text)
tmp = f"{budget_path}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(budget, handle, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, budget_path)

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
    exit_text = "running" if exit_value is None else str(exit_value)
    job_lines.append(
        f"job {job['job_id']}: lane={job.get('lane', 'unknown')} "
        f"vendor={job.get('vendor', 'unknown')} exit={exit_text} "
        f"seconds={job.get('seconds', 0)} task={job.get('task', '')}"
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
    f"- Jobs: {budget['spent_jobs']} / {budget['budget_jobs']}",
    f"- Seconds: {budget['spent_seconds']} / {budget['budget_seconds']}",
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
