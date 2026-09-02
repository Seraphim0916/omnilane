#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_SESSION_MODE OMNILANE_THREAD_MODE OMNILANE_THREAD_ID
unset CLAUDE_BIN CODEX_BIN
export CLAUDE_CODE_SESSION_ID="thread-test-foreman"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-thread-tests.XXXXXX")"

cleanup() {
  /bin/rm -r -- "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf 'not ok - threaded dispatch: %s\n' "$1" >&2
  exit 1
}

assert_state() {
  local path="$1" expected_name="$2" expected_model="$3"
  local expected_session="$4" expected_turns="$5"
  THREAD_PATH="$path" EXPECTED_NAME="$expected_name" EXPECTED_MODEL="$expected_model" \
    EXPECTED_SESSION="$expected_session" EXPECTED_TURNS="$expected_turns" \
    python3 - <<'PY' || fail "thread state mismatch: $path"
import json
import os
import pathlib

path = pathlib.Path(os.environ["THREAD_PATH"])
state = json.loads(path.read_text(encoding="utf-8"))
assert state["name"] == os.environ["EXPECTED_NAME"]
assert state["vendor"] == "claude"
assert state["model"] == os.environ["EXPECTED_MODEL"]
assert state["effort"] == "low"
assert state["workdir"] == str(pathlib.Path.cwd().resolve())
assert state["session_id"] == os.environ["EXPECTED_SESSION"]
assert state["turns"] == int(os.environ["EXPECTED_TURNS"])
assert state["last_job_id"]
assert state["created"].endswith("Z")
assert state["updated"].endswith("Z")
PY
}

HOME_DIR="$TEST_ROOT/home"
BIN_DIR="$TEST_ROOT/bin"
mkdir -p "$HOME_DIR" "$BIN_DIR"
printf 'consult: claude fake-thread-model low | codex fake-codex low\n' \
  > "$HOME_DIR/routing.local.yaml"

cat > "$BIN_DIR/claude" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >> "${FAKE_CLAUDE_ARGV_LOG:?}"
printf '\n' >> "$FAKE_CLAUDE_ARGV_LOG"

session_id=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id|--resume)
      session_id="$2"
      shift 2
      ;;
    *) shift ;;
  esac
done

if [[ -f "${FAKE_CLAUDE_FAIL_FILE:?}" ]]; then
  echo 'fake claude: stored session cannot be resumed' >&2
  exit 17
fi
if [[ -s "${FAKE_CLAUDE_RETURN_FILE:?}" ]]; then
  session_id="$(cat "$FAKE_CLAUDE_RETURN_FILE")"
fi
SESSION_ID="$session_id" python3 - <<'PY'
import json
import os
print(json.dumps({"type": "result", "result": "fake threaded answer", "session_id": os.environ["SESSION_ID"]}))
PY
EOF
chmod +x "$BIN_DIR/claude"

cat > "$BIN_DIR/codex" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$BIN_DIR/codex"

export FAKE_CLAUDE_ARGV_LOG="$TEST_ROOT/claude.argv"
export FAKE_CLAUDE_RETURN_FILE="$TEST_ROOT/return-session"
export FAKE_CLAUDE_FAIL_FILE="$TEST_ROOT/fail-resume"
export PATH="$BIN_DIR:$PATH"

STATE="$HOME_DIR/threads/orchard.json"
OUT1="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread orchard --vendor claude --model fake-thread-model --effort low \
  --single-shot consult 'turn one')" || fail "first turn failed"
[[ "$OUT1" == *"omnilane: thread orchard turn 1 (claude session "*", new)"* ]] \
  || fail "first turn visibility missing: $OUT1"
SESSION1="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$STATE")"
assert_state "$STATE" orchard fake-thread-model "$SESSION1" 1
[[ "$(stat -f '%Lp' "$HOME_DIR/threads")" == "700" ]] || fail "thread store mode is not 700"
[[ "$(stat -f '%Lp' "$STATE")" == "600" ]] || fail "thread state mode is not 600"
FIRST_ARGV="$(sed -n '1p' "$FAKE_CLAUDE_ARGV_LOG")"
[[ "$FIRST_ARGV" == *"--session-id $SESSION1"* ]] || fail "first turn omitted --session-id"
[[ "$FIRST_ARGV" != *"--resume"* ]] || fail "first turn unexpectedly resumed"

OUT2="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread orchard --vendor claude --model fake-thread-model --effort low \
  --single-shot consult 'turn two')" || fail "second turn failed"
[[ "$OUT2" == *"omnilane: thread orchard turn 2 (claude session $SESSION1, resume)"* ]] \
  || fail "second turn visibility missing: $OUT2"
SECOND_ARGV="$(sed -n '2p' "$FAKE_CLAUDE_ARGV_LOG")"
[[ "$SECOND_ARGV" == *"--resume $SESSION1"* ]] || fail "second turn omitted --resume"
[[ "$SECOND_ARGV" != *"--session-id"* ]] || fail "resume also passed --session-id"
assert_state "$STATE" orchard fake-thread-model "$SESSION1" 2

SESSION2="11111111-2222-4333-8444-555555555555"
printf '%s\n' "$SESSION2" > "$FAKE_CLAUDE_RETURN_FILE"
OUT3="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread orchard --vendor claude --model fake-thread-model --effort low \
  --single-shot consult 'turn three')" || fail "third turn failed"
[[ "$OUT3" == *"claude returned a different session id ($SESSION1 -> $SESSION2)"* ]] \
  || fail "session-id replacement notice missing: $OUT3"
assert_state "$STATE" orchard fake-thread-model "$SESSION2" 3
rm "$FAKE_CLAUDE_RETURN_FILE"

LAST_JOB="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["last_job_id"])' "$STATE")"
META="$HOME_DIR/jobs/$LAST_JOB/meta.json" RECORD="$HOME_DIR/inbox/$LAST_JOB.json" \
  python3 - <<'PY' || fail "thread fields missing from meta or completion record"
import json
import os

for key in ("META", "RECORD"):
    with open(os.environ[key], encoding="utf-8") as handle:
        value = json.load(handle)
    assert value["thread"] == "orchard"
    assert value["thread_turn"] == 3
PY
REPORT="$(OMNILANE_HOME="$HOME_DIR" CLAUDE_PROJECT_DIR="$ROOT" \
  "$ROOT/hooks/report-completions.sh" <<'EOF'
{"session_id":"thread-test-foreman"}
EOF
)"
[[ "$REPORT" == *"Omnilane completion:"*"thread=orchard turn=3"* ]] \
  || fail "completion report omitted thread visibility: $REPORT"

set +e
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" --dry-run --thread foreign \
  --vendor codex consult x >"$TEST_ROOT/nonclaude.out" 2>"$TEST_ROOT/nonclaude.err"
RC=$?
set -e
EXPECTED="omnilane: --thread is claude-only in this release; resolved vendor 'codex' cannot continue a thread"
[[ "$RC" -eq 2 ]] || fail "non-Claude refusal exit was $RC"
grep -Fxq "$EXPECTED" "$TEST_ROOT/nonclaude.out" || fail "non-Claude stdout notice missing"
grep -Fxq "$EXPECTED" "$TEST_ROOT/nonclaude.err" || fail "non-Claude stderr notice missing"

set +e
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" --thread orchard \
  --vendor claude --model another-model --effort low --single-shot consult x \
  >"$TEST_ROOT/mismatch.out" 2>"$TEST_ROOT/mismatch.err"
RC=$?
set -e
[[ "$RC" -eq 2 ]] || fail "pinned model mismatch exit was $RC"
grep -q "pinned model 'fake-thread-model'.*resolved model is 'another-model'" \
  "$TEST_ROOT/mismatch.out" || fail "pinned mismatch notice omitted both values"
cmp -s "$TEST_ROOT/mismatch.out" "$TEST_ROOT/mismatch.err" \
  || fail "pinned mismatch stdout/stderr notices differ"

set +e
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" --dry-run --thread live-one \
  --live --background --vendor claude consult x \
  >"$TEST_ROOT/live.out" 2>"$TEST_ROOT/live.err"
RC=$?
set -e
[[ "$RC" -eq 2 ]] || fail "--thread plus --live exit was $RC"
cmp -s "$TEST_ROOT/live.out" "$TEST_ROOT/live.err" \
  || fail "--thread plus --live stdout/stderr notices differ"

LIST="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/jobs.sh" threads)" \
  || fail "jobs threads list failed"
[[ "$LIST" == *"orchard"*"claude"*"fake-thread-model"*"3"* ]] \
  || fail "jobs threads list missing fields: $LIST"
SHOW="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/jobs.sh" threads show orchard)" \
  || fail "jobs threads show failed"
SHOW_JSON="$SHOW" python3 - <<'PY' || fail "jobs threads show was not state JSON"
import json, os
assert json.loads(os.environ["SHOW_JSON"])["name"] == "orchard"
PY
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/jobs.sh" --json threads \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["schema_version"] == 1 and d["threads"][0]["name"] == "orchard"' \
  || fail "jobs threads --json list invalid"
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/jobs.sh" threads show orchard --json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["schema_version"] == 1 and d["thread"]["name"] == "orchard"' \
  || fail "jobs threads show --json invalid"

BG_OUT="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" --background \
  --thread background --vendor claude --model fake-thread-model --effort low \
  --single-shot consult 'background turn')" || fail "background thread dispatch failed"
BG_ID="$(printf '%s\n' "$BG_OUT" | tail -1)"
[[ "$BG_OUT" == *"omnilane: thread background turn 1 (claude session "*", new)"* ]] \
  || fail "background thread visibility missing: $BG_OUT"
[[ "$BG_ID" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$ ]] \
  || fail "background dispatch did not return job id: $BG_OUT"
for _ in {1..100}; do
  [[ -f "$HOME_DIR/threads/background.json" ]] && break
  sleep 0.1
done
BG_STATE="$HOME_DIR/threads/background.json"
[[ -f "$BG_STATE" ]] || fail "background worker did not write thread state"
BG_SESSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$BG_STATE")"
assert_state "$BG_STATE" background fake-thread-model "$BG_SESSION" 1
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/jobs.sh" threads rm background >/dev/null \
  || fail "jobs threads rm failed for background thread"

touch "$FAKE_CLAUDE_FAIL_FILE"
set +e
FAIL_OUT="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread orchard --vendor claude --model fake-thread-model --effort low \
  --single-shot consult 'failed resume' 2>&1)"
RC=$?
set -e
rm "$FAKE_CLAUDE_FAIL_FILE"
[[ "$RC" -eq 17 ]] || fail "failed resume exit was $RC"
[[ "$FAIL_OUT" == *"fake claude: stored session cannot be resumed"* ]] \
  || fail "Claude resume error was hidden"
[[ "$FAIL_OUT" == *"thread orchard could not be continued"* ]] \
  || fail "thread continuation failure notice missing"
assert_state "$STATE" orchard fake-thread-model "$SESSION2" 3

OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/jobs.sh" threads rm orchard >/dev/null \
  || fail "jobs threads rm failed"
[[ ! -e "$STATE" ]] || fail "jobs threads rm retained state"

set +e
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" --thread '../bad' \
  --vendor claude --single-shot consult x >"$TEST_ROOT/name.out" 2>&1
RC=$?
set -e
[[ "$RC" -eq 2 ]] || fail "invalid thread name exit was $RC"
[[ ! -e "$TEST_ROOT/bad.json" ]] || fail "invalid thread name escaped store"

printf 'ok - threaded dispatch state resume refusals and jobs CLI\n'
