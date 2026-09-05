#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CASE="${1:-}"
TEST_BASE="$ROOT/.test-scratch"
mkdir -p "$TEST_BASE"
TEST_ROOT="$(mktemp -d "$TEST_BASE/omnilane-live-tests.XXXXXX")"

cleanup() {
  /bin/rm -rf -- "$TEST_ROOT"
  rmdir "$TEST_BASE" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  printf '%s\n' "$1" >&2
  exit 1
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
  cat > "$bin/grok" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$bin/grok"
  printf 'triage: grok grok-default low\n' > "$home/routing.local.yaml"
  out="$(OMNILANE_HOME="$home" GROK_BIN="$bin/grok" \
    "$ROOT/scripts/dispatch.sh" --dry-run --live --background --vendor grok \
      triage x 2>&1)" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "--live silently accepted non-capable vendor"
  [[ "$out" == *"claude"* && "$out" == *"gemini"* && "$out" == *"codex"* ]] ||
    fail "--live error did not name live-capable vendors: $out"
  [[ ! -e "$home/jobs" ]] || fail "fail-fast created job state"
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


if len(sys.argv) >= 2 and sys.argv[1] == "app-server":
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
  wait_for_file "$job_dir/inbox.ready" || fail "Codex live mailbox did not become ready"
  wait_for_lines "$job_dir/out.txt" 1 || fail "Codex agentMessage was not written incrementally"
  grep -Fxq 'initial agent text' "$job_dir/out.txt" || fail "Codex initial agentMessage missing"
  ! grep -Fq 'initial prompt echo marker' "$job_dir/out.txt" || fail "Codex userMessage echo leaked into output"

  OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$job" "$reply" >/dev/null
  wait_for_lines "$job_dir/out.txt" 2 || fail "Codex steered agentMessage did not reach output"
  grep -Fxq "$reply" "$job_dir/out.txt" || fail "Codex steered reply was not preserved verbatim"

  INPUT="$input" ROOT="$ROOT" REPLY="$reply" python3 - <<'PY' || fail "Codex JSON-RPC request sequence mismatch"
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
assert turn["input"] == [{"type": "text", "text": "initial prompt echo marker"}]
steer = requests[3]["params"]
assert steer["threadId"] == "thread-live-1"
assert steer["expectedTurnId"] == "turn-live-1"
assert steer["input"] == [{"type": "text", "text": os.environ["REPLY"]}]
PY
  grep -Fxq 'thread-live-1' "$job_dir/out.txt.session-id" || fail "Codex thread id was not recorded"

  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "Codex live close failed: $close_out"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Codex live job did not exit zero"
}

case_codex_live_fallback() {
  local home="$TEST_ROOT/codex-fallback" bin="$TEST_ROOT/codex-fallback/bin"
  local fake="$bin/codex" job job_dir doctor_out
  mkdir -p "$home" "$bin"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
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

case_gemini_schema() {
  local home="$TEST_ROOT/gemini" bin="$TEST_ROOT/gemini/bin"
  local fake="$bin/agy" args="$home/agy.args" input="$home/agy.input"
  local job job_dir second close_out
  mkdir -p "$home" "$bin"
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
  job="$(OMNILANE_HOME="$home" AGY_BIN="$fake" FAKE_AGY_ARGS="$args" \
    FAKE_AGY_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background --live \
      --idle-timeout 0 --vendor gemini triage first)"
  job_dir="$home/jobs/$job"
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
  printf '%s' "$prompt" > "$expected"

  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" FAKE_CLAUDE_ARGS="$args" \
    FAKE_CLAUDE_INPUT="$input" "$ROOT/scripts/dispatch.sh" --background --live \
    --idle-timeout 0 --vendor claude triage "$prompt")"
  job_dir="$home/jobs/$job"
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

case_codex_shutdown_grace() {
  local fixture="$TEST_ROOT/codex-shutdown-fixture"
  local runner="$fixture/scripts/runners/run-codex.sh"
  local probe="$fixture/codex"
  mkdir -p "$fixture/scripts/lib" "$fixture/scripts/runners"
  ln -s "$ROOT/scripts/lib/job-worker.sh" "$fixture/scripts/lib/job-worker.sh"
  ln -s "$ROOT/scripts/lib/common.sh" "$fixture/scripts/lib/common.sh"
  ln -s "$ROOT/scripts/lib/live-protocol.sh" "$fixture/scripts/lib/live-protocol.sh"
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
    while :; do /bin/sleep 1; done
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
      "$fixture/scripts/lib/job-worker.sh" codex advise "$case_dir/workdir" fake medium \
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
  done
}

case "$CASE" in
  "")
    bash "$0" codex-shutdown-grace
    printf 'ok - Codex live shutdown waits gracefully then escalates\n'
    bash "$0" live-fail-fast
    printf 'ok - live mode rejects non-capable vendor\n'
    bash "$0" single-shot-claude
    printf 'ok - single-shot mode forces Claude one-shot\n'
    bash "$0" idle-cap
    printf 'ok - live mailbox idle cap closes session\n'
    bash "$0" gemini-schema
    printf 'ok - Gemini live mailbox uses agy schema\n'
    bash "$0" json-escape
    printf 'ok - live prompt JSON escaping round-trips exact text\n'
  bash "$0" jobs-send-json-escape
  printf 'ok - jobs.sh send JSON escaping round-trips exact text\n'
  bash "$0" codex-live-rpc
  printf 'ok - Codex live JSON-RPC mailbox and incremental output\n'
  bash "$0" codex-live-fallback
  printf 'ok - Codex failed handshake degrades to single-shot\n'
    ;;
  live-fail-fast) case_live_fail_fast ;;
  single-shot-claude) case_single_shot_claude ;;
  idle-cap) case_idle_cap ;;
  gemini-schema) case_gemini_schema ;;
  json-escape) case_json_escape_round_trip ;;
  jobs-send-json-escape) case_jobs_send_json_escape_round_trip ;;
  codex-live-rpc) case_codex_live_rpc ;;
  codex-live-fallback) case_codex_live_fallback ;;
  codex-shutdown-grace) case_codex_shutdown_grace ;;
  *) fail "unknown live mailbox test case: $CASE" ;;
esac
