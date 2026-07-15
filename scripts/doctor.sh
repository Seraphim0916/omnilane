#!/usr/bin/env bash
set -u
# Read-only health report for routing, state, watchdog, and optional UI support.

[[ $# -eq 0 ]] || { echo "usage: omnilane doctor" >&2; exit 2; }
REPO="${OMNILANE_DOCTOR_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OMNILANE_HOME="${OMNILANE_HOME:-$HOME/.omnilane}"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

report() {
  local level="$1" check="$2" message="$3"
  # Diagnostic inputs include user-controlled paths and routing errors. Strip
  # terminal control bytes so a failed check cannot forge the report display.
  message="$(printf '%s' "$message" | LC_ALL=C tr -d '\000-\010\013\014\016-\037\177')"
  printf '%-5s %-12s %s\n' "$level" "$check" "$message"
  case "$level" in
    PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
    WARN) WARN_COUNT=$((WARN_COUNT + 1)) ;;
    FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
  esac
}

DISPATCH="$REPO/scripts/dispatch.sh"
if [[ -x "$DISPATCH" ]]; then
  report PASS dispatch "$DISPATCH is executable"
else
  report FAIL dispatch "$DISPATCH is missing or not executable"
fi

if [[ -r "$REPO/routing.yaml" ]]; then
  report PASS config "$REPO/routing.yaml is readable"
else
  report FAIL config "$REPO/routing.yaml is missing or unreadable"
fi

if [[ -x "$DISPATCH" ]]; then
  route_output="$(OMNILANE_HOME="$OMNILANE_HOME" /bin/bash "$DISPATCH" --list 2>&1)"
  route_rc=$?
  route_count="$(printf '%s\n' "$route_output" | awk 'NF { count++ } END { print count + 0 }')"
  usable_count="$(printf '%s\n' "$route_output" | awk \
    'NF && $0 !~ /unavailable/ && $0 !~ /^[^:]+:[[:space:]]+off([[:space:]]|$)/ { count++ } END { print count + 0 }')"
  if [[ "$route_rc" -ne 0 ]]; then
    first_error="$(printf '%s\n' "$route_output" | sed -n '1p')"
    report FAIL routing "effective routing failed: ${first_error:-unknown error}"
  elif [[ "$route_count" -eq 0 ]]; then
    report FAIL routing "effective routing is empty"
  elif [[ "$usable_count" -eq 0 ]]; then
    report WARN routing "$route_count lanes parsed, but none is currently usable"
  else
    report PASS routing "$route_count lanes parsed; $usable_count currently usable"
  fi
fi

if [[ ! -e "$OMNILANE_HOME" ]]; then
  report WARN state "$OMNILANE_HOME does not exist yet; doctor left it unchanged"
elif [[ ! -d "$OMNILANE_HOME" ]]; then
  report FAIL state "$OMNILANE_HOME exists but is not a directory"
elif [[ ! -r "$OMNILANE_HOME" || ! -w "$OMNILANE_HOME" || ! -x "$OMNILANE_HOME" ]]; then
  report FAIL state "$OMNILANE_HOME is not readable, writable, and searchable"
else
  report PASS state "$OMNILANE_HOME is accessible"
fi

if [[ -f "$OMNILANE_HOME/local.sh" ]]; then
  if /bin/bash -n "$OMNILANE_HOME/local.sh" 2>/dev/null; then
    report PASS local-config "$OMNILANE_HOME/local.sh syntax is valid"
  else
    report FAIL local-config "$OMNILANE_HOME/local.sh has invalid Bash syntax"
  fi
fi

if [[ -L "$OMNILANE_HOME/jobs" ||
      ( -e "$OMNILANE_HOME/jobs" && ! -d "$OMNILANE_HOME/jobs" ) ]]; then
  report FAIL job-privacy "$OMNILANE_HOME/jobs must be a real directory, not a symlink or file"
elif [[ -d "$OMNILANE_HOME/jobs" ]]; then
  jobs_mode="$(stat -f '%Lp' "$OMNILANE_HOME/jobs" 2>/dev/null || stat -c '%a' "$OMNILANE_HOME/jobs" 2>/dev/null || true)"
  if [[ "$jobs_mode" =~ ^[0-7]*00$ ]]; then
    report PASS job-privacy "$OMNILANE_HOME/jobs mode is $jobs_mode"
  elif [[ -n "$jobs_mode" ]]; then
    report WARN job-privacy "$OMNILANE_HOME/jobs mode is $jobs_mode; owner-only 700 is safer"
  else
    report WARN job-privacy "could not determine $OMNILANE_HOME/jobs permissions"
  fi
fi

if [[ -L "$OMNILANE_HOME/locks" ||
      ( -e "$OMNILANE_HOME/locks" && ! -d "$OMNILANE_HOME/locks" ) ]]; then
  report FAIL lock-store "$OMNILANE_HOME/locks must be a real directory, not a symlink or file"
fi

if command -v timeout >/dev/null 2>&1; then
  report PASS watchdog "timeout is available"
elif command -v gtimeout >/dev/null 2>&1; then
  report PASS watchdog "gtimeout is available"
elif command -v perl >/dev/null 2>&1; then
  report PASS watchdog "Perl alarm fallback is available"
else
  report FAIL watchdog "timeout, gtimeout, and perl are all unavailable"
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
      >/dev/null 2>&1; then
    python_version="$(python3 -c 'import platform; print(platform.python_version())')"
    report PASS live-ui "Python $python_version supports the optional UI"
  else
    report WARN live-ui "Python 3.9 or newer is required for the optional UI"
  fi
else
  report WARN live-ui "python3 is absent; model routing still works"
fi

warning_suffix=s
[[ "$WARN_COUNT" -eq 1 ]] && warning_suffix=""
printf '\nSummary: %s passed, %s warning%s, %s failed\n' \
  "$PASS_COUNT" "$WARN_COUNT" "$warning_suffix" "$FAIL_COUNT"
[[ "$FAIL_COUNT" -eq 0 ]]
