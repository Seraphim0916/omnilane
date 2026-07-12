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

# Depth guard: a dispatched worker must not fan out again (quota-burn chains).
depth_guard() {
  local depth="${OMNILANE_DEPTH:-0}"
  if [[ "$depth" -ge 1 ]]; then
    echo "omnilane: refusing nested dispatch (OMNILANE_DEPTH=$depth)" >&2
    exit 86
  fi
}

# Same-cwd serial lock for codex: two concurrent `codex exec` in one repo
# corrupt the job index and cross-pollute pytest. mkdir is the portable lock.
acquire_cwd_lock() {
  local vendor="$1" key lockdir waited=0
  key="$(pwd | shasum | cut -c1-12)-$vendor"
  lockdir="$OMNILANE_HOME/locks/$key"
  mkdir -p "$OMNILANE_HOME/locks"
  while ! mkdir "$lockdir" 2>/dev/null; do
    sleep 2; waited=$((waited + 2))
    if [[ "$waited" -ge "${OMNILANE_LOCK_TIMEOUT:-600}" ]]; then
      echo "omnilane: lock timeout for $vendor in $(pwd)" >&2; exit 87
    fi
  done
  OMNILANE_LOCKDIR="$lockdir"
  trap 'rmdir "$OMNILANE_LOCKDIR" 2>/dev/null || true' EXIT
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
