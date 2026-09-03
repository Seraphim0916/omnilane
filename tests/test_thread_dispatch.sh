#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_SESSION_MODE OMNILANE_THREAD_MODE OMNILANE_THREAD_ID
unset CLAUDE_BIN CODEX_BIN GROK_BIN AGY_BIN
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

# Lines an operator sees: captured stdout plus the stderr file of the same run.
count_visible() {
  local stdout_text="$1" stderr_file="$2" needle="$3"
  { printf '%s\n' "$stdout_text"; cat "$stderr_file"; } | grep -c -F -- "$needle" || true
}

assert_state() {
  local path="$1" expected_name="$2" expected_model="$3"
  local expected_session="$4" expected_turns="$5" expected_vendor="${6:-claude}"
  THREAD_PATH="$path" EXPECTED_NAME="$expected_name" EXPECTED_MODEL="$expected_model" \
    EXPECTED_SESSION="$expected_session" EXPECTED_TURNS="$expected_turns" \
    EXPECTED_VENDOR="$expected_vendor" \
    python3 - <<'PY' || fail "thread state mismatch: $path"
import json
import os
import pathlib

path = pathlib.Path(os.environ["THREAD_PATH"])
state = json.loads(path.read_text(encoding="utf-8"))
assert state["name"] == os.environ["EXPECTED_NAME"]
assert state["vendor"] == os.environ["EXPECTED_VENDOR"]
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
printf 'consult: claude fake-thread-model low | codex fake-codex low | grok fake-grok low | gemini fake-gemini low\n' \
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
set -euo pipefail

printf '%q ' "$@" >> "${FAKE_CODEX_ARGV_LOG:?}"
printf '\n' >> "$FAKE_CODEX_ARGV_LOG"

# Real `codex exec resume` (codex-cli 0.153.0) has no -s/--sandbox and exits 2.
if [[ "${1:-}" == "exec" && "${2:-}" == "resume" ]]; then
  for arg in "$@"; do
    if [[ "$arg" == "-s" || "$arg" == "--sandbox" ]]; then
      printf "error: unexpected argument '-s' found\n\n" >&2
      printf "  tip: to pass '-s' as a value, use '-- -s'\n\n" >&2
      printf 'Usage: codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]\n' >&2
      exit 2
    fi
  done
fi

output_file=""
resume_id=""
resume_mode=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    resume) resume_mode=1; shift ;;
    -o) output_file="$2"; shift 2 ;;
    *)
      if [[ "$resume_mode" -eq 1 && "$1" =~ ^[0-9A-Fa-f-]{36}$ ]]; then
        resume_id="$1"
      fi
      shift
      ;;
  esac
done

session_id="${resume_id:-22222222-2222-4222-8222-222222222222}"
if [[ -s "${FAKE_CODEX_RETURN_FILE:?}" ]]; then
  session_id="$(cat "$FAKE_CODEX_RETURN_FILE")"
fi
printf 'fake codex answer\n' > "$output_file"
printf '{"type":"thread.started","thread_id":"%s"}\n' "$session_id"
EOF
chmod +x "$BIN_DIR/codex"

cat > "$BIN_DIR/grok" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >> "${FAKE_GROK_ARGV_LOG:?}"
printf '\n' >> "$FAKE_GROK_ARGV_LOG"

session_id=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id|--resume) session_id="$2"; shift 2 ;;
    *) shift ;;
  esac
done

returned_id="$session_id"
if [[ -s "${FAKE_GROK_RETURN_FILE:?}" ]]; then
  returned_id="$(cat "$FAKE_GROK_RETURN_FILE")"
  printf 'Session %s forked to %s\n' "$session_id" "$returned_id" >&2
fi
printf 'fake grok answer\n'
EOF
chmod +x "$BIN_DIR/grok"

cat > "$BIN_DIR/agy" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >> "${FAKE_GEMINI_ARGV_LOG:?}"
printf '\n' >> "$FAKE_GEMINI_ARGV_LOG"

conversation_id=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --conversation) conversation_id="$2"; shift 2 ;;
    *) shift ;;
  esac
done

conversation_id="${conversation_id:-33333333-3333-4333-8333-333333333333}"
if [[ -s "${FAKE_GEMINI_RETURN_FILE:?}" ]]; then
  conversation_id="$(cat "$FAKE_GEMINI_RETURN_FILE")"
fi
CONVERSATION_ID="$conversation_id" python3 - <<'PY'
import json
import os

print(json.dumps({
    "conversation_id": os.environ["CONVERSATION_ID"],
    "status": "SUCCESS",
    "response": "fake gemini answer\n",
    "num_turns": 1,
}))
PY
EOF
chmod +x "$BIN_DIR/agy"

export FAKE_CLAUDE_ARGV_LOG="$TEST_ROOT/claude.argv"
export FAKE_CLAUDE_RETURN_FILE="$TEST_ROOT/return-session"
export FAKE_CLAUDE_FAIL_FILE="$TEST_ROOT/fail-resume"
export FAKE_CODEX_ARGV_LOG="$TEST_ROOT/codex.argv"
export FAKE_CODEX_RETURN_FILE="$TEST_ROOT/codex-return-session"
export FAKE_GROK_ARGV_LOG="$TEST_ROOT/grok.argv"
export FAKE_GROK_RETURN_FILE="$TEST_ROOT/grok-return-session"
export FAKE_GEMINI_ARGV_LOG="$TEST_ROOT/gemini.argv"
export FAKE_GEMINI_RETURN_FILE="$TEST_ROOT/gemini-return-session"
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
  --vendor exec consult x >"$TEST_ROOT/nonclaude.out" 2>"$TEST_ROOT/nonclaude.err"
RC=$?
set -e
EXPECTED="omnilane: --thread is supported for claude, codex, grok and gemini only; resolved vendor 'exec' cannot continue a thread"
[[ "$RC" -eq 2 ]] || fail "non-Claude refusal exit was $RC"
grep -Fxq "$EXPECTED" "$TEST_ROOT/nonclaude.out" || fail "non-Claude stdout notice missing"
grep -Fxq "$EXPECTED" "$TEST_ROOT/nonclaude.err" || fail "non-Claude stderr notice missing"

set +e
OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" --dry-run --thread stateless \
  --vendor openrouter consult x >"$TEST_ROOT/stateless.out" 2>"$TEST_ROOT/stateless.err"
RC=$?
set -e
EXPECTED="omnilane: --thread is supported for claude, codex, grok and gemini only; resolved vendor 'openrouter' cannot continue a thread"
[[ "$RC" -eq 2 ]] || fail "stateless-vendor refusal exit was $RC"
grep -Fxq "$EXPECTED" "$TEST_ROOT/stateless.out" || fail "stateless-vendor stdout notice missing"
grep -Fxq "$EXPECTED" "$TEST_ROOT/stateless.err" || fail "stateless-vendor stderr notice missing"

CODEX_STATE="$HOME_DIR/threads/codex-thread.json"
CODEX_OUT1="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread codex-thread --vendor codex --model fake-codex --effort low \
  --single-shot consult 'codex turn one' 2>"$TEST_ROOT/codex1.err")" \
  || fail "Codex first turn failed"
[[ "$CODEX_OUT1" == *"omnilane: thread codex-thread turn 1 (codex session pending, new)"* ]] \
  || fail "Codex pending visibility missing: $CODEX_OUT1"
CODEX_SESSION1="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$CODEX_STATE")"
[[ "$CODEX_OUT1" == *"omnilane: thread codex-thread turn 1 (codex session $CODEX_SESSION1, new)"* ]] \
  || fail "Codex resolved visibility missing: $CODEX_OUT1"
[[ "$CODEX_OUT1" == *"(codex session pending, new)"*"(codex session $CODEX_SESSION1, new)"* ]] \
  || fail "Codex pending line did not precede the resolved line: $CODEX_OUT1"
[[ "$(count_visible "$CODEX_OUT1" "$TEST_ROOT/codex1.err" 'omnilane: thread codex-thread turn 1 (')" -eq 2 ]] \
  || fail "Codex turn 1 visibility line was not printed exactly twice: $CODEX_OUT1 $(cat "$TEST_ROOT/codex1.err")"
[[ "$(count_visible "$CODEX_OUT1" "$TEST_ROOT/codex1.err" 'turn 1 (codex session pending, new)')" -eq 1 ]] \
  || fail "Codex pending line was not printed exactly once"
[[ "$(count_visible "$CODEX_OUT1" "$TEST_ROOT/codex1.err" "turn 1 (codex session $CODEX_SESSION1, new)")" -eq 1 ]] \
  || fail "Codex resolved line was not printed exactly once"
CODEX_ARGV1="$(sed -n '1p' "$FAKE_CODEX_ARGV_LOG")"
[[ "$CODEX_ARGV1" == exec\ --json* ]] || fail "Codex first turn argv malformed: $CODEX_ARGV1"
[[ "$CODEX_ARGV1" != *" resume "* && "$CODEX_ARGV1" != *"--ephemeral"* ]] \
  || fail "Codex first turn resumed or was ephemeral: $CODEX_ARGV1"
[[ "$CODEX_ARGV1" == *" -s read-only "* && "$CODEX_ARGV1" != *"sandbox_mode"* ]] \
  || fail "Codex plain exec lost -s read-only: $CODEX_ARGV1"
assert_state "$CODEX_STATE" codex-thread fake-codex "$CODEX_SESSION1" 1 codex

CODEX_OUT2="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread codex-thread --vendor codex --model fake-codex --effort low \
  --single-shot consult 'codex turn two' 2>"$TEST_ROOT/codex2.err")" \
  || fail "Codex second turn failed: $(cat "$TEST_ROOT/codex2.err")"
[[ "$(count_visible "$CODEX_OUT2" "$TEST_ROOT/codex2.err" 'omnilane: thread codex-thread turn 2 (')" -eq 1 ]] \
  || fail "Codex turn 2 visibility line was not printed exactly once: $CODEX_OUT2 $(cat "$TEST_ROOT/codex2.err")"
CODEX_ARGV2="$(sed -n '2p' "$FAKE_CODEX_ARGV_LOG")"
[[ "$CODEX_ARGV2" == exec\ resume\ --json*"$CODEX_SESSION1"* ]] \
  || fail "Codex second turn omitted exec resume id: $CODEX_ARGV2"
[[ "$CODEX_ARGV2" == *'-c sandbox_mode=\"read-only\"'* ]] \
  || fail "Codex resume omitted the sandbox_mode override: $CODEX_ARGV2"
[[ "$CODEX_ARGV2" != *" -s "* && "$CODEX_ARGV2" != *"--sandbox"* ]] \
  || fail "Codex resume passed -s, which codex exec resume rejects: $CODEX_ARGV2"
[[ "$CODEX_ARGV2" == *" $CODEX_SESSION1 - " ]] \
  || fail "Codex resume did not end with the session id and stdin prompt marker: $CODEX_ARGV2"
assert_state "$CODEX_STATE" codex-thread fake-codex "$CODEX_SESSION1" 2 codex

CODEX_SESSION2="22222222-2222-4222-8222-999999999999"
printf '%s\n' "$CODEX_SESSION2" > "$FAKE_CODEX_RETURN_FILE"
CODEX_OUT3="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread codex-thread --vendor codex --model fake-codex --effort low \
  --single-shot consult 'codex replacement id')" || fail "Codex replacement-id turn failed"
rm "$FAKE_CODEX_RETURN_FILE"
[[ "$CODEX_OUT3" == *"codex returned a different session id ($CODEX_SESSION1 -> $CODEX_SESSION2)"* ]] \
  || fail "Codex replacement-id notice missing: $CODEX_OUT3"
assert_state "$CODEX_STATE" codex-thread fake-codex "$CODEX_SESSION2" 3 codex

GROK_STATE="$HOME_DIR/threads/grok-thread.json"
GROK_OUT1="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread grok-thread --vendor grok --model fake-grok --effort low \
  --single-shot consult 'grok turn one')" || fail "Grok first turn failed: $GROK_OUT1"
GROK_SESSION1="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$GROK_STATE")"
GROK_ARGV1="$(sed -n '1p' "$FAKE_GROK_ARGV_LOG")"
[[ "$GROK_ARGV1" == *"--session-id $GROK_SESSION1"* && "$GROK_ARGV1" != *"--resume"* ]] \
  || fail "Grok first turn argv malformed: $GROK_ARGV1"
assert_state "$GROK_STATE" grok-thread fake-grok "$GROK_SESSION1" 1 grok

GROK_OUT2="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread grok-thread --vendor grok --model fake-grok --effort low \
  --single-shot consult 'grok turn two')" || fail "Grok second turn failed"
GROK_ARGV2="$(sed -n '2p' "$FAKE_GROK_ARGV_LOG")"
[[ "$GROK_ARGV2" == *"--resume $GROK_SESSION1"* && "$GROK_ARGV2" != *"--session-id"* ]] \
  || fail "Grok second turn omitted resume id: $GROK_ARGV2"
assert_state "$GROK_STATE" grok-thread fake-grok "$GROK_SESSION1" 2 grok

GROK_SESSION2="44444444-4444-4444-8444-444444444444"
printf '%s\n' "$GROK_SESSION2" > "$FAKE_GROK_RETURN_FILE"
GROK_OUT3="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread grok-thread --vendor grok --model fake-grok --effort low \
  --single-shot consult 'grok forked id')" || fail "Grok forked-id turn failed"
rm "$FAKE_GROK_RETURN_FILE"
[[ "$GROK_OUT3" == *"grok returned a different session id ($GROK_SESSION1 -> $GROK_SESSION2)"* ]] \
  || fail "Grok forked-id notice missing: $GROK_OUT3"
assert_state "$GROK_STATE" grok-thread fake-grok "$GROK_SESSION2" 3 grok

GEMINI_STATE="$HOME_DIR/threads/gemini-thread.json"
GEMINI_OUT1="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread gemini-thread --vendor gemini --model fake-gemini --effort low \
  --single-shot consult 'gemini turn one' 2>"$TEST_ROOT/gemini1.err")" \
  || fail "Gemini first turn failed"
GEMINI_SESSION1="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$GEMINI_STATE")"
[[ "$GEMINI_OUT1" == *"omnilane: thread gemini-thread turn 1 (gemini session pending, new)"* ]] \
  || fail "Gemini pending visibility missing: $GEMINI_OUT1"
[[ "$GEMINI_OUT1" == *"(gemini session pending, new)"*"(gemini session $GEMINI_SESSION1, new)"* ]] \
  || fail "Gemini resolved line missing or not after the pending line: $GEMINI_OUT1"
[[ "$(count_visible "$GEMINI_OUT1" "$TEST_ROOT/gemini1.err" 'omnilane: thread gemini-thread turn 1 (')" -eq 2 ]] \
  || fail "Gemini turn 1 visibility line was not printed exactly twice: $GEMINI_OUT1 $(cat "$TEST_ROOT/gemini1.err")"
[[ "$(count_visible "$GEMINI_OUT1" "$TEST_ROOT/gemini1.err" 'turn 1 (gemini session pending, new)')" -eq 1 ]] \
  || fail "Gemini pending line was not printed exactly once"
[[ "$(count_visible "$GEMINI_OUT1" "$TEST_ROOT/gemini1.err" "turn 1 (gemini session $GEMINI_SESSION1, new)")" -eq 1 ]] \
  || fail "Gemini resolved line was not printed exactly once"
GEMINI_ARGV1="$(sed -n '1p' "$FAKE_GEMINI_ARGV_LOG")"
[[ "$GEMINI_ARGV1" == *"--output-format json"* && "$GEMINI_ARGV1" != *"--conversation"* ]] \
  || fail "Gemini first turn argv malformed: $GEMINI_ARGV1"
assert_state "$GEMINI_STATE" gemini-thread fake-gemini "$GEMINI_SESSION1" 1 gemini

GEMINI_OUT2="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread gemini-thread --vendor gemini --model fake-gemini --effort low \
  --single-shot consult 'gemini turn two' 2>"$TEST_ROOT/gemini2.err")" \
  || fail "Gemini second turn failed"
[[ "$(count_visible "$GEMINI_OUT2" "$TEST_ROOT/gemini2.err" 'omnilane: thread gemini-thread turn 2 (')" -eq 1 ]] \
  || fail "Gemini turn 2 visibility line was not printed exactly once: $GEMINI_OUT2 $(cat "$TEST_ROOT/gemini2.err")"
GEMINI_ARGV2="$(sed -n '2p' "$FAKE_GEMINI_ARGV_LOG")"
[[ "$GEMINI_ARGV2" == *"--conversation $GEMINI_SESSION1"* ]] \
  || fail "Gemini second turn omitted conversation id: $GEMINI_ARGV2"
assert_state "$GEMINI_STATE" gemini-thread fake-gemini "$GEMINI_SESSION1" 2 gemini

GEMINI_SESSION2="55555555-5555-4555-8555-555555555555"
printf '%s\n' "$GEMINI_SESSION2" > "$FAKE_GEMINI_RETURN_FILE"
GEMINI_OUT3="$(OMNILANE_HOME="$HOME_DIR" "$ROOT/scripts/dispatch.sh" \
  --thread gemini-thread --vendor gemini --model fake-gemini --effort low \
  --single-shot consult 'gemini replacement id')" || fail "Gemini replacement-id turn failed"
rm "$FAKE_GEMINI_RETURN_FILE"
[[ "$GEMINI_OUT3" == *"gemini returned a different session id ($GEMINI_SESSION1 -> $GEMINI_SESSION2)"* ]] \
  || fail "Gemini replacement-id notice missing: $GEMINI_OUT3"
assert_state "$GEMINI_STATE" gemini-thread fake-gemini "$GEMINI_SESSION2" 3 gemini

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
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["schema_version"] == 1 and any(t["name"] == "orchard" for t in d["threads"])' \
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

printf 'ok - four-vendor threaded dispatch state resume refusals and jobs CLI\n'
