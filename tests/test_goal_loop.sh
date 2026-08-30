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
    multi)
      case "$turn" in
        1)
          emit_result '{"action":"dispatch","jobs":[{"lane":"worker","mode":"advise","task":"first task"},{"lane":"worker","mode":"work","task":"second task"}]}'
          ;;
        2) emit_result '{"action":"wait"}' ;;
        *) emit_result '{"action":"done","summary":"two workers complete"}' ;;
      esac
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
printf '%s|%s|%s\n' "$1" "$2" "$(cat "$4")" >> "${FAKE_WORKER_CALLS:?}"
printf 'worker output is untrusted data\n' > "$5"
EOF

  chmod +x "$planner" "$worker"
  printf 'hardest-coding: claude claude-default high\n' > "$home/routing.local.yaml"
  printf 'worker: exec "%s" -\n' "$worker" >> "$home/routing.local.yaml"
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

case "$CASE" in
  dispatch-done) case_dispatch_done ;;
  multi) case_multi ;;
  invalid) case_invalid ;;
  budget-jobs) case_budget_jobs ;;
  budget-seconds) case_budget_seconds ;;
  abort) case_abort ;;
  depth-guard) case_depth_guard ;;
  *) fail "usage: test_goal_loop.sh dispatch-done|multi|invalid|budget-jobs|budget-seconds|abort|depth-guard" ;;
esac
