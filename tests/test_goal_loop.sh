#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CASE="${1:-}"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-goal-tests.XXXXXX")"

cleanup() {
  /bin/rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

wait_for_file() {
  local path="$1" tries=0
  while [[ "$tries" -lt 100 ]]; do
    [[ -f "$path" ]] && return 0
    sleep 0.1
    tries=$((tries + 1))
  done
  return 1
}

make_fixture() {
  local home="$1" scenario="$2"
  local bin planner worker
  bin="$home/bin"
  planner="$bin/claude"
  worker="$home/worker.sh"
  mkdir -p "$bin" "$home/work"

  cat > "$planner" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

turn=0
emit_result() {
  RESULT_TEXT="$1" python3 - <<'PY'
import json
import os
print(json.dumps({"type": "result", "is_error": False,
                  "result": os.environ["RESULT_TEXT"]}, separators=(",", ":")))
PY
}

while IFS= read -r line; do
  printf '%s\n' "$line" >> "${FAKE_PLANNER_INPUT:?}"
  turn=$((turn + 1))
  case "${PLANNER_SCENARIO:?}" in
    dispatch-done)
      if [[ "$turn" -eq 1 ]]; then
        emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"work","task":"inspect fixture"}]}'
      else
        emit_result '{"action":"done","summary":"fixture goal complete"}'
      fi
      ;;
    worker-single-shot)
      if [[ "$line" == *'single-shot worker'* ]]; then
        emit_result 'worker finished'
        break
      elif [[ "$turn" -eq 1 ]]; then
        emit_result '{"action":"dispatch","jobs":[{"lane":"hardest-coding","mode":"work","task":"single-shot worker"}]}'
      else
        emit_result '{"action":"done","summary":"single-shot worker complete"}'
      fi
      ;;
    multi)
      case "$turn" in
        1)
          emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"advise","task":"first task"},{"lane":"worker","mode":"work","task":"second task"}]}'
          ;;
        2) emit_result '{"action":"wait"}' ;;
        *) emit_result '{"action":"done","summary":"two workers complete"}' ;;
      esac
      ;;
      parallel)
        case "$turn" in
          1)
            emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"work","task":"slow task"},{"lane":"worker","mode":"work","task":"fast task"}]}'
            ;;
          2) emit_result '{"action":"wait"}' ;;
          *) emit_result '{"action":"done","summary":"parallel workers complete"}' ;;
        esac
        ;;
    fuse)
      case "$turn" in
        1|2|3) emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"work","task":"always fail"}]}' ;;
        *) emit_result '{"action":"done","summary":"fuse stopped retry loop"}' ;;
      esac
      ;;
    invalid-lane)
      if [[ "$turn" -le 2 ]]; then
        emit_result '{"action":"dispatch","jobs":[{"lane":"code-fast","mode":"work","task":"invalid lane request"}]}'
      else
        emit_result '{"action":"done","summary":"invalid lane corrected"}'
      fi
      ;;
    launch-failure)
      case "$turn" in
        1) emit_result '{"action":"dispatch","jobs":[{"lane":"broken","mode":"work","task":"launch must fail"}]}' ;;
        2) emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"work","task":"budget remains available"}]}' ;;
        *) emit_result '{"action":"done","summary":"launch failure did not consume budget"}' ;;
      esac
      ;;
    planner-dies)
      if [[ "$turn" -eq 1 ]]; then
        emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"work","task":"planner dies after worker"}]}'
      else
        exit 142
      fi
      ;;
    invalid)
      emit_result 'this is not JSON'
      ;;
    budget)
      if [[ "$line" == *"budget exhausted, summarize now"* ]]; then
        emit_result '{"action":"done","summary":"budget summary"}'
      else
        emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"advise","task":"consume one slot"}]}'
      fi
      ;;
    budget-seconds)
      if [[ "$line" == *"budget exhausted, summarize now"* ]]; then
        emit_result '{"action":"done","summary":"time budget summary"}'
      else
        sleep 2
        emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"work","task":"too late"}]}'
      fi
      ;;
    abort)
      emit_result '{"action":"abort","reason":"planner stopped intentionally"}'
      ;;
    depth)
      if [[ ! -e "${NESTED_RC_FILE:?}" ]]; then
        set +e
        "${NESTED_ROOT:?}/scripts/dispatch.sh" worker "nested attempt" \
          >"$NESTED_RC_FILE.out" 2>&1
        nested_rc=$?
        set -e
        printf '%s\n' "$nested_rc" > "$NESTED_RC_FILE"
      fi
      emit_result '{"action":"done","summary":"depth guard observed"}'
      ;;
    *)
      exit 91
      ;;
  esac
done
EOF

  cat > "$worker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# exec runner signature: MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE
task="$(cat "$4")"
printf '%s|%s|%s\n' "$1" "$2" "$task" >> "${FAKE_WORKER_CALLS:?}"
case "$task" in
  *'slow task'*) sleep 1 ;;
  *'fast task'*) sleep 0.1 ;;
  *'always fail'*)
    printf 'failed %s\n' "$task" > "$5"
    exit 7
    ;;
esac
printf 'completed %s\n' "$task" > "$5"
EOF

  chmod +x "$planner" "$worker"
  printf 'hardest-coding: claude claude-default high\n' > "$home/routing.local.yaml"
  printf 'worker: exec "%s" -\n' "$worker" >> "$home/routing.local.yaml"
  printf 'broken: exec "%s" -\n' "$home/missing-worker.sh" >> "$home/routing.local.yaml"
}

run_goal() {
  local home="$1" scenario="$2"; shift 2
  OMNILANE_HOME="$home" CLAUDE_BIN="$home/bin/claude" \
    PLANNER_SCENARIO="$scenario" FAKE_PLANNER_INPUT="$home/planner.input" \
    FAKE_WORKER_CALLS="$home/worker.calls" NESTED_RC_FILE="$home/nested.rc" \
    NESTED_ROOT="$ROOT" \
    "$ROOT/bin/omnilane" goal "fixture goal" --workdir "$home/work" "$@"
}

only_goal_dir() {
  local home="$1" count
  count="$(find "$home/goals" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d '[:space:]')"
  [[ "$count" == "1" ]] || fail "expected one goal state directory, got $count"
  find "$home/goals" -mindepth 1 -maxdepth 1 -type d -print
}

case_dispatch_done() {
  local home="$TEST_ROOT/dispatch-done" goal_dir goal_id status_out state_before state_after
  mkdir -p "$home"
  make_fixture "$home" dispatch-done
  run_goal "$home" dispatch-done --budget-jobs 3 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "goal dispatch/done failed: $(cat "$home/err")"

  goal_dir="$(only_goal_dir "$home")"
  goal_id="${goal_dir##*/}"
  grep -Fxq 'fixture goal complete' "$goal_dir/summary.txt" ||
    fail "final summary was not recorded"
  [[ "$(wc -l < "$home/worker.calls" | tr -d '[:space:]')" == "1" ]] ||
    fail "worker was not dispatched exactly once"
  grep -Fq '"status":"done"' "$goal_dir/budget.json" ||
    fail "done state missing from budget.json"
  [[ -f "$goal_dir/rounds/0001/action.json" && -f "$goal_dir/rounds/0002/action.json" ]] ||
    fail "per-round action records missing"
  [[ -f "$goal_dir/rounds/0001/job-0001.json" ]] ||
    fail "worker completion record missing"
  [[ "$(stat -f '%Lp' "$goal_dir" 2>/dev/null || stat -c '%a' "$goal_dir")" == "700" ]] ||
    fail "goal directory mode is not 0700"
  [[ "$(stat -f '%Lp' "$goal_dir/goal.txt" 2>/dev/null || stat -c '%a' "$goal_dir/goal.txt")" == "600" ]] ||
    fail "goal.txt mode is not 0600"
  [[ -z "$(find "$goal_dir" -type d ! -perm 700 -print -quit)" ]] ||
    fail "goal state contains a directory not mode 0700"
  [[ -z "$(find "$goal_dir" -type f ! -perm 600 -print -quit)" ]] ||
    fail "goal state contains a file not mode 0600"

  state_before="$(cksum "$goal_dir/budget.json")"
  status_out="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id")" ||
    fail "goal status command failed"
  state_after="$(cksum "$goal_dir/budget.json")"
  [[ "$state_before" == "$state_after" ]] || fail "goal status mutated budget state"
  [[ "$status_out" == *"status: done"* && "$status_out" == *"jobs: 1/3"* &&
     "$status_out" == *"last action: done"* ]] ||
    fail "goal status output incomplete: $status_out"
}

case_invalid_lane() {
  local home="$TEST_ROOT/invalid-lane" goal_dir
  mkdir -p "$home"
  make_fixture "$home" invalid-lane
  run_goal "$home" invalid-lane --budget-jobs 1 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "invalid-lane goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"

  grep -Fxq 'invalid lane corrected' "$goal_dir/summary.txt" ||
    fail "invalid-lane goal did not continue to completion"
  [[ ! -s "$home/worker.calls" ]] || fail "invalid lane was dispatched"
  grep -Fq 'EFFECTIVE LANE LIST' "$home/planner.input" ||
    fail "initial planner brief omitted effective lane list"
  grep -Fq 'worker:' "$home/planner.input" ||
    fail "initial planner brief omitted worker lane"
  grep -Fq "Invalid lane 'code-fast'. Valid lanes:" "$home/planner.input" ||
    fail "planner did not receive invalid-lane corrective notice"
  grep -Fq '"spent_jobs":0' "$goal_dir/budget.json" ||
    fail "invalid lane consumed budget-jobs"
  grep -Fq '"fuse_trips":1' "$goal_dir/budget.json" ||
    fail "second invalid lane request did not trip fuse"
}

case_launch_failure() {
  local home="$TEST_ROOT/launch-failure" goal_dir calls
  mkdir -p "$home"
  make_fixture "$home" launch-failure
  run_goal "$home" launch-failure --budget-jobs 1 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "launch-failure goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"

  grep -Fxq 'launch failure did not consume budget' "$goal_dir/summary.txt" ||
    fail "launch-failure goal did not finish"
  calls="$(wc -l < "$home/worker.calls" | tr -d '[:space:]')"
  [[ "$calls" == "1" ]] || fail "successful worker did not retain sole budget slot: $calls"
  grep -Fq 'dispatch failed' "$home/planner.input" ||
    fail "planner did not receive launch-failure report"
  grep -Fq '"spent_jobs":1' "$goal_dir/budget.json" ||
    fail "launch failure was charged against budget-jobs"
}

case_planner_timeout() {
  local home="$TEST_ROOT/planner-timeout" goal_dir planner_id meta
  mkdir -p "$home"
  make_fixture "$home" dispatch-done
  run_goal "$home" dispatch-done --budget-jobs 3 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "planner-timeout goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  planner_id="$(cat "$goal_dir/planner-job-id")"
  meta="$home/jobs/$planner_id/meta.json"
  [[ -f "$meta" ]] || fail "planner metadata missing: $meta"
  python3 - "$meta" <<'PY' || fail "planner timeout metadata is not derived from goal budget"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    meta = json.load(handle)
assert meta["timeout"] == 330, meta
assert meta["idle_timeout"] == 0, meta
PY
}

case_planner_dies() {
  local home="$TEST_ROOT/planner-dies" goal_dir rc=0
  mkdir -p "$home"
  make_fixture "$home" planner-dies
  run_goal "$home" planner-dies --budget-jobs 2 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "dead planner returned success"
  goal_dir="$(only_goal_dir "$home")"
  grep -Fq 'planner exit code 142' "$goal_dir/summary.txt" ||
    fail "dead planner summary omitted recorded exit code"
  grep -Fq "job dir: $home/jobs/" "$goal_dir/summary.txt" ||
    fail "dead planner summary omitted job directory pointer"
}

case_invalid() {
  local home="$TEST_ROOT/invalid" goal_dir rc=0 turns
  mkdir -p "$home"
  make_fixture "$home" invalid
  run_goal "$home" invalid --budget-jobs 2 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "invalid planner reply did not abort"
  turns="$(wc -l < "$home/planner.input" | tr -d '[:space:]')"
  [[ "$turns" == "2" ]] || fail "invalid reply reprompt count wrong: $turns planner turns"
  goal_dir="$(only_goal_dir "$home")"
  grep -Fq '"status":"aborted"' "$goal_dir/budget.json" ||
    fail "invalid reply did not persist aborted state"
  grep -Fq 'validator error' "$goal_dir/summary.txt" ||
    fail "invalid reply diagnostic missing"
}

case_multi() {
  local home="$TEST_ROOT/multi" goal_dir first second
  mkdir -p "$home"
  make_fixture "$home" multi
  run_goal "$home" multi --budget-jobs 4 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "multi-job goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  [[ "$(wc -l < "$home/worker.calls" | tr -d '[:space:]')" == "2" ]] ||
    fail "multi-job action did not dispatch exactly two workers"
  first="$(sed -n '1p' "$home/worker.calls")"
  second="$(sed -n '2p' "$home/worker.calls")"
  [[ "$first" == advise\|*\|first\ task && "$second" == work\|*\|second\ task ]] ||
    fail "multi-job dispatch order or mode wrong: $first / $second"
  grep -Fxq 'two workers complete' "$goal_dir/summary.txt" ||
    fail "multi-job summary missing"
  [[ -f "$goal_dir/rounds/0001/job-0001.json" &&
     -f "$goal_dir/rounds/0001/job-0002.json" ]] ||
    fail "multi-job completion records missing"
}

case_parallel_two() {
  local home="$TEST_ROOT/parallel-two" goal_dir fast_line slow_line records
  mkdir -p "$home"
  make_fixture "$home" parallel
  run_goal "$home" parallel --budget-jobs 4 --budget-seconds 30 --budget-parallel 2 \
    >"$home/out" 2>"$home/err" || fail "parallel=2 goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  [[ "$(wc -l < "$home/worker.calls" | tr -d '[:space:]')" == "2" ]] ||
    fail "parallel=2 did not dispatch both workers"
  records="$(grep -c 'BEGIN WORKER COMPLETION DATA' "$home/planner.input")"
  [[ "$records" == "2" ]] || fail "planner received $records completion records instead of 2"
  fast_line="$(grep -nF 'completed fast task' "$home/planner.input" | head -1 | cut -d: -f1)"
  slow_line="$(grep -nF 'completed slow task' "$home/planner.input" | head -1 | cut -d: -f1)"
  [[ -n "$fast_line" && -n "$slow_line" && "$fast_line" -lt "$slow_line" ]] ||
    fail "parallel=2 did not report finish order: fast=$fast_line slow=$slow_line"
  [[ -f "$goal_dir/rounds/0001/job-0001.json" &&
     -f "$goal_dir/rounds/0001/job-0002.json" ]] ||
    fail "parallel=2 completion records missing"
}

case_parallel_one() {
  local home="$TEST_ROOT/parallel-one" slow_line fast_line
  mkdir -p "$home"
  make_fixture "$home" parallel
  run_goal "$home" parallel --budget-jobs 4 --budget-seconds 30 --budget-parallel 1 \
    >"$home/out" 2>"$home/err" || fail "parallel=1 goal failed: $(cat "$home/err")"
  slow_line="$(grep -nF 'completed slow task' "$home/planner.input" | head -1 | cut -d: -f1)"
  fast_line="$(grep -nF 'completed fast task' "$home/planner.input" | head -1 | cut -d: -f1)"
  [[ -n "$slow_line" && -n "$fast_line" && "$slow_line" -lt "$fast_line" ]] ||
    fail "parallel=1 did not preserve sequential order: slow=$slow_line fast=$fast_line"
}

case_fuse() {
  local home="$TEST_ROOT/fuse" goal_dir calls
  mkdir -p "$home"
  make_fixture "$home" fuse
  run_goal "$home" fuse --budget-jobs 6 --budget-seconds 30 --budget-parallel 2 \
    >"$home/out" 2>"$home/err" || fail "fuse goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  calls="$(wc -l < "$home/worker.calls" | tr -d '[:space:]')"
  [[ "$calls" == "2" ]] || fail "fuse dispatched unchanged failing job $calls times"
  grep -Fq 'BEGIN FAILURE FUSE NOTICE DATA' "$home/planner.input" ||
    fail "planner did not receive failure fuse notice"
  grep -Fxq 'fuse stopped retry loop' "$goal_dir/summary.txt" ||
    fail "goal did not continue after fuse trip"
  grep -Fq '"fuse_trips":1' "$goal_dir/budget.json" || fail "fuse trip not persisted"
}

case_status_p2() {
  local home="$TEST_ROOT/status-p2" goal_dir goal_id status_out job_lines
  mkdir -p "$home"
  make_fixture "$home" fuse
  run_goal "$home" fuse --budget-jobs 6 --budget-seconds 30 --budget-parallel 3 \
    >"$home/out" 2>"$home/err" || fail "status fixture goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  goal_id="${goal_dir##*/}"
  status_out="$(OMNILANE_HOME="$home" "$ROOT/bin/omnilane" goal status "$goal_id")" ||
    fail "P2 goal status failed"
  [[ "$status_out" == *"parallel: 3"* && "$status_out" == *"fuse trips: 1"* ]] ||
    fail "P2 status settings missing: $status_out"
  job_lines="$(printf '%s\n' "$status_out" | grep -c '^job ')"
  [[ "$job_lines" == "2" ]] || fail "P2 status reported $job_lines jobs instead of 2"
  printf '%s\n' "$status_out" | grep -Eq '^job .+: lane=worker vendor=exec exit=7 seconds=[0-9]+$' ||
    fail "P2 status per-job fields missing: $status_out"
}

case_budget_jobs() {
  local home="$TEST_ROOT/budget" goal_dir calls
  mkdir -p "$home"
  make_fixture "$home" budget
  run_goal "$home" budget --budget-jobs 2 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "budget goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  calls="$(wc -l < "$home/worker.calls" | tr -d '[:space:]')"
  [[ "$calls" == "2" ]] || fail "budget-jobs cap dispatched $calls jobs instead of 2"
  grep -Fxq 'budget summary' "$goal_dir/summary.txt" ||
    fail "forced budget summary missing"
  grep -Fq '"status":"budget_exhausted"' "$goal_dir/budget.json" ||
    fail "budget exhaustion state missing"
  grep -Fq 'budget exhausted, summarize now' "$home/planner.input" ||
    fail "planner never received forced summarize turn"
  grep -Fq '75%' "$home/planner.input" ||
    fail "planner never received 75% budget warning"
}

case_budget_seconds() {
  local home="$TEST_ROOT/budget-seconds" goal_dir
  mkdir -p "$home"
  make_fixture "$home" budget-seconds
  run_goal "$home" budget-seconds --budget-jobs 4 --budget-seconds 1 \
    >"$home/out" 2>"$home/err" || fail "time-budget goal failed: $(cat "$home/err")"
  goal_dir="$(only_goal_dir "$home")"
  [[ ! -s "$home/worker.calls" ]] || fail "worker started after wall-clock budget expired"
  grep -Fxq 'time budget summary' "$goal_dir/summary.txt" ||
    fail "time-budget summary missing"
  grep -Fq '"status":"budget_exhausted"' "$goal_dir/budget.json" ||
    fail "time-budget exhaustion state missing"
  grep -Fq 'budget exhausted, summarize now' "$home/planner.input" ||
    fail "time-budget forced summarize turn missing"
}

case_abort() {
  local home="$TEST_ROOT/abort" goal_dir rc=0
  mkdir -p "$home"
  make_fixture "$home" abort
  run_goal "$home" abort --budget-jobs 2 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "planner abort action returned success"
  goal_dir="$(only_goal_dir "$home")"
  grep -Fxq 'planner stopped intentionally' "$goal_dir/summary.txt" ||
    fail "abort reason missing from summary"
  grep -Fq '"status":"aborted"' "$goal_dir/budget.json" ||
    fail "abort state missing"
  grep -Fq '"last_action":"abort"' "$goal_dir/budget.json" ||
    fail "abort last action missing"
}

case_depth_guard() {
  local home="$TEST_ROOT/depth"
  mkdir -p "$home"
  make_fixture "$home" depth
  run_goal "$home" depth --budget-jobs 2 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" || fail "depth goal failed: $(cat "$home/err")"
  wait_for_file "$home/nested.rc" || fail "planner did not attempt nested dispatch"
  [[ "$(cat "$home/nested.rc")" == "86" ]] ||
    fail "nested planner dispatch did not preserve exit 86: $(cat "$home/nested.rc")"
  grep -Fq 'refusing nested dispatch' "$home/nested.rc.out" ||
    fail "depth guard diagnostic missing"
}

case_worker_single_shot() {
  local home="$TEST_ROOT/worker-single-shot" modes
  mkdir -p "$home"
  make_fixture "$home" worker-single-shot
  run_goal "$home" worker-single-shot --budget-jobs 2 --budget-seconds 30 \
    >"$home/out" 2>"$home/err" ||
    fail "worker single-shot goal failed: $(cat "$home/err")"

  modes="$(python3 - "$home/jobs" <<'PY'
import glob
import json
import os
import sys

modes = []
for path in glob.glob(os.path.join(sys.argv[1], "*", "meta.json")):
    with open(path, encoding="utf-8") as handle:
        meta = json.load(handle)
    if meta.get("vendor") == "claude":
        modes.append(meta.get("session_mode"))
print(" ".join(sorted(modes)))
PY
)"
  [[ "$modes" == "live single-shot" ]] ||
    fail "planner/worker session modes were '$modes', expected 'live single-shot'"
}

case "$CASE" in
  dispatch-done) case_dispatch_done ;;
  worker-single-shot) case_worker_single_shot ;;
  multi) case_multi ;;
  parallel-two) case_parallel_two ;;
  parallel-one) case_parallel_one ;;
  fuse) case_fuse ;;
  status-p2) case_status_p2 ;;
  invalid-lane) case_invalid_lane ;;
  launch-failure) case_launch_failure ;;
  planner-timeout) case_planner_timeout ;;
  planner-dies) case_planner_dies ;;
  invalid) case_invalid ;;
  budget-jobs) case_budget_jobs ;;
  budget-seconds) case_budget_seconds ;;
  abort) case_abort ;;
  depth-guard) case_depth_guard ;;
  *) fail "usage: test_goal_loop.sh dispatch-done|worker-single-shot|multi|parallel-two|parallel-one|fuse|status-p2|invalid-lane|launch-failure|planner-timeout|planner-dies|invalid|budget-jobs|budget-seconds|abort|depth-guard" ;;
esac
