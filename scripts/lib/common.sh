#!/usr/bin/env bash
# omnilane shared helpers — sourced by dispatch.sh and runners.

OMNILANE_HOME="${OMNILANE_HOME:-$HOME/.omnilane}"
OMNILANE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export OMNILANE_REPO

# Optional local overlay: proxies, auth wrappers, per-machine binaries.
# Publishable default is plain CLIs on PATH; power users add ~/.omnilane/local.sh.
[[ -f "$OMNILANE_HOME/local.sh" ]] && source "$OMNILANE_HOME/local.sh"

resolve_timeout_cmd() {
  if command -v timeout &>/dev/null; then echo "timeout";
  elif command -v gtimeout &>/dev/null; then echo "gtimeout";
  else echo ""; fi
}

# Portable watchdog: timeout/gtimeout when present, perl alarm otherwise
# (stock macOS has perl but no coreutils timeout). Warns when neither exists
# so a hung vendor CLI cannot silently block forever.
run_with_timeout() { # seconds, command...
  local secs="$1"; shift
  local t; t="$(resolve_timeout_cmd)"
  if [[ -n "$t" ]]; then
    "$t" "$secs" "$@"
  elif command -v perl &>/dev/null; then
    perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"
  else
    echo "omnilane: no timeout/gtimeout/perl — running without a watchdog" >&2
    "$@"
  fi
}

hash_str() { # stdin -> short stable token (sha256sum/shasum/cksum fallback)
  if command -v sha256sum &>/dev/null; then sha256sum | cut -c1-12
  elif command -v shasum &>/dev/null; then shasum | cut -c1-12
  else cksum | cut -d' ' -f1; fi
}

current_pid() { # subshell-accurate PID; $BASHPID needs bash>=4, macOS ships 3.2
  if [[ -n "${BASHPID:-}" ]]; then printf '%s' "$BASHPID"
  else (exec sh -c 'printf %s "$PPID"'); fi
}

vendor_bin() { # vendor -> binary (honors local.sh overrides)
  case "$1" in
    codex)  echo "${CODEX_BIN:-codex}" ;;
    claude) echo "${CLAUDE_BIN:-claude}" ;;
    grok)   echo "${GROK_BIN:-grok}" ;;
    gemini) echo "${AGY_BIN:-agy}" ;;
    *)      echo "" ;;
  esac
}

vendor_available() {
  # "exec" is the bring-your-own-gate vendor: the model field is a script path,
  # checked at dispatch time (the chain resolver cannot see it here).
  # "vote" is the built-in multi-model panel: needs >=2 voters, checked at dispatch.
  [[ "$1" == "exec" || "$1" == "vote" ]] && return 0
  local b; b="$(vendor_bin "$1")"
  [[ -n "$b" ]] && command -v "$b" >/dev/null 2>&1
}

# Depth guard: a dispatched worker must not fan out again (quota-burn chains).
depth_guard() {
  local depth="${OMNILANE_DEPTH:-0}"
  [[ "$depth" =~ ^(0|[1-9][0-9]{0,8})$ ]] || {
    echo "omnilane: invalid OMNILANE_DEPTH '$depth' (want 0..999999999)" >&2
    exit 2
  }
  if [[ "$depth" -ge 1 ]]; then
    echo "omnilane: refusing nested dispatch (OMNILANE_DEPTH=$depth)" >&2
    exit 86
  fi
}

# Same-cwd serial lock for codex: two concurrent `codex exec` in one repo
# corrupt the job index and cross-pollute pytest. mkdir is the portable lock.
acquire_cwd_lock() { # vendor, workdir — the lock keys on the TARGET dir, not $PWD
  local vendor="$1" dir="${2:-$PWD}" key lockdir waited=0 owner
  dir="$(cd "$dir" 2>/dev/null && pwd -P)" || dir="$2"
  key="$(printf '%s' "$dir" | hash_str)-$vendor"
  lockdir="$OMNILANE_HOME/locks/$key"
  mkdir -p "$OMNILANE_HOME/locks"
  while ! mkdir "$lockdir" 2>/dev/null; do
    # Steal the lock if its owner is gone (crash / kill -9 leaves the dir behind).
    owner="$(cat "$lockdir/pid" 2>/dev/null || true)"
    if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
      rm "$lockdir/pid" 2>/dev/null || true
      rmdir "$lockdir" 2>/dev/null || true
      continue
    fi
    sleep 2; waited=$((waited + 2))
    if [[ "$waited" -ge "${OMNILANE_LOCK_TIMEOUT:-600}" ]]; then
      echo "omnilane: lock timeout for $vendor in $dir" >&2; exit 87
    fi
  done
  # Real subshell PID, not $$: in a backgrounded subshell $$ is the (soon-dead)
  # parent, which would make the live lock look stale and steal-able.
  OMNILANE_LOCK_OWNER="$(current_pid)"
  printf '%s' "$OMNILANE_LOCK_OWNER" > "$lockdir/pid"
  OMNILANE_LOCKDIR="$lockdir"
  trap 'release_cwd_lock' EXIT
}

release_cwd_lock() {
  [[ -n "${OMNILANE_LOCKDIR:-}" ]] || return 0
  # Only the recorded owner may remove the lock — a stale-steal may have
  # handed this path to a newer job.
  [[ "$(cat "$OMNILANE_LOCKDIR/pid" 2>/dev/null)" == "${OMNILANE_LOCK_OWNER:-}" ]] || return 0
  rm "$OMNILANE_LOCKDIR/pid" 2>/dev/null || true
  rmdir "$OMNILANE_LOCKDIR" 2>/dev/null || true
}

# Cap payload so a runaway prompt cannot blow a worker's context.
truncate_payload() { # file, cap_bytes
  local f="$1" cap="${2:-140000}" size
  size=$(wc -c < "$f")
  if [[ "$size" -gt "$cap" ]]; then
    { head -c $((cap * 7 / 10)) "$f"
      printf '\n\n--- TRUNCATED (original: %s bytes) ---\n\n' "$size"
      tail -c $((cap / 4)) "$f"; } > "$f.tmp"
    mv "$f.tmp" "$f"
  fi
}

strip_ansi() { # file
  sed -i '' 's/\x1b\[[0-9;]*m//g' "$1" 2>/dev/null || sed -i 's/\x1b\[[0-9;]*m//g' "$1" 2>/dev/null || true
}
