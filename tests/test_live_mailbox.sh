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
  cat > "$bin/qwen" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$bin/qwen"
  printf 'triage: qwen qwen-default low\n' > "$home/routing.local.yaml"
  out="$(OMNILANE_HOME="$home" QWEN_BIN="$bin/qwen" \
    "$ROOT/scripts/dispatch.sh" --dry-run --live --background --vendor qwen \
      triage x 2>&1)" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "--live silently accepted non-capable vendor"
  [[ "$out" == *"claude"* && "$out" == *"gemini"* && "$out" == *"codex"* && "$out" == *"grok"* ]] ||
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


app_args = sys.argv[1:]
while app_args[:1] == ["-c"] and len(app_args) >= 2:
    app_args = app_args[2:]
if app_args[:1] == ["app-server"]:
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
while [[ "${1:-}" == "-c" ]]; do shift 2; done
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

case_claude_close_recovers_result_output() {
  local home="$TEST_ROOT/claude-close-recovery" bin="$TEST_ROOT/claude-close-recovery/bin"
  local fake="$bin/claude" job job_dir close_out
  mkdir -p "$home" "$bin"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
trap '' TERM
while IFS= read -r _; do
  printf '%s\n' '{"type":"system","model":"claude-opus-5"}'
  printf '%s\n' '{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"text","text":"claude recovered close output"}]}}'
  printf '%s\n' '{"type":"result","is_error":false,"result":"claude recovered close output"}'
done
sleep 60
EOF
  chmod +x "$fake"
  printf 'triage: claude claude-opus-5 high\n' > "$home/routing.local.yaml"
  job="$(OMNILANE_HOME="$home" CLAUDE_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
    --mode work --workdir "$ROOT" --vendor claude triage 'recover completed output')"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/inbox.ready" || fail "stubborn Claude mailbox did not become ready"
  wait_for_lines "$job_dir/events.jsonl" 3 || fail "stubborn Claude did not emit a successful result"
  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" ||
    fail "stubborn Claude close failed: $close_out"
  wait_for_file "$job_dir/exit" || fail "stubborn Claude close did not record exit"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "stubborn Claude close was not successful"
  grep -Fxq 'claude recovered close output' "$job_dir/out.txt" ||
    fail "successful Claude result event was not recovered into out.txt"
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
      --idle-timeout 0 --mode sysops --vendor gemini triage first)"
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

case_codex_killed_runner_reaps_app_server() {
 local case_dir="$TEST_ROOT/codex-killed-runner" fake="$TEST_ROOT/codex-killed-runner/codex"
 mkdir -p "$case_dir"
 : > "$case_dir/inbox"
 cat > "$fake" <<'EOF'
#!/usr/bin/env bash
trap '' TERM
sleep 30 &
printf '%s %s\n' "$$" "$!" > "${FAKE_CODEX_PID_FILE:?}"
wait
EOF
 chmod +x "$fake"

 if ! ROOT="$ROOT" CASE_DIR="$case_dir" FAKE_CODEX="$fake" \
  FAKE_CODEX_PID_FILE="$case_dir/codex-pids" python3 - <<'PY'
import os
import pathlib
import signal
import subprocess
import time

root = pathlib.Path(os.environ["ROOT"])
case_dir = pathlib.Path(os.environ["CASE_DIR"])
pidfile = pathlib.Path(os.environ["FAKE_CODEX_PID_FILE"])
command = [
    str(root / "scripts/runners/run-codex-live.py"),
    "--codex-bin", os.environ["FAKE_CODEX"],
    "--cwd", str(case_dir),
    "--model", "fake-model",
    "--effort", "low",
    "--sandbox", "read-only",
    "--inbox", str(case_dir / "inbox"),
    "--events", str(case_dir / "events.jsonl"),
    "--output", str(case_dir / "out.txt"),
    "--progress", str(case_dir / "progress.json"),
    "--session-id-file", str(case_dir / "session-id"),
    "--rpc-timeout", "30",
]
stderr = (case_dir / "stderr").open("w", encoding="utf-8")
runner = subprocess.Popen(
    command,
    stdout=subprocess.DEVNULL,
    stderr=stderr,
    start_new_session=True,
)
server_pids: list[int] = []
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.05)
    if not pidfile.exists():
        raise AssertionError("fake app-server did not start")
    server_pids = [int(value) for value in pidfile.read_text().split()]

    # Model the outer watchdog's uncatchable escalation. The app-server and
    # its child must remain in this group even though the runner's finally
    # block cannot execute after SIGKILL.
    os.killpg(runner.pid, signal.SIGKILL)
    runner.wait(timeout=2)

    deadline = time.monotonic() + 2
    survivors = server_pids
    while survivors and time.monotonic() < deadline:
        current = []
        for pid in survivors:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            current.append(pid)
        survivors = current
        if survivors:
            time.sleep(0.05)
    if survivors:
        raise AssertionError(f"surviving app-server pids: {survivors}")
finally:
    stderr.close()
    if runner.poll() is None:
        try:
            os.killpg(runner.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        runner.wait(timeout=2)
    for pid in server_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
PY
 then
  fail "killed Codex live runner left its app-server process group alive"
 fi
}

case_codex_app_server_eof_cleanup() {
  local case_dir="$TEST_ROOT/codex-app-server-close" fake="$TEST_ROOT/codex-app-server-close/codex"
  mkdir -p "$case_dir"
  cat > "$fake" <<'PY'
#!/usr/bin/env python3
import os
import pathlib
import signal
import subprocess
import sys
import time

args = sys.argv[1:]
while args[:1] == ["-c"] and len(args) >= 2:
    args = args[2:]
if args != ["app-server"]:
    raise SystemExit(2)

prefix = pathlib.Path(os.environ["FAKE_CLOSE_PREFIX"])
mode = os.environ["FAKE_CLOSE_MODE"]
sidecar = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
prefix.with_suffix(".sidecar-pid").write_text(str(sidecar.pid), encoding="utf-8")

def on_term(_signum, _frame):
    prefix.with_suffix(".term").write_text("TERM\n", encoding="utf-8")
    if mode == "ignore-eof-term":
        return
    raise SystemExit(143)

signal.signal(signal.SIGTERM, on_term)
sys.stdin.read()
if mode == "eof-clean":
    time.sleep(0.15)
    sidecar.terminate()
    sidecar.wait(timeout=2)
    prefix.with_suffix(".eof-clean").write_text("clean\n", encoding="utf-8")
    raise SystemExit(0)

while True:
    time.sleep(0.05)
PY
  chmod +x "$fake"

  ROOT="$ROOT" CASE_DIR="$case_dir" FAKE_CODEX="$fake" python3 - <<'PY' \
    || fail "Codex app-server EOF cleanup/escalation oracle failed"
import importlib.util
import os
import pathlib
import signal
import time
from types import SimpleNamespace

root = pathlib.Path(os.environ["ROOT"])
case_dir = pathlib.Path(os.environ["CASE_DIR"])
spec = importlib.util.spec_from_file_location(
    "omnilane_codex_live", root / "scripts/runners/run-codex-live.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False

def stop_sidecar(prefix):
    pid_path = prefix.with_suffix(".sidecar-pid")
    if not pid_path.exists():
        return
    pid = int(pid_path.read_text(encoding="utf-8"))
    if alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

def start(mode):
    prefix = case_dir / mode
    os.environ["FAKE_CLOSE_MODE"] = mode
    os.environ["FAKE_CLOSE_PREFIX"] = str(prefix)
    args = SimpleNamespace(
        codex_bin=os.environ["FAKE_CODEX"],
        cwd=str(case_dir),
        app_server_eof_grace=0.5,
        app_server_term_grace=0.2,
        app_server_kill_grace=0.2,
    )
    client = module.CodexLiveClient(args)
    client.start_server()
    deadline = time.monotonic() + 2
    pid_path = prefix.with_suffix(".sidecar-pid")
    while time.monotonic() < deadline and not pid_path.exists():
        time.sleep(0.02)
    assert pid_path.exists(), f"{mode}: sidecar did not start"
    return client, prefix, int(pid_path.read_text(encoding="utf-8"))

normal, normal_prefix, normal_sidecar = start("eof-clean")
try:
    normal.close()
    assert normal_prefix.with_suffix(".eof-clean").exists(), "normal EOF cleanup did not run"
    assert not normal_prefix.with_suffix(".term").exists(), "normal EOF path received TERM"
    assert not alive(normal_sidecar), f"normal EOF left sidecar {normal_sidecar}"
    assert normal.process is not None and normal.process.returncode == 0, normal.process
finally:
    stop_sidecar(normal_prefix)

stuck, stuck_prefix, _ = start("ignore-eof-term")
started = time.monotonic()
try:
    stuck.close()
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"stuck close was not bounded: {elapsed:.3f}s"
    assert stuck_prefix.with_suffix(".term").exists(), "stuck process never received TERM"
    assert stuck.process is not None and stuck.process.returncode == -signal.SIGKILL, stuck.process
finally:
    stop_sidecar(stuck_prefix)
PY
}

case_codex_close_grace_precedes_worker_escalation() {
 if ! ROOT="$ROOT" python3 - <<'PY'
import ast
import os
import pathlib
import re

root = pathlib.Path(os.environ["ROOT"])
runner_path = root / "scripts/runners/run-codex-live.py"
worker_path = root / "scripts/lib/job-worker.sh"

tree = ast.parse(runner_path.read_text(encoding="utf-8"))
close_grace = None
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
        continue
    if not isinstance(node.args[0], ast.Constant) or node.args[0].value != "--close-grace":
        continue
    for keyword in node.keywords:
        if keyword.arg == "default":
            close_grace = float(ast.literal_eval(keyword.value))

worker = worker_path.read_text(encoding="utf-8")
grace = re.search(r'^CLOSE_RUNNER_GRACE=([0-9.]+)$', worker, re.MULTILINE)
if close_grace is None or grace is None:
    raise AssertionError("could not resolve close timing constants")
outer_grace = float(grace.group(1))

# close() itself may spend one more second waiting after TERM. Keep a real
# margin instead of merely making the two nominal deadlines equal.
if not close_grace + 1.0 < outer_grace:
    raise AssertionError(
        f"inner={close_grace}s plus cleanup is not below outer={outer_grace}s"
    )
PY
 then
  fail "Codex close grace does not expire before worker escalation"
 fi
}

case_codex_full_close_budget_precedes_worker_escalation() {
  if ! ROOT="$ROOT" python3 - <<'PY'
import ast
import os
import pathlib
import re

root = pathlib.Path(os.environ["ROOT"])
runner_path = root / "scripts/runners/run-codex-live.py"
worker_path = root / "scripts/lib/job-worker.sh"
tree = ast.parse(runner_path.read_text(encoding="utf-8"))
wanted = {
    "--close-grace",
    "--app-server-eof-grace",
    "--app-server-term-grace",
    "--app-server-kill-grace",
}
defaults = {}
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
        continue
    if not isinstance(node.args[0], ast.Constant) or node.args[0].value not in wanted:
        continue
    for keyword in node.keywords:
        if keyword.arg == "default":
            defaults[node.args[0].value] = float(ast.literal_eval(keyword.value))

worker = worker_path.read_text(encoding="utf-8")
grace = re.search(r'^CLOSE_RUNNER_GRACE=([0-9.]+)$', worker, re.MULTILINE)
if set(defaults) != wanted or grace is None:
    raise AssertionError(f"could not resolve close budget: {defaults}")
inner_budget = sum(defaults.values())
outer_budget = float(grace.group(1))
if not inner_budget + 0.25 < outer_budget:
    raise AssertionError(
        f"turn+EOF+TERM+KILL={inner_budget}s lacks margin below worker={outer_budget}s"
    )
timeouts = {name: float(re.search(r'^' + name + r'=([0-9.]+)$', worker, re.MULTILINE).group(1))
            for name in ('CLOSE_DRAIN_TIMEOUT', 'CLOSE_TERM_GRACE', 'CLOSE_KILL_GRACE')}
if not 1.0 + sum(timeouts.values()) + outer_budget < 9.0:
    raise AssertionError("worker wakeup+drain+grace+TERM+KILL must precede jobs close's quantized 10s")
PY
  then
    fail "Codex full close budget does not expire before worker escalation"
  fi
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
    # Let Bash run its deferred TERM trap inside the bounded TERM grace.
    while :; do /bin/sleep 0.05; done
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
      /bin/bash "$fixture/scripts/lib/job-worker.sh" codex advise "$case_dir/workdir" fake medium \
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
    ! grep -Fq 'invalid timeout specification' "$case_dir/stderr" \
      || fail "Codex $mode close used a Bash 3.2-incompatible FIFO timeout"
  done
}

case_grok_live_acp() {
  local home="$TEST_ROOT/grok-live" bin="$TEST_ROOT/grok-live/bin"
  local fake="$bin/grok" input="$home/grok.input" counter="$home/grok.counter"
  local job job_dir close_out send_out tries reply
  mkdir -p "$home" "$bin"

  cat > "$fake" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


if sys.argv[1:] not in (
    ["agent", "stdio"],
    ["agent", "--model", "grok-4.6", "stdio"],
    ["--no-memory", "--no-subagents", "--no-plan", "--verbatim", "--sandbox", "off", "agent", "--always-approve", "stdio"],
    ["--no-memory", "--no-subagents", "--no-plan", "--verbatim", "--sandbox", "off", "agent", "--always-approve", "--model", "grok-4.6", "stdio"],
):
    print("grok single-shot")
    raise SystemExit(0)

counter_path = pathlib.Path(os.environ["FAKE_GROK_COUNTER"])
try:
    invocation = int(counter_path.read_text(encoding="utf-8")) + 1
except (FileNotFoundError, ValueError):
    invocation = 1
counter_path.write_text(str(invocation), encoding="utf-8")
input_path = pathlib.Path(os.environ["FAKE_GROK_INPUT"])
session_id = "grok-session-live-1"
prompt_count = 0

for raw in sys.stdin:
    if invocation > 1:
        with input_path.open("a", encoding="utf-8") as captured:
            captured.write(raw)
    request = json.loads(raw)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        emit({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "sessionCapabilities": {"list": {}, "resume": {}, "close": {}}
                },
            },
        })
    elif method == "session/new":
        emit({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"sessionId": session_id, "models": {}},
        })
    elif method == "session/prompt":
        prompt_count += 1
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        if prompt_count == 1:
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "PRIVATE THOUGHT"},
                    },
                },
            })
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "initial visible "},
                    },
                },
            })
        else:
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": os.environ["FAKE_GROK_REPLY"]},
                    },
                },
            })
            emit({
                "jsonrpc": "2.0",
                "method": "_x.ai/session/prompt_complete",
                "params": {"sessionId": session_id, "stopReason": "end_turn"},
            })
    elif method == "session/cancel":
        emit({
            "jsonrpc": "2.0",
            "method": "_x.ai/session/prompt_complete",
            "params": {"sessionId": session_id, "stopReason": "cancelled"},
        })
    elif method == "session/close":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        raise SystemExit(0)
PY
  chmod +x "$fake"
  printf 'live-search: grok grok-4.6 high\n' > "$home/routing.local.yaml"
  printf -v reply 'path\\to "x" 測試'

  local advise_dry advise_live_out advise_live_rc=0
  advise_dry="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --dry-run --background \
      --workdir "$ROOT" --vendor grok --model grok-4.6 \
      live-search 'read-only Grok advice')"
  [[ "$advise_dry" == *"session_mode=single-shot"* ]] ||
    fail "Grok advise did not stay single-shot: $advise_dry"

  set +e
  advise_live_out="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --dry-run --background --live \
      --workdir "$ROOT" --vendor grok --model grok-4.6 \
      live-search 'read-only Grok advice' 2>&1)"
  advise_live_rc=$?
  set -e
  [[ "$advise_live_rc" -eq 2 ]] ||
    fail "explicit Grok advise --live should fail with exit 2: $advise_live_out"
  [[ "$advise_live_out" == *"only explicit --mode sysops"* ]] ||
    fail "explicit Grok advise --live lacked sysops-only guidance: $advise_live_out"

  job="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    FAKE_GROK_INPUT="$input" FAKE_GROK_COUNTER="$counter" \
    FAKE_GROK_REPLY="$reply" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
      --mode sysops --workdir "$ROOT" --vendor grok --model grok-4.6 \
      live-search 'initial Grok prompt')"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/inbox.ready" || fail "Grok live mailbox did not become ready"

  tries=0
  until grep -Fq 'initial visible ' "$job_dir/out.txt" 2>/dev/null; do
    [[ ! -e "$job_dir/exit" ]] || fail "Grok live job exited before incremental output"
    [[ "$tries" -lt 100 ]] || fail "Grok agent_message_chunk was not written incrementally"
    sleep 0.1
    tries=$((tries + 1))
  done
  ! grep -Fq 'PRIVATE THOUGHT' "$job_dir/out.txt" || \
    fail "Grok agent_thought_chunk leaked into output"

  send_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" send "$job" "$reply")" ||
    fail "Grok live send failed: $send_out"
  [[ "$send_out" == *"cancels an active turn"*"in-progress work is lost"* ]] ||
    fail "jobs.sh send did not surface Grok cancellation semantics: $send_out"
  tries=0
  until grep -Fq "$reply" "$job_dir/out.txt" 2>/dev/null; do
    [[ ! -e "$job_dir/exit" ]] || fail "Grok live job exited before steered reply"
    [[ "$tries" -lt 100 ]] || fail "Grok steered reply did not reach output"
    sleep 0.1
    tries=$((tries + 1))
  done

  INPUT="$input" ROOT="$ROOT" REPLY="$reply" python3 - <<'PY'
import json
import os
import pathlib

requests = [
    json.loads(line)
    for line in pathlib.Path(os.environ["INPUT"]).read_text(encoding="utf-8").splitlines()
]
methods = [item["method"] for item in requests]
assert methods[:5] == [
    "initialize",
    "session/new",
    "session/prompt",
    "session/cancel",
    "session/prompt",
], methods
initialize = requests[0]
assert initialize["params"] == {
    "protocolVersion": 1,
    "clientCapabilities": {
        "fs": {"readTextFile": False, "writeTextFile": False}
    },
}
new_session = requests[1]
assert new_session["params"] == {"cwd": os.environ["ROOT"], "mcpServers": []}
first_prompt = requests[2]
cancel = requests[3]
second_prompt = requests[4]
assert first_prompt["params"]["sessionId"] == "grok-session-live-1"
assert first_prompt["params"]["prompt"] == [
    {"type": "text", "text": "initial Grok prompt"}
]
assert "id" not in cancel, cancel
assert cancel["params"] == {"sessionId": "grok-session-live-1"}
assert second_prompt["params"]["sessionId"] == "grok-session-live-1"
assert second_prompt["params"]["prompt"] == [
    {"type": "text", "text": os.environ["REPLY"]}
]
PY
  grep -Fxq 'grok-session-live-1' "$job_dir/out.txt.session-id" || \
    fail "Grok session id was not recorded"
  grep -Fq 'no permission-mode field' "$job_dir/out.txt.stderr.log" || \
    fail "Grok ACP permission limitation was not surfaced"
  grep -Fq 'in-progress work is lost' "$job_dir/out.txt.stderr.log" || \
    fail "Grok cancellation semantics were not surfaced"

  close_out="$(OMNILANE_HOME="$home" "$ROOT/scripts/jobs.sh" close "$job" 2>&1)" || \
    fail "Grok live close failed: $close_out"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Grok live job did not exit zero"
  INPUT="$input" python3 - <<'PY'
import json
import os
import pathlib

requests = [
    json.loads(line)
    for line in pathlib.Path(os.environ["INPUT"]).read_text(encoding="utf-8").splitlines()
]
assert requests[-1]["method"] == "session/close", requests[-1]
assert requests[-1]["params"] == {"sessionId": "grok-session-live-1"}
PY
}

case_grok_live_fallback() {
  local home="$TEST_ROOT/grok-fallback" bin="$TEST_ROOT/grok-fallback/bin"
  local fake="$bin/grok" job job_dir doctor_out
  mkdir -p "$home" "$bin"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "agent" && "${2:-}" == "stdio" ]]; then
  IFS= read -r _ || true
  printf '{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"agent stdio unavailable"}}\n'
  exit 1
fi
printf 'grok single-shot fallback\n'
EOF
  chmod +x "$fake"
  printf 'live-search: grok grok-4.6 high\n' > "$home/routing.local.yaml"

  job="$(OMNILANE_HOME="$home" GROK_BIN="$fake" \
    "$ROOT/scripts/dispatch.sh" --background --live --idle-timeout 0 \
      --mode sysops --workdir "$ROOT" --vendor grok --model grok-4.6 live-search fallback)"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/exit" || fail "Grok fallback job did not finish"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Grok fallback job failed"
  grep -Fxq 'grok single-shot fallback' "$job_dir/out.txt" || \
    fail "Grok failed handshake did not run single-shot"
  grep -Fxq 'omnilane: grok live surface unavailable; ran in single-shot mode' \
    "$job_dir/mode-notice.txt" || fail "Grok fallback notice was not recorded"

  doctor_out="$(OMNILANE_HOME="$home" GROK_BIN="$fake" "$ROOT/scripts/doctor.sh" 2>&1)"
  [[ "$doctor_out" == *"grok-live"*"agent stdio initialize handshake failed"* ]] || \
    fail "doctor did not report Grok handshake failure"
  [[ "$doctor_out" == *"live-unavailable"*"grok"* ]] || \
    fail "doctor did not list Grok as live-unavailable"
}

case_grok_close_grace_precedes_worker_escalation() {
  if ! ROOT="$ROOT" python3 - <<'PY'
import ast
import os
import pathlib
import re

root = pathlib.Path(os.environ["ROOT"])
runner_path = root / "scripts/runners/run-grok-live.py"
worker_path = root / "scripts/lib/job-worker.sh"
tree = ast.parse(runner_path.read_text(encoding="utf-8"))
close_grace = None
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
        continue
    if not isinstance(node.args[0], ast.Constant) or node.args[0].value != "--close-grace":
        continue
    for keyword in node.keywords:
        if keyword.arg == "default":
            close_grace = float(ast.literal_eval(keyword.value))

worker = worker_path.read_text(encoding="utf-8")
grace = re.search(r'^CLOSE_RUNNER_GRACE=([0-9.]+)$', worker, re.MULTILINE)
if close_grace is None or grace is None:
    raise AssertionError("could not read Grok close timing constants")
outer_grace = float(grace.group(1))
if not close_grace + 1.0 < outer_grace:
    raise AssertionError(
        f"Grok inner={close_grace}s plus TERM wait is not below outer={outer_grace}s"
    )
PY
  then
    fail "Grok close grace does not expire before worker escalation"
  fi
}

case_grok_killed_runner_reaps_agent() {
  local case_dir="$TEST_ROOT/grok-killed-runner" fake="$TEST_ROOT/grok-killed-runner/grok"
  mkdir -p "$case_dir"
  : > "$case_dir/inbox"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
trap '' TERM
sleep 30 &
printf '%s %s\n' "$$" "$!" > "${FAKE_GROK_PID_FILE:?}"
wait
EOF
  chmod +x "$fake"

  if ! ROOT="$ROOT" CASE_DIR="$case_dir" FAKE_GROK="$fake" \
    FAKE_GROK_PID_FILE="$case_dir/grok-pids" python3 - <<'PY'
import os
import pathlib
import signal
import subprocess
import time

root = pathlib.Path(os.environ["ROOT"])
case_dir = pathlib.Path(os.environ["CASE_DIR"])
pidfile = pathlib.Path(os.environ["FAKE_GROK_PID_FILE"])
command = [
    str(root / "scripts/runners/run-grok-live.py"),
    "--grok-bin", os.environ["FAKE_GROK"],
    "--cwd", str(case_dir),
    "--model", "fake-model",
    "--mode", "sysops",
    "--inbox", str(case_dir / "inbox"),
    "--events", str(case_dir / "events.jsonl"),
    "--output", str(case_dir / "out.txt"),
    "--progress", str(case_dir / "progress.json"),
    "--session-id-file", str(case_dir / "session-id"),
    "--rpc-timeout", "30",
]
stderr = (case_dir / "stderr").open("w", encoding="utf-8")
runner = subprocess.Popen(
    command,
    cwd=case_dir,
    stdout=subprocess.DEVNULL,
    stderr=stderr,
    start_new_session=True,
)
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.05)
    if not pidfile.exists():
        raise AssertionError("fake Grok ACP pid file was not created")
    pids = [int(value) for value in pidfile.read_text().split()]
    os.killpg(runner.pid, signal.SIGKILL)
    runner.wait(timeout=2)
    deadline = time.monotonic() + 2
    survivors = pids
    while time.monotonic() < deadline:
        current = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            current.append(pid)
        survivors = current
        if not survivors:
            break
        time.sleep(0.05)
    if survivors:
        raise AssertionError(f"surviving Grok ACP processes: {survivors}")
finally:
    stderr.close()
    if runner.poll() is None:
        try:
            os.killpg(runner.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        runner.wait(timeout=2)
    if pidfile.exists():
        for value in pidfile.read_text().split():
            try:
                os.kill(int(value), signal.SIGKILL)
            except ProcessLookupError:
                pass
PY
  then
    fail "Grok live runner left ACP process group alive"
  fi
}

make_eof_sensitive_probe() {
  local path="$1"
  cat > "$path" <<'PY'
#!/usr/bin/env python3
import json
import select
import sys
import time

raw = sys.stdin.readline()
if not raw:
    raise SystemExit(3)
request = json.loads(raw)
readable, _, _ = select.select([sys.stdin], [], [], 0.25)
if readable and sys.stdin.read(1) == "":
    raise SystemExit(42)
if sys.argv[1:] == ["app-server"]:
    result = {"userAgent": "eof-sensitive", "codexHome": "/fake"}
elif sys.argv[1:] == ["agent", "stdio"]:
    result = {"protocolVersion": 1, "agentCapabilities": {}}
else:
    raise SystemExit(4)
print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
time.sleep(0.1)
PY
  chmod +x "$path"
}

case_codex_probe_stdin_open() {
  local case_dir="$TEST_ROOT/codex-probe-stdin" fake legacy
  fake="$case_dir/fake-live-cli.py"
  legacy="$case_dir/live-protocol-legacy.sh"
  mkdir -p "$case_dir"
  make_eof_sensitive_probe "$fake"

  (
    # shellcheck source=../scripts/lib/live-protocol.sh
    source "$ROOT/scripts/lib/live-protocol.sh"
    codex_live_surface_available "$fake"
  ) || fail "Codex probe closed stdin before initialize response"

  SOURCE="$ROOT/scripts/lib/live-protocol.sh" LEGACY="$legacy" python3 - <<'PY'
import os
import pathlib

source = pathlib.Path(os.environ["SOURCE"]).read_text(encoding="utf-8")
needle = "process.stdin.flush()\n"
if needle not in source:
    raise SystemExit("Codex probe flush marker missing")
source = source.replace(needle, needle + "    process.stdin.close()\n", 1)
pathlib.Path(os.environ["LEGACY"]).write_text(source, encoding="utf-8")
PY
  if (
    # shellcheck source=/dev/null
    source "$legacy"
    codex_live_surface_available "$fake"
  ); then
    fail "Codex EOF-sensitive oracle accepted the legacy early-close probe"
  fi
}

case_grok_probe_stdin_open() {
  local case_dir="$TEST_ROOT/grok-probe-stdin" fake legacy
  fake="$case_dir/fake-live-cli.py"
  legacy="$case_dir/live-protocol-legacy.sh"
  mkdir -p "$case_dir"
  make_eof_sensitive_probe "$fake"

  (
    # shellcheck source=../scripts/lib/live-protocol.sh
    source "$ROOT/scripts/lib/live-protocol.sh"
    grok_live_surface_available "$fake"
  ) || fail "Grok probe closed stdin before initialize response"

  SOURCE="$ROOT/scripts/lib/live-protocol.sh" LEGACY="$legacy" python3 - <<'PY'
import os
import pathlib

source = pathlib.Path(os.environ["SOURCE"]).read_text(encoding="utf-8")
head, marker, tail = source.partition("grok_live_surface_available() {")
if not marker or "process.stdin.flush()\n" not in tail:
    raise SystemExit("Grok probe flush marker missing")
tail = tail.replace(
    "process.stdin.flush()\n",
    "process.stdin.flush()\n    process.stdin.close()\n",
    1,
)
pathlib.Path(os.environ["LEGACY"]).write_text(head + marker + tail, encoding="utf-8")
PY
  if (
    # shellcheck source=/dev/null
    source "$legacy"
    grok_live_surface_available "$fake"
  ); then
    fail "Grok EOF-sensitive oracle accepted the legacy early-close probe"
  fi
}

case_grok_model_and_prompt_errors() {
  local case_dir="$TEST_ROOT/grok-model-errors" fake model="grok-nondefault-regression"
  local runner="${GROK_LIVE_RUNNER_UNDER_TEST:-$ROOT/scripts/runners/run-grok-live.py}"
  mkdir -p "$case_dir"
  fake="$case_dir/fake-grok.py"
  cat > "$fake" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


pathlib.Path(os.environ["FAKE_GROK_ARGV"]).write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8"
)
pathlib.Path(os.environ["FAKE_GROK_PID"]).write_text(str(os.getpid()), encoding="utf-8")
input_path = pathlib.Path(os.environ["FAKE_GROK_INPUT"])
mode = os.environ["FAKE_GROK_MODE"]
session_id = "grok-regression-session"
first_prompt_id = None
prompt_count = 0
for raw in sys.stdin:
    with input_path.open("a", encoding="utf-8") as captured:
        captured.write(raw)
    request = json.loads(raw)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session_id}})
    elif method == "session/prompt":
        prompt_count += 1
        if prompt_count == 1:
            first_prompt_id = request_id
        if mode == "error-empty":
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "prompt failed", "data": "private-detail"}})
        elif mode == "error-partial":
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial before error"}}}})
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "prompt failed", "data": "private-detail"}})
        elif prompt_count == 1:
            emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "first turn running"}}}})
        else:
            emit({"jsonrpc": "2.0", "id": first_prompt_id, "error": {"code": -32800, "message": "old cancelled prompt"}})
            emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "replacement succeeded"}}}})
            emit({"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {"sessionId": session_id, "stopReason": "end_turn"}})
    elif method == "session/cancel":
        emit({"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {"sessionId": session_id, "stopReason": "cancelled"}})
    elif method == "session/close":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        break
PY
  chmod +x "$fake"

  run_error_case() {
    local mode="$1" expected_text="$2" pid rc=0 tries=0
    local dir="$case_dir/$mode"
    mkdir -p "$dir"
    mkfifo "$dir/inbox"
    FAKE_GROK_ARGV="$dir/argv.json" FAKE_GROK_PID="$dir/pid" \
      FAKE_GROK_INPUT="$dir/input.jsonl" FAKE_GROK_MODE="$mode" \
      python3 "$runner" --grok-bin "$fake" --cwd "$ROOT" --model "$model" \
      --mode sysops --inbox "$dir/inbox" --events "$dir/events.jsonl" \
      --output "$dir/out.txt" --progress "$dir/progress.json" \
      --session-id-file "$dir/session-id" --rpc-timeout 1 --close-grace 0.5 \
      >"$dir/stdout" 2>"$dir/stderr" &
    pid=$!
    exec 7>"$dir/inbox"
    printf '{"type":"grok-user","text":"trigger error"}\n' >&7
    while kill -0 "$pid" 2>/dev/null && [[ "$tries" -lt 30 ]]; do
      sleep 0.1
      tries=$((tries + 1))
    done
    exec 7>&-
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      fail "Grok $mode prompt error did not fail quickly"
    fi
    wait "$pid" || rc=$?
    [[ "$rc" -ne 0 ]] || fail "Grok $mode prompt error returned success"
    grep -Fq "Grok ACP rejected session/prompt: code=-32001 message='prompt failed'" "$dir/stderr" ||
      fail "Grok $mode prompt error was not observable"
    ! grep -Fq 'private-detail' "$dir/stderr" ||
      fail "Grok $mode prompt error leaked private data"
    if [[ -n "$expected_text" ]]; then
      grep -Fq "$expected_text" "$dir/out.txt" || fail "Grok $mode lost partial output"
    else
      [[ ! -s "$dir/out.txt" ]] || fail "Grok $mode unexpectedly wrote output"
    fi
    if kill -0 "$(cat "$dir/pid")" 2>/dev/null; then
      fail "Grok $mode left fake ACP running"
    fi
  }

  run_error_case error-empty ""
  run_error_case error-partial "partial before error"

  local live="$case_dir/delayed" pid rc=0 tries=0
  mkdir -p "$live"
  mkfifo "$live/inbox"
  FAKE_GROK_ARGV="$live/argv.json" FAKE_GROK_PID="$live/pid" \
    FAKE_GROK_INPUT="$live/input.jsonl" FAKE_GROK_MODE=delayed-cancel \
    python3 "$runner" --grok-bin "$fake" --cwd "$ROOT" --model "$model" \
    --mode sysops --inbox "$live/inbox" --events "$live/events.jsonl" \
    --output "$live/out.txt" --progress "$live/progress.json" \
    --session-id-file "$live/session-id" --rpc-timeout 1 --close-grace 0.5 \
    >"$live/stdout" 2>"$live/stderr" &
  pid=$!
  exec 7>"$live/inbox"
  printf '{"type":"grok-user","text":"first"}\n' >&7
  while ! grep -Fq 'first turn running' "$live/out.txt" 2>/dev/null; do
    [[ "$tries" -lt 30 ]] || fail "Grok delayed-cancel first turn did not start"
    sleep 0.1
    tries=$((tries + 1))
  done
  printf '{"type":"grok-user","text":"replacement"}\n' >&7
  tries=0
  while ! grep -Fq 'replacement succeeded' "$live/out.txt" 2>/dev/null; do
    [[ "$tries" -lt 30 ]] || fail "Grok delayed old error poisoned replacement turn"
    sleep 0.1
    tries=$((tries + 1))
  done
  exec 7>&-
  wait "$pid" || rc=$?
  [[ "$rc" -eq 0 ]] || fail "Grok replacement turn failed on delayed old error"
  ARGV="$live/argv.json" MODEL="$model" python3 - <<'PY'
import json
import os
import pathlib

argv = json.loads(pathlib.Path(os.environ["ARGV"]).read_text(encoding="utf-8"))
assert argv == ["--no-memory", "--no-subagents", "--no-plan", "--verbatim", "--sandbox", "off", "agent", "--always-approve", "--model", os.environ["MODEL"], "stdio"], argv
PY
  grep -Fq '"method":"session/cancel"' "$live/input.jsonl" ||
    fail "Grok replacement turn did not send cancellation"
}

case_grok_advise_fail_closed() {
  local home="$TEST_ROOT/grok-advise" bin="$TEST_ROOT/grok-advise/bin"
  local fake="$bin/grok" args="$home/args.txt" job job_dir
  local direct="$TEST_ROOT/grok-advise-direct" rc=0 before
  local worker="${GROK_JOB_WORKER_UNDER_TEST:-$ROOT/scripts/lib/job-worker.sh}"
  local grok_runner="${GROK_RUNNER_UNDER_TEST:-$ROOT/scripts/runners/run-grok.sh}"
  mkdir -p "$home" "$bin" "$direct/job" "$direct/workdir"
  cat > "$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "${FAKE_GROK_ARGS:?}"
printf 'grok advise single-shot\n'
EOF
  chmod +x "$fake"
  printf 'live-search: grok grok-advise-model high\n' > "$home/routing.local.yaml"
  job="$(OMNILANE_HOME="$home" GROK_BIN="$fake" FAKE_GROK_ARGS="$args" \
    "$ROOT/scripts/dispatch.sh" --background --workdir "$ROOT" --vendor grok \
    --model grok-advise-model live-search 'advise dispatch')"
  job_dir="$home/jobs/$job"
  wait_for_file "$job_dir/exit" || fail "Grok advise single-shot dispatch did not finish"
  [[ "$(cat "$job_dir/exit")" == "0" ]] || fail "Grok advise single-shot dispatch failed"
  grep -Fq '"session_mode":"single-shot"' "$job_dir/meta.json" ||
    fail "Grok advise metadata claimed a live session"
  grep -Fxq 'grok advise single-shot' "$job_dir/out.txt" ||
    fail "Grok advise did not execute the fake single-shot CLI"
  ARGS="$args" python3 - <<'PY'
import os
import pathlib

args = pathlib.Path(os.environ["ARGS"]).read_text(encoding="utf-8").splitlines()
index = args.index("--permission-mode")
assert args[index + 1] == "dontAsk", args
assert args[args.index("--tools") + 1] == "Bash,Read,Glob,Grep,WebSearch,WebFetch", args
assert [args[i + 1] for i, item in enumerate(args[:-1]) if item == "--deny"] == [
    "Bash", "Edit", "MCPTool"
], args
PY
  [[ ! -e "$job_dir/inbox.fifo" && ! -e "$job_dir/runner-inbox.fifo" && ! -e "$job_dir/events.jsonl" ]] ||
    fail "Grok advise single-shot dispatch created live FIFO artifacts"

  printf 'direct advise\n' > "$direct/prompt.txt"
  FAKE_GROK_ARGS="$direct/args.txt" GROK_BIN="$fake" OMNILANE_REPO="$ROOT" \
    OMNILANE_SESSION_MODE=live OMNILANE_LIVE_REQUIRED=0 \
    "$worker" grok advise "$direct/workdir" \
    grok-advise-model high "$direct/prompt.txt" "$direct/job/out.txt"
  grep -Fq -- '--permission-mode' "$direct/args.txt" ||
    fail "Internal Grok advise worker did not downgrade to single-shot restricted mode"
  [[ ! -e "$direct/job/inbox.fifo" && ! -e "$direct/job/runner-inbox.fifo" ]] ||
    fail "Internal Grok advise worker created live FIFOs"

  mkfifo "$direct/live.fifo"
  before="$(shasum -a 256 "$direct/args.txt")"
  set +e
  OMNILANE_INBOX="$direct/live.fifo" FAKE_GROK_ARGS="$direct/args.txt" GROK_BIN="$fake" \
    "$grok_runner" advise "$direct/workdir" grok-advise-model \
    high "$direct/prompt.txt" "$direct/direct-out.txt" >"$direct/direct-stdout" 2>"$direct/direct-stderr"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "Direct Grok advise runner accepted a live FIFO"
  [[ "$(shasum -a 256 "$direct/args.txt")" == "$before" ]] ||
    fail "Direct Grok advise runner invoked CLI after rejecting live FIFO"
}

case "$CASE" in
 "")
    bash "$0" codex-killed-runner
    printf 'ok - killed Codex live runner reaps app-server process group\n'
    bash "$0" codex-app-server-eof-cleanup
    printf 'ok - Codex app-server prefers EOF cleanup and bounds TERM/KILL escalation\n'
    bash "$0" codex-close-grace-invariant
    printf 'ok - Codex close grace precedes worker escalation\n'
    bash "$0" codex-full-close-budget-invariant
    printf 'ok - Codex turn plus app-server close budget precedes worker escalation\n'
    bash "$0" codex-shutdown-grace
    printf 'ok - Codex live shutdown waits gracefully then escalates\n'
    /bin/bash "$ROOT/tests/test_live_close_deadline.sh"
    printf 'ok - bounded close preserves queued input and precedes jobs timeout\n'
    bash "$0" live-fail-fast
    printf 'ok - live mode rejects non-capable vendor\n'
    bash "$0" single-shot-claude
    printf 'ok - single-shot mode forces Claude one-shot\n'
    bash "$0" idle-cap
    printf 'ok - live mailbox idle cap closes session\n'
    bash "$0" claude-close-recovery
    printf 'ok - Claude close recovers completed result output\n'
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
    bash "$0" grok-killed-runner
    printf 'ok - killed Grok live runner reaps ACP process group\n'
    bash "$0" grok-close-grace-invariant
    printf 'ok - Grok close grace precedes worker escalation\n'
    bash "$0" grok-live-acp
    printf 'ok - Grok live ACP mailbox, cancellation, and incremental output\n'
    bash "$0" grok-model-errors
    printf 'ok - Grok model argv and prompt error correlation\n'
    bash "$0" grok-advise-fail-closed
    printf 'ok - Grok advise dispatch and internal FIFO fail closed\n'
    bash "$0" grok-live-fallback
    printf 'ok - Grok failed handshake degrades to single-shot\n'
    bash "$0" codex-probe-stdin-open
    printf 'ok - Codex initialize probe keeps stdin open\n'
    bash "$0" grok-probe-stdin-open
    printf 'ok - Grok initialize probe keeps stdin open\n'
    ;;
  live-fail-fast) case_live_fail_fast ;;
  single-shot-claude) case_single_shot_claude ;;
  idle-cap) case_idle_cap ;;
  claude-close-recovery) case_claude_close_recovers_result_output ;;
  gemini-schema) case_gemini_schema ;;
  json-escape) case_json_escape_round_trip ;;
  jobs-send-json-escape) case_jobs_send_json_escape_round_trip ;;
  codex-live-rpc) case_codex_live_rpc ;;
    codex-live-fallback) case_codex_live_fallback ;;
    codex-killed-runner) case_codex_killed_runner_reaps_app_server ;;
    codex-app-server-eof-cleanup) case_codex_app_server_eof_cleanup ;;
    codex-close-grace-invariant) case_codex_close_grace_precedes_worker_escalation ;;
    codex-full-close-budget-invariant) case_codex_full_close_budget_precedes_worker_escalation ;;
  codex-shutdown-grace) case_codex_shutdown_grace ;;
  codex-close-deadline) /bin/bash "$ROOT/tests/test_live_close_deadline.sh" ;;
    grok-killed-runner) case_grok_killed_runner_reaps_agent ;;
    grok-close-grace-invariant) case_grok_close_grace_precedes_worker_escalation ;;
    grok-live-acp) case_grok_live_acp ;;
    grok-model-errors) case_grok_model_and_prompt_errors ;;
    grok-advise-fail-closed) case_grok_advise_fail_closed ;;
    grok-live-fallback) case_grok_live_fallback ;;
    codex-probe-stdin-open) case_codex_probe_stdin_open ;;
    grok-probe-stdin-open) case_grok_probe_stdin_open ;;
  *) fail "unknown live mailbox test case: $CASE" ;;
esac
