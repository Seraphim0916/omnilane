#!/usr/bin/env bash
set -euo pipefail

unset OMNILANE_DEPTH OMNILANE_TIMEOUT OMNILANE_JOB_TIMEOUT
unset OMNILANE_JOB_SUPERVISED OMNILANE_IDLE_TIMEOUT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CASE="${1:-}"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-live-tests.XXXXXX")"

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
  cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$bin/codex"
  printf 'triage: codex codex-default low\n' > "$home/routing.local.yaml"
  out="$(OMNILANE_HOME="$home" CODEX_BIN="$bin/codex" \
    "$ROOT/scripts/dispatch.sh" --dry-run --live --background --vendor codex \
      triage x 2>&1)" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "--live silently accepted non-capable vendor"
  [[ "$out" == *"claude"* && "$out" == *"gemini"* ]] ||
    fail "--live error did not name live-capable vendors: $out"
  [[ ! -e "$home/jobs" ]] || fail "fail-fast created job state"
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

case "$CASE" in
  "")
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
    ;;
  live-fail-fast) case_live_fail_fast ;;
  single-shot-claude) case_single_shot_claude ;;
  idle-cap) case_idle_cap ;;
  gemini-schema) case_gemini_schema ;;
  json-escape) case_json_escape_round_trip ;;
  jobs-send-json-escape) case_jobs_send_json_escape_round_trip ;;
  *) fail "unknown live mailbox test case: $CASE" ;;
esac
