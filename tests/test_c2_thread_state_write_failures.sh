#!/usr/bin/env bash
# Source: CODEX C2-B03 / MacStudio. All faults are fixture-local.

# BASH_ENV loads these wrappers in the real dispatch process. Dynamic scope
# exposes write_thread_state's exact temporary path without predicting RANDOM.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  date() {
    if [[ "${C2_FAULT:-}" == create && "${FUNCNAME[1]:-}" == write_thread_state &&
          "${tmp:-}" == "$C2_HOME/threads/.orchard.tmp."* ]]; then
      command ln -s "$C2_HOME/absent-parent/state" "$tmp" || return 91
      printf 'create\n' >> "$C2_HIT"
    fi
    command date "$@"
  }
  chmod() {
    if [[ "${C2_FAULT:-}" == chmod && "${1:-}" == 600 &&
          "${2:-}" == "$C2_HOME/threads/.orchard.tmp."* ]]; then
      [[ -s "$2" ]] || return 92
      printf 'chmod\n' >> "$C2_HIT"
      printf 'injected thread chmod failure\n' >&2
      return 73
    fi
    command chmod "$@"
  }
  mv() {
    if [[ "${C2_FAULT:-}" == mv &&
          "${1:-}" == "$C2_HOME/threads/.orchard.tmp."* &&
          "${2:-}" == "$C2_HOME/threads/orchard.json" ]]; then
      [[ -s "$1" ]] || return 93
      printf 'mv\n' >> "$C2_HIT"
      printf 'injected thread rename failure\n' >&2
      return 74
    fi
    command mv "$@"
  }
  return 0
fi

if [[ "${1:-}" == --fake-provider ]]; then
  shift
  session_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session-id|--resume) session_id="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  [[ -n "$session_id" ]] || exit 95
  printf '%s\n' "$session_id" >> "$C2_PROVIDER_CALLS"
  printf '{"type":"result","result":"C2 offline answer","session_id":"%s"}\n' "$session_id"
  exit 0
fi

if [[ "${1:-}" != --omnilane-offline-child ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:?}/c2-thread-write.XXXXXX")"
trap '/bin/rm -r -- "$TEST_ROOT"' EXIT
export C2_TEST_SCRIPT="$ROOT/tests/test_c2_thread_state_write_failures.sh"
export CLAUDE_CODE_SESSION_ID=c2-thread-write-foreman
mkdir -p "$TEST_ROOT/bin"
printf '#!/usr/bin/env bash\nexec /bin/bash "$C2_TEST_SCRIPT" --fake-provider "$@"\n' \
  > "$TEST_ROOT/bin/claude"
chmod 700 "$TEST_ROOT/bin/claude"
export PATH="$TEST_ROOT/bin:$PATH"

fail() { printf 'not ok - C2 thread write: %s\n' "$*" >&2; exit 1; }

assert_state() {
  python3 -c '
import json, pathlib, stat, sys
p = pathlib.Path(sys.argv[1])
s = json.loads(p.read_bytes())
assert s["turns"] == int(sys.argv[2]), s
assert s["name"] == "orchard" and s["vendor"] == "claude", s
assert s["session_id"] == pathlib.Path(sys.argv[3]).read_text().splitlines()[-1], s
assert stat.S_IMODE(p.stat().st_mode) == 0o600
' "$STATE" "$1" "$C2_PROVIDER_CALLS" || fail "state/0600/turn mismatch"
}

assert_clean_store() {
  python3 -c '
import pathlib, sys
p = pathlib.Path(sys.argv[1])
expected = {"orchard.json"} if sys.argv[2] == "existing" else set()
assert {f.name for f in p.iterdir()} == expected, list(p.iterdir())
' "$C2_HOME/threads" "$1" || fail "partial state or temporary artifact remains"
}

# The same 60s outer limit B04/B05 give each dispatch. dispatch's own --timeout only bounds
# the provider call, so a stall in preparation or state writing would otherwise hang the
# suite. The command runs in its own process group so its children end with it; exit 124
# = timed out.
DISPATCH_LIMIT=60
run_bounded() {
  python3 -c '
import os, signal, subprocess, sys, time
process = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    sys.exit(process.wait(timeout=float(sys.argv[1])))
except subprocess.TimeoutExpired:
    pgid = process.pid
    # The leader exiting on TERM does not empty the group: a descendant that ignores
    # TERM keeps it, so KILL goes to the remembered group after the grace period anyway.
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if process.poll() is not None:
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    break
            time.sleep(0.1)
    sys.exit(124)
' "$DISPATCH_LIMIT" "$@"
}

dispatch() {
  local label="$1" fault="$2"
  RC=0
  OMNILANE_HOME="$C2_HOME" C2_FAULT="$fault" BASH_ENV="$C2_TEST_SCRIPT" \
    run_bounded /bin/bash "$ROOT/scripts/dispatch.sh" --thread orchard --vendor claude \
    --model fake-thread-model --effort low --single-shot --workdir "$ROOT" \
    consult 'C2 state persistence fixture' > "$C2_HOME/$label.out" \
    2> "$C2_HOME/$label.err" || RC=$?
  [[ "$RC" != 124 ]] || fail "${C2_HOME##*/} $label: dispatch did not finish within ${DISPATCH_LIMIT}s (outer limit)"
}

for prior in existing first; do
  for fault in create chmod mv; do
    export C2_HOME="$TEST_ROOT/$prior-$fault"
    export C2_HIT="$C2_HOME/injection-hit"
    export C2_PROVIDER_CALLS="$C2_HOME/provider-calls"
    mkdir -p "$C2_HOME"
    printf 'consult: claude fake-thread-model low\n' > "$C2_HOME/routing.local.yaml"
    STATE="$C2_HOME/threads/orchard.json"
    before_turn=0
    if [[ "$prior" == existing ]]; then
      dispatch seed none
      [[ "$RC" == 0 ]] || fail "$prior/$fault seed exit $RC"
      assert_state 1
      cp "$STATE" "$C2_HOME/previous.json"
      before_turn=1
    fi

    dispatch failed "$fault"
    [[ "$RC" == 1 ]] || fail "$prior/$fault failure exit $RC (expected 1)"
    [[ "$(cat "$C2_HIT")" == "$fault" ]] || fail "$prior/$fault injection did not fire exactly once"
    notice='omnilane: thread orchard state could not be written'
    grep -Fxq "$notice" "$C2_HOME/failed.err" || fail "$prior/$fault missing stderr notice"
    grep -Fxq "$notice" "$C2_HOME/failed.out" || fail "$prior/$fault missing visible result notice"
    grep -Fq 'C2 offline answer' "$C2_HOME/failed.out" || fail "$prior/$fault provider did not finish"
    if [[ "$prior" == existing ]]; then
      cmp -s "$STATE" "$C2_HOME/previous.json" || fail "$fault changed previous bytes"
      assert_state 1
    else
      [[ ! -e "$STATE" && ! -L "$STATE" ]] || fail "$fault published a first partial state"
    fi
    assert_clean_store "$prior"

    # Recovery must read the preserved turn, not count the failed publication.
    dispatch recovered none
    [[ "$RC" == 0 ]] || fail "$prior/$fault recovery exit $RC"
    assert_state "$((before_turn + 1))"
    assert_clean_store existing
    [[ "$(wc -l < "$C2_PROVIDER_CALLS" | tr -d ' ')" == "$((before_turn + 2))" ]] \
      || fail "$prior/$fault unexpected provider invocation count"
    [[ "$(cat "$C2_HIT")" == "$fault" ]] || fail "$prior/$fault recovery reinjected fault"
    if grep -Fq "$notice" "$C2_HOME/recovered.err" "$C2_HOME/recovered.out"; then
      fail "$prior/$fault stale failure after recovery"
    fi
    printf 'ok - %s / %s: failure visible, state preserved, no debris, recovery 0600 turn %s\n' \
      "$prior" "$fault" "$((before_turn + 1))"
  done
done
printf 'PASS - C2 thread state write failures (6 injected failures and recoveries)\n'
