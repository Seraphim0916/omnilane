#!/usr/bin/env bash

# Reuse the suite's clean HOME/PATH and network guards, even when run standalone.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-c2-thread-names.XXXXXX")"
trap '/bin/rm -r -- "$TEST_ROOT"' EXIT
export OMNILANE_HOME="$TEST_ROOT/home"
export EXECUTOR_CALLS="$TEST_ROOT/executor.calls"
mkdir -p "$OMNILANE_HOME" "$TEST_ROOT/bin"
printf 'consult: claude fake-thread-model low\n' > "$OMNILANE_HOME/routing.local.yaml"

# Any attempted provider call is observable and fails without touching a service.
printf '%s\n' '#!/bin/bash' \
  'printf "called\n" >> "${EXECUTOR_CALLS:?}"' 'exit 99' > "$TEST_ROOT/bin/claude"
chmod +x "$TEST_ROOT/bin/claude"
export PATH="$TEST_ROOT/bin:$PATH"

fail() {
  printf 'not ok - C2 thread names: %s\n' "$1" >&2
  exit 1
}

# The same 60s outer limit B04/B05 give each dispatch. dispatch's own --timeout only bounds
# the provider call, so a stall before or after it would otherwise hang the suite. The
# command runs in its own process group so its children end with it; exit 124 = timed out.
DISPATCH_LIMIT=60
run_bounded() {
  python3 -c '
import os, signal, subprocess, sys
process = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    sys.exit(process.wait(timeout=float(sys.argv[1])))
except subprocess.TimeoutExpired:
    for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    sys.exit(124)
' "$DISPATCH_LIMIT" "$@"
}

count=0
check_name() {
  local expected="$1" label="$2" name="$3" rc=0 notice
  local -a flags=()
  # Rejected names deliberately use real dispatch, not --dry-run, so an early
  # rejection is required to prevent the fake executor from being called.
  [[ "$expected" != valid ]] || flags=(--dry-run)
  run_bounded bash "$ROOT/scripts/dispatch.sh" ${flags[@]+"${flags[@]}"} \
    --thread "$name" --vendor claude --model fake-thread-model --effort low \
    --single-shot --workdir "$ROOT" consult 'name boundary probe' \
    > "$TEST_ROOT/stdout" 2> "$TEST_ROOT/stderr" || rc=$?
  [[ "$rc" -ne 124 ]] || fail "$label: dispatch did not finish within ${DISPATCH_LIMIT}s (outer limit)"

  if [[ "$expected" == valid ]]; then
    [[ "$rc" -eq 0 ]] || fail "$label: expected exit 0, got $rc"
    grep -Fxq "thread=$name" "$TEST_ROOT/stdout" \
      || fail "$label: dry-run did not retain the exact thread name"
    grep -Fxq 'thread_mode=new' "$TEST_ROOT/stdout" \
      || fail "$label: missing new-thread dry-run plan"
  else
    [[ "$rc" -eq 2 ]] || fail "$label: expected exit 2, got $rc"
    notice="omnilane: invalid --thread name '$name' (want 1-64 characters: A-Z, a-z, 0-9, dot, underscore, hyphen; first character must be alphanumeric)"
    # Compare both channels in full, including embedded newlines in the name.
    [[ "$(cat "$TEST_ROOT/stdout")" == "$notice" ]] \
      || fail "$label: stdout refusal differs from the complete name rule"
    [[ "$(cat "$TEST_ROOT/stderr")" == "$notice" ]] \
      || fail "$label: stderr refusal differs from stdout"
  fi
  [[ ! -e "$EXECUTOR_CALLS" ]] || fail "$label: executor was called"
  [[ ! -e "$OMNILANE_HOME/threads" && ! -e "$OMNILANE_HOME/jobs" ]] \
    || fail "$label: dispatch created thread/job state"
  count=$((count + 1))
  printf 'ok - %s\n' "$label"
}

printf -v name64 '%064d' 0
check_name valid length-1-uppercase A
check_name valid length-1-lowercase z
check_name valid length-1-digit 0
check_name valid length-64 "$name64"
check_name invalid length-65 "${name64}0"
check_name valid internal-dot a.b
check_name valid internal-underscore a_b
check_name valid internal-hyphen a-b
check_name valid mixed-allowed A0.z_9-b
check_name valid trailing-punctuation a._-
check_name invalid first-dot .abc
check_name invalid first-underscore _abc
check_name invalid first-hyphen -abc
check_name invalid dot-only .
check_name invalid underscore-only _
check_name invalid hyphen-only -
check_name invalid space-only ' '
check_name invalid leading-space ' abc'
check_name invalid internal-space 'a b'
check_name invalid trailing-space 'abc '
check_name invalid leading-newline $'\nabc'
check_name invalid internal-newline $'a\nb'
check_name invalid trailing-newline $'abc\n'
check_name invalid tab $'a\tb'
check_name invalid traditional-chinese '對話'
check_name invalid internal-traditional-chinese 'a對話'
check_name invalid slash a/b
check_name invalid backslash 'a\b'
check_name invalid path-traversal ../bad
printf 'ok - C2 thread names: %s cases; no executor calls or thread/job state\n' "$count"
