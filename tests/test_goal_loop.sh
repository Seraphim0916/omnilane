#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CASE="${1:-}"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-goal-tests.XXXXXX")"

cleanup() {
  /bin/rm -r -- "$TEST_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

make_fixture() {
  local home="$1" worker="$1/worker.sh"
  mkdir -p "$home/work"
  cat > "$worker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# exec runner signature: MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE
task="$(cat "$4")"
printf '%s|%s|%s\n' "$1" "$2" "$task" >> "${FAKE_WORKER_CALLS:?}"
case "$task" in
  slow*) sleep 1 ;;
  fail*)
    printf 'failed %s\n' "$task" > "$5"
    exit 9 ;;
esac
printf 'completed %s\n' "$task" > "$5"
EOF
  chmod +x "$worker"
  printf 'probe: exec "%s" -\n' "$worker" > "$home/routing.local.yaml"
}

open_goal() {
  local home="$1" text="$2" jobs="$3" seconds="$4"
  OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal open "$text" \
    --budget-jobs "$jobs" --budget-seconds "$seconds" --workdir "$home/work"
}

dispatch_goal() {
  local home="$1" goal_id="$2" task="$3"
  OMNILANE_HOME="$home" FAKE_WORKER_CALLS="$home/worker.calls" \
    "$ROOT/bin/omnilane" goal dispatch "$goal_id" --mode work probe "$task"
}

wait_for_job() {
  local home="$1" job_id="$2" expected="$3" tries=0 actual
  while [[ "$tries" -lt 100 && ! -f "$home/jobs/$job_id/exit" ]]; do
    sleep 0.1
    tries=$((tries + 1))
  done
  [[ -f "$home/jobs/$job_id/exit" ]] || fail "job did not finish: $job_id"
  actual="$(tr -d '[:space:]' < "$home/jobs/$job_id/exit")"
  [[ "$actual" == "$expected" ]] || fail "job exit mismatch: want=$expected got=$actual"
}

case_open() {
  local home="$TEST_ROOT/open" goal_id goal_dir physical_workdir
  make_fixture "$home"
  goal_id="$(open_goal "$home" "repair the checkout flow" 4 60)"
  [[ "$goal_id" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$ ]] ||
    fail "open did not print a goal id: $goal_id"
  goal_dir="$home/goals/$goal_id"
  [[ -f "$goal_dir/goal.txt" && -f "$goal_dir/budget.json" ]] ||
    fail "open did not create goal records"
  [[ "$(cat "$goal_dir/goal.txt")" == "repair the checkout flow" ]] ||
    fail "goal text was not preserved"
  physical_workdir="$(cd "$home/work" && pwd -P)"
  python3 - "$goal_dir/budget.json" "$physical_workdir" <<'PY' || fail "open budget record mismatch"
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    budget = json.load(handle)
assert budget["schema_version"] == 3
assert budget["budget_jobs"] == 4 and budget["budget_seconds"] == 60
assert budget["spent_jobs"] == 0 and budget["spent_seconds"] == 0
assert budget["status"] == "open" and budget["workdir"] == sys.argv[2]
assert "budget_parallel" not in budget
PY
}

case_dispatch_allow() {
  local home="$TEST_ROOT/dispatch-allow" goal_id job_id status physical_workdir
  make_fixture "$home"
  goal_id="$(open_goal "$home" "run one bounded job" 2 30)"
  job_id="$(dispatch_goal "$home" "$goal_id" "succeed once")"
  [[ "$job_id" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$ ]] ||
    fail "dispatch did not print a job id: $job_id"
  wait_for_job "$home" "$job_id" 0
  status="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id")"
  [[ "$status" == *"jobs: 1/2"* && "$status" == *"job $job_id:"* &&
     "$status" == *"lane=probe vendor=exec exit=0"* ]] ||
    fail "allowed dispatch was not recorded: $status"
  physical_workdir="$(cd "$home/work" && pwd -P)"
  [[ "$(cat "$home/worker.calls")" == "work|$physical_workdir|succeed once" ]] ||
    fail "goal workdir or task did not reach dispatch"
}

case_jobs_cap() {
  local home="$TEST_ROOT/jobs-cap" goal_id first out rc calls
  make_fixture "$home"
  goal_id="$(open_goal "$home" "enforce job cap" 1 30)"
  first="$(dispatch_goal "$home" "$goal_id" "first")"
  wait_for_job "$home" "$first" 0
  set +e
  out="$(dispatch_goal "$home" "$goal_id" "second" 2>&1)"
  rc=$?
  set -e
  [[ "$rc" -ne 0 && "$out" == *"jobs budget exhausted: 1/1"* ]] ||
    fail "jobs cap did not refuse before dispatch: rc=$rc out=$out"
  calls="$(wc -l < "$home/worker.calls" | tr -d '[:space:]')"
  [[ "$calls" == "1" ]] || fail "over-budget job reached worker: calls=$calls"
}

case_seconds_cap() {
  local home="$TEST_ROOT/seconds-cap" goal_id out rc
  make_fixture "$home"
  goal_id="$(open_goal "$home" "enforce seconds cap" 2 1)"
  sleep 2
  set +e
  out="$(dispatch_goal "$home" "$goal_id" "too late" 2>&1)"
  rc=$?
  set -e
  [[ "$rc" -ne 0 && "$out" == *"seconds budget exhausted:"* ]] ||
    fail "seconds cap did not refuse before dispatch: rc=$rc out=$out"
  [[ ! -e "$home/worker.calls" ]] || fail "seconds-capped job reached worker"
}

case_fuse() {
  local home="$TEST_ROOT/fuse" goal_id first second out rc status calls
  make_fixture "$home"
  goal_id="$(open_goal "$home" "stop repeated failures" 5 30)"
  first="$(dispatch_goal "$home" "$goal_id" "fail identically")"
  wait_for_job "$home" "$first" 9
  second="$(dispatch_goal "$home" "$goal_id" "fail identically")"
  wait_for_job "$home" "$second" 9
  set +e
  out="$(dispatch_goal "$home" "$goal_id" "fail identically" 2>&1)"
  rc=$?
  set -e
  [[ "$rc" -ne 0 && "$out" == *"failure fuse tripped: lane=probe failures=2"* ]] ||
    fail "fuse did not refuse the third identical failure: rc=$rc out=$out"
  calls="$(wc -l < "$home/worker.calls" | tr -d '[:space:]')"
  [[ "$calls" == "2" ]] || fail "fused dispatch reached worker: calls=$calls"
  status="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id")"
  [[ "$status" == *"jobs: 2/5"* && "$status" == *"fuse trips: 1"* ]] ||
    fail "status did not report fuse accounting: $status"
}

case_note_status() {
  local home="$TEST_ROOT/note-status" goal_id slow failed running done_status
  make_fixture "$home"
  goal_id="$(open_goal "$home" "observe jobs as they land" 4 30)"
  slow="$(dispatch_goal "$home" "$goal_id" "slow success")"
  running="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id")"
  [[ "$running" == *"job $slow:"* && "$running" == *"exit=running"* ]] ||
    fail "status did not show running job: $running"
  wait_for_job "$home" "$slow" 0
  failed="$(dispatch_goal "$home" "$goal_id" "fail once")"
  wait_for_job "$home" "$failed" 9
  OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal note "$goal_id" \
    "reviewed results in /tmp/goal-result.txt"
  done_status="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id")"
  [[ "$done_status" == *"jobs: 2/4"* && "$done_status" == *"job $slow:"* &&
     "$done_status" == *"exit=0"* && "$done_status" == *"job $failed:"* &&
     "$done_status" == *"exit=9"* ]] || fail "status per-job lines mismatch: $done_status"
  grep -q 'reviewed results in /tmp/goal-result.txt' "$home/goals/$goal_id/notes.jsonl" ||
    fail "foreman note was not recorded"
}

case_close_report() {
  local home="$TEST_ROOT/close-report" goal_id job_id report summary_text
  make_fixture "$home"
  goal_id="$(open_goal "$home" "finish the payment audit" 3 30)"
  job_id="$(dispatch_goal "$home" "$goal_id" "audit payment")"
  wait_for_job "$home" "$job_id" 0
  OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal note "$goal_id" \
    "worker result accepted"
  summary_text="foreman completed \`/tmp/final.txt\`"
  report="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal close "$goal_id" \
    --summary "$summary_text")"
  [[ "$report" == "$home/goals/$goal_id/report.md" && -f "$report" ]] ||
    fail "close did not print or write report path: $report"
  grep -q 'finish the payment audit' "$report" || fail "report omitted goal text"
  grep -q "job $job_id:" "$report" || fail "report omitted recorded job"
  grep -q -- '- Jobs: 1 / 3' "$report" || fail "report omitted job budget"
  grep -q -- '- Seconds:' "$report" || fail "report omitted seconds budget"
  grep -q '## Foreman summary (data)' "$report" || fail "report omitted foreman summary section"
  grep -q "foreman completed \`/tmp/final.txt\`" "$report" || fail "report omitted close summary"
  grep -q 'worker result accepted' "$report" || fail "report omitted foreman notes"
  grep -q '/tmp/final.txt' "$report" || fail "report omitted named artifact"
  OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id" |
    grep -q '^status: closed$' || fail "close did not seal goal state"
}

case_cli_surface() {
  local home="$TEST_ROOT/cli-surface" out rc
  make_fixture "$home"
  set +e
  out="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal "old one-shot" 2>&1)"
  rc=$?
  set -e
  [[ "$rc" -eq 2 && "$out" == *"usage: omnilane goal open"* ]] ||
    fail "deprecated one-shot goal form remained active: rc=$rc out=$out"
  set +e
  out="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal open "no parallel cap" \
    --budget-parallel 2 2>&1)"
  rc=$?
  set -e
  [[ "$rc" -eq 2 && "$out" == *"usage: omnilane goal open"* ]] ||
    fail "removed --budget-parallel remained active: rc=$rc out=$out"
}

case "$CASE" in
  open) case_open ;;
  dispatch-allow) case_dispatch_allow ;;
  jobs-cap) case_jobs_cap ;;
  seconds-cap) case_seconds_cap ;;
  fuse) case_fuse ;;
  note-status) case_note_status ;;
  close-report) case_close_report ;;
  cli-surface) case_cli_surface ;;
  *) fail "unknown case: $CASE" ;;
esac
