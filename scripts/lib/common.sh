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
  # GNU timeout creates a nested process group. Under the whole-job supervisor,
  # keep calls in its isolated group so one outer signal reaches every runner.
  if [[ "${OMNILANE_JOB_SUPERVISED:-0}" == "1" ]]; then
    command -v perl &>/dev/null || {
      echo "omnilane: supervised timeout requires perl" >&2; return 125
    }
    perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"
    return $?
  fi
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

current_pid() { # Call with direct redirection; command substitution records its short-lived shell.
  if [[ -n "${BASHPID:-}" ]]; then printf '%s' "$BASHPID"
  else (exec sh -c 'printf %s "$PPID"'); fi
}

write_current_pid_file() {
  local path="$1" tmp="${1}.tmp.$$-$RANDOM" old_umask rc=0
  old_umask="$(umask)"
  umask 077
  current_pid > "$tmp" || rc=$?
  umask "$old_umask"
  if [[ "$rc" -ne 0 ]]; then
    rm "$tmp" 2>/dev/null || true
    return "$rc"
  fi
  mv "$tmp" "$path"
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

expand_home_path() {
  case "$1" in
    \~/*) printf '%s' "$HOME/${1#\~/}" ;;
    *)    printf '%s' "$1" ;;
  esac
}

# Depth guard: a dispatched worker must not fan out again (quota-burn chains).
depth_guard() {
  local depth="${OMNILANE_DEPTH:-0}"
  [[ "$depth" =~ ^(0|[1-9][0-9]{0,8})$ ]] || {
    echo "omnilane: invalid OMNILANE_DEPTH (want 0..999999999)" >&2
    exit 2
  }
  if [[ "$depth" -ge 1 ]]; then
    echo "omnilane: refusing nested dispatch (OMNILANE_DEPTH=$depth)" >&2
    exit 86
  fi
}

# Same-cwd serial lock for codex: two concurrent `codex exec` in one repo
# corrupt the job index and cross-pollute pytest. mkdir is the portable lock.
read_lock_owner() {
  local path="$1" size value length
  local LC_ALL=C
  LOCK_OWNER_VALUE=""
  [[ -f "$path" && ! -L "$path" ]] || return 1
  size="$({ wc -c < "$path"; } 2>/dev/null | tr -d '[:space:]')" || return 2
  [[ "$size" =~ ^[0-9]+$ && "$size" -le 11 ]] || return 2
  value="$(cat "$path" 2>/dev/null)" || return 2
  length="${#value}"
  [[ "$size" -eq "$length" || "$size" -eq $((length + 1)) ]] || return 2
  [[ "$value" =~ ^[1-9][0-9]{0,9}$ ]] || return 2
  LOCK_OWNER_VALUE="$value"
}

prepare_lock_store() {
  local lock_root="$OMNILANE_HOME/locks"
  mkdir -p "$OMNILANE_HOME"
  if [[ -L "$lock_root" || ( -e "$lock_root" && ! -d "$lock_root" ) ]]; then
    echo "omnilane: unsafe lock store path (want a real directory): $lock_root" >&2
    exit 1
  fi
  [[ -d "$lock_root" ]] || mkdir -m 700 "$lock_root"
  [[ -d "$lock_root" && ! -L "$lock_root" ]] || {
    echo "omnilane: lock store changed while preparing it" >&2
    exit 1
  }
  chmod 700 "$lock_root"
}

write_lock_owner() {
  local path="$1" tmp="${1}.tmp.$$-$RANDOM" old_umask rc=0
  old_umask="$(umask)"
  umask 077
  current_pid > "$tmp" || rc=$?
  umask "$old_umask"
  if [[ "$rc" -eq 0 ]] && ln "$tmp" "$path" 2>/dev/null; then
    rm "$tmp"
    return 0
  fi
  rm "$tmp" 2>/dev/null || true
  return 1
}

acquire_cwd_lock() { # vendor, workdir — the lock keys on the TARGET dir, not $PWD
  local vendor="$1" dir="${2:-$PWD}" key lockdir waited=0 owner owner_state
  local empty_since=-1 empty_grace="${OMNILANE_LOCK_EMPTY_GRACE:-10}"
  local lock_timeout="${OMNILANE_LOCK_TIMEOUT:-600}"
  [[ "$empty_grace" =~ ^(0|[1-9][0-9]{0,5})$ ]] || {
    echo "omnilane: invalid OMNILANE_LOCK_EMPTY_GRACE (want 0..999999)" >&2
    exit 2
  }
  [[ "$lock_timeout" =~ ^[1-9][0-9]{0,5}$ ]] || {
    echo "omnilane: invalid OMNILANE_LOCK_TIMEOUT (want 1..999999)" >&2
    exit 2
  }
  dir="$(cd "$dir" 2>/dev/null && pwd -P)" || dir="$2"
  key="$(printf '%s' "$dir" | hash_str)-$vendor"
  lockdir="$OMNILANE_HOME/locks/$key"
  prepare_lock_store
  while ! mkdir "$lockdir" 2>/dev/null; do
    # Steal the lock if its owner is gone (crash / kill -9 leaves the dir behind).
    owner=""
    owner_state="empty"
    if [[ -e "$lockdir/pid" || -L "$lockdir/pid" ]]; then
      if read_lock_owner "$lockdir/pid"; then
        owner="$LOCK_OWNER_VALUE"
        owner_state="valid"
      else
        owner_state="invalid"
      fi
    fi
    if [[ "$owner_state" == "invalid" ]]; then
      rm "$lockdir/pid" 2>/dev/null || true
      if rmdir "$lockdir" 2>/dev/null; then
        continue
      fi
    elif [[ "$owner_state" == "valid" ]] && ! kill -0 "$owner" 2>/dev/null; then
      rm "$lockdir/pid" 2>/dev/null || true
      if rmdir "$lockdir" 2>/dev/null; then
        continue
      fi
    elif [[ "$owner_state" == "empty" ]]; then
      [[ "$empty_since" -ge 0 ]] || empty_since="$waited"
      if [[ $((waited - empty_since)) -ge "$empty_grace" ]]; then
        # The creator normally writes pid immediately after mkdir. A lock that
        # stays empty beyond the grace period is an interrupted acquisition.
        rmdir "$lockdir" 2>/dev/null || true
        empty_since=-1
        continue
      fi
    else
      empty_since=-1
    fi
    if [[ "$waited" -ge "$lock_timeout" ]]; then
      echo "omnilane: lock timeout for $vendor in $dir" >&2; exit 87
    fi
    sleep 2; waited=$((waited + 2))
  done
  # Real subshell PID, not $$: in a backgrounded subshell $$ is the (soon-dead)
  # parent, which would make the live lock look stale and steal-able.
  # If an empty lock was reclaimed while this process was descheduled, never
  # overwrite the replacement owner's PID when execution resumes.
  if ! write_lock_owner "$lockdir/pid"; then
    echo "omnilane: lost lock ownership for $vendor in $dir" >&2
    exit 87
  fi
  read_lock_owner "$lockdir/pid" || {
    echo "omnilane: invalid owner metadata after lock acquisition" >&2
    exit 87
  }
  OMNILANE_LOCK_OWNER="$LOCK_OWNER_VALUE"
  OMNILANE_LOCKDIR="$lockdir"
  trap 'release_cwd_lock' EXIT
}

release_cwd_lock() {
  [[ -n "${OMNILANE_LOCKDIR:-}" ]] || return 0
  # Only the recorded owner may remove the lock — a stale-steal may have
  # handed this path to a newer job.
  read_lock_owner "$OMNILANE_LOCKDIR/pid" || return 0
  [[ "$LOCK_OWNER_VALUE" == "${OMNILANE_LOCK_OWNER:-}" ]] || return 0
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
