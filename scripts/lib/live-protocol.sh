#!/usr/bin/env bash

# Shared live-mailbox protocol differences. Callers provide json_escape().

live_capable_vendors() {
  printf 'claude, gemini, codex'
}

live_vendor_capable() {
  case "$1" in
    claude|gemini|codex) return 0 ;;
    *) return 1 ;;
  esac
}

codex_live_surface_available() {
  local bin="${1:-${CODEX_BIN:-codex}}"
  command -v "$bin" >/dev/null 2>&1 || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  python3 - "$bin" 2>/dev/null <<'PY'
import json
import os
import select
import signal
import subprocess
import sys

process = None
ok = False
try:
    process = subprocess.Popen(
        [sys.argv[1], "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "omnilane-probe", "version": "1"}},
    }
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    process.stdin.close()
    if select.select([process.stdout], [], [], 3)[0]:
        response = json.loads(process.stdout.readline())
        ok = isinstance(response, dict) and isinstance(response.get("result"), dict)
except (OSError, ValueError, json.JSONDecodeError):
    ok = False
finally:
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
sys.exit(0 if ok else 1)
PY
}

live_encode_message() {
  local vendor="$1" job_id="$2" foreman_session="$3" text="$4"
  case "$vendor" in
    claude)
      printf '{"type":"user","omnilane_job_id":"%s","foreman_session":"%s","message":{"role":"user","content":[{"type":"text","text":"%s"}]}}' \
        "$(json_escape "$job_id")" "$(json_escape "$foreman_session")" \
        "$(json_escape "$text")"
      ;;
    gemini)
      printf '{"event":"user","message":{"role":"user","content":[{"type":"text","text":"%s"}]}}' \
        "$(json_escape "$text")"
      ;;
    codex)
      printf '{"type":"codex-user","text":"%s"}' "$(json_escape "$text")"
      ;;
    *) return 2 ;;
  esac
}

# Cheap structural check, not a parse. Two properties matter more here than
# precision. It must not fork: the drain loop runs one call per stream event
# between inbox reads, so a per-event process would leave an operator's
# `jobs.sh send` waiting behind a busy stream. And it must not be able to fail
# because a parser is missing: a validator that cannot run would judge every
# event invalid, activity would never refresh, and the idle cap would kill
# healthy jobs. Strict parsing still guards the places where correctness
# depends on it (json_file_round_trip_valid).
live_event_is_valid() {
  local event="$1" lead trail
  lead="${event%%[![:space:]]*}"
  event="${event#"$lead"}"
  trail="${event##*[![:space:]]}"
  event="${event%"$trail"}"
  [[ "$event" == "{"*"}" ]]
}

live_event_is_result() {
  local vendor="$1" event="$2" pattern
  case "$vendor" in
    claude) pattern='"type"[[:space:]]*:[[:space:]]*"result"' ;;
    gemini) pattern='"event"[[:space:]]*:[[:space:]]*"result"' ;;
    codex) pattern='"method"[[:space:]]*:[[:space:]]*"turn/completed"' ;;
    *) return 2 ;;
  esac
  [[ "$event" =~ $pattern ]]
}

live_event_is_success() {
  local vendor="$1" event="$2" pattern
  live_event_is_result "$vendor" "$event" || return 1
  case "$vendor" in
    claude)
      pattern='"is_error"[[:space:]]*:[[:space:]]*true'
      [[ ! "$event" =~ $pattern ]]
      ;;
    gemini)
      pattern='"status"[[:space:]]*:[[:space:]]*"SUCCESS"'
      [[ "$event" =~ $pattern ]]
      ;;
    codex)
      pattern='"status"[[:space:]]*:[[:space:]]*"completed"'
      [[ "$event" =~ $pattern ]]
      ;;
    *) return 2 ;;
  esac
}

live_count_result_events() {
  local vendor="$1" path="$2" event count=0
  [[ -f "$path" && ! -L "$path" ]] || {
    printf '0'
    return 0
  }
  while IFS= read -r event || [[ -n "$event" ]]; do
    if live_event_is_result "$vendor" "$event"; then
      count=$((count + 1))
    fi
  done < "$path"
  printf '%s' "$count"
}
