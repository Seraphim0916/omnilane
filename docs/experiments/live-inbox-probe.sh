#!/usr/bin/env bash
# Probe backing the roadmap's R2 "live mailbox" claim: does
# `claude -p --input-format stream-json` consume a user message that arrives
# AFTER the first turn has already been answered?
#
# Result on 2026-08-28: yes. `after-turn-1 results: 1` printed before the second
# message was written, and the same process answered it. rc=0.
#
# The single write fd (`exec 3>`) is load-bearing: a FIFO reader sees EOF as soon
# as the last writer closes, so a transient open/write/close per message would
# end the session instead of continuing it.
#
# If this script ever appears to produce nothing at all, do not conclude the
# worker never started: a hang leaves every echo sitting in the pipe buffer, so
# zero output and zero progress look identical from outside. Read the worker's
# own live-out.jsonl instead — that distinguishes "never ran" from "ran fine but
# could not print".
set -uo pipefail

WORK="$(mktemp -d)"
# Named files plus rmdir, not `rm -rf`: recursive deletes trip the operator's
# destructive-action guard and the temp dir would then leak on every run.
cleanup() {
  rm -- "$WORK/inbox.fifo" "$WORK/live-out.jsonl" "$WORK/live-err.log" 2>/dev/null
  rmdir "$WORK" 2>/dev/null
}
trap cleanup EXIT
cd "$WORK" || exit 1
mkfifo inbox.fifo

claude -p --verbose --input-format stream-json --output-format stream-json \
  --model claude-haiku-4-5-20251001 < inbox.fifo > live-out.jsonl 2> live-err.log &
CLAUDE_PID=$!

exec 3> inbox.fifo
msg() { printf '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"%s"}]}}\n' "$1" >&3; }

msg "記住暗號：鳳梨三號。只回 ok"

# Wait for turn 1 to be answered before turn 2 is even written to the pipe.
for _ in $(seq 1 60); do
  grep -q '"type":"result"' live-out.jsonl 2>/dev/null && break
  sleep 1
done
echo "after-turn-1 results: $(grep -c '"type":"result"' live-out.jsonl 2>/dev/null || echo 0)"

msg "剛才的暗號是什麼？只回暗號"

# Wait for turn 2, then stop. Closing the write end does NOT make the worker
# exit: `claude -p --input-format stream-json` keeps running after stdin EOF, so
# `wait` here would block forever. R2's `close` must terminate the worker, and
# completion must be read from result events, not from process exit.
for _ in $(seq 1 60); do
  [ "$(grep -c '"type":"result"' live-out.jsonl 2>/dev/null || echo 0)" -ge 2 ] && break
  sleep 1
done
exec 3>&-

RESULTS="$(grep -c '"type":"result"' live-out.jsonl 2>/dev/null || echo 0)"
echo "total results: $RESULTS"
grep -oE '"result":"[^"]{0,40}"' live-out.jsonl

kill "$CLAUDE_PID" 2>/dev/null

if [ "$RESULTS" -lt 2 ]; then
  KEEP="${TMPDIR:-/tmp}/live-inbox-probe-failed.$$"
  cp -R "$WORK" "$KEEP" && echo "FAILED — artifacts kept at $KEEP"
  exit 1
fi
