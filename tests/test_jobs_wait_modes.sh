#!/usr/bin/env bash
# jobs wait must block until the job is terminal or the caller's timeout, never
# end silently while the job runs, and explain the unsupported --json form.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-jobs-wait.XXXXXX")"
mkdir -p "$HOME_DIR/jobs"
JOBS_SH="$ROOT/scripts/jobs.sh"
failures=0
fail() { echo "FAIL $*" >&2; failures=$((failures + 1)); }
pass() { echo "PASS $*"; }

# new_job ID SECONDS EXIT: a fake background worker that records EXIT after SECONDS.
WORKERS=()
new_job() {
  local id="$1" seconds="$2" code="$3"
  mkdir -p "$HOME_DIR/jobs/$id"
  (sleep "$seconds"; printf '%s\n' "$code" > "$HOME_DIR/jobs/$id/exit") &
  WORKERS+=("$!")
  printf '%s\n' "$!" > "$HOME_DIR/jobs/$id/pid"
}
cleanup() {
  local pid
  for pid in ${WORKERS[@]+"${WORKERS[@]}"}; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

# 1. wait blocks while the job runs and returns its status when it finishes.
new_job 20261109-190001-1-1 3 3
start=$SECONDS
out="$(OMNILANE_HOME="$HOME_DIR" bash "$JOBS_SH" wait 20261109-190001-1-1 --timeout 30 2>&1)"; rc=$?
elapsed=$((SECONDS - start))
if [[ "$rc" -eq 3 && "$out" == "done exit=3" && "$elapsed" -ge 2 ]]; then
  pass "wait blocks until the job finishes (rc=$rc elapsed=${elapsed}s)"
else
  fail "wait did not block until the job finished: rc=$rc elapsed=$elapsed out=$out"
fi

# 2. --json before or after wait is refused with the supported form spelled out.
new_job 20261109-190002-1-2 30 0
for form in "--json wait 20261109-190002-1-2" "wait 20261109-190002-1-2 --json"; do
  # shellcheck disable=SC2086
  out="$(OMNILANE_HOME="$HOME_DIR" bash "$JOBS_SH" $form 2>&1)"; rc=$?
  if [[ "$rc" -eq 2 ]] && python3 -c '
import json, sys
d = json.loads(sys.argv[1])
assert d["command"] == "wait" and d["ok"] is False, d
assert "no --json mode" in d["error"] and "omnilane jobs wait ID [--timeout N]" in d["error"], d
assert "omnilane jobs --json status ID" in d["error"], d
' "$out"; then
    pass "'$form' is refused with the supported usage"
  else
    fail "'$form': rc=$rc out=$out"
  fi
done

# 3. A timeout shorter than the job ends with 124 and says so.
out="$(OMNILANE_HOME="$HOME_DIR" bash "$JOBS_SH" wait 20261109-190002-1-2 --timeout 1 2>&1)"; rc=$?
if [[ "$rc" -eq 124 && "$out" == "wait timeout after 1s" ]]; then
  pass "timeout before the job ends gives 124 with a reason"
else
  fail "timeout: rc=$rc out=$out"
fi

# 4. A killed waiter says it was interrupted and leaves the job running.
for spec in TERM:143 HUP:129; do
  signal="${spec%%:*}"; expected="${spec##*:}"
  OMNILANE_HOME="$HOME_DIR" bash "$JOBS_SH" wait 20261109-190002-1-2 --timeout 30 > "$HOME_DIR/sig.out" 2>&1 &
  waiter=$!
  sleep 1.5
  kill "-$signal" "$waiter"
  wait "$waiter"; rc=$?
  out="$(cat "$HOME_DIR/sig.out")"
  worker="$(cat "$HOME_DIR/jobs/20261109-190002-1-2/pid")"
  if [[ "$rc" -eq "$expected" && "$out" == *"wait interrupted by SIG$signal before job 20261109-190002-1-2 finished"* ]] &&
     kill -0 "$worker" 2>/dev/null && [[ ! -e "$HOME_DIR/jobs/20261109-190002-1-2/exit" ]]; then
    pass "SIG$signal on the waiter reports the interruption (rc=$rc) and the job keeps running"
  else
    fail "SIG$signal: rc=$rc out=$out"
  fi
done

if [[ "$failures" -eq 0 ]]; then
  echo "PASS test_jobs_wait_modes.sh"
else
  echo "FAIL test_jobs_wait_modes.sh ($failures)"
  exit 1
fi
