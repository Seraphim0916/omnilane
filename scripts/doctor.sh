#!/usr/bin/env bash
set -u
# Read-only health report for routing, state, watchdog, and optional UI support.

JSON_MODE=0
if [[ $# -eq 1 && "$1" == "--json" ]]; then
  JSON_MODE=1
elif [[ $# -ne 0 ]]; then
  echo "usage: omnilane doctor [--json]" >&2
  exit 2
fi
REPO="${OMNILANE_DOCTOR_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OMNILANE_HOME="${OMNILANE_HOME:-$HOME/.omnilane}"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
JSON_REPORTS=""
JSON_FIRST=1

json_escape() {
  local s="$1" out="" ch escaped code i
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:i:1}"
    case "$ch" in
      '"') out="$out\\\"" ;;
      '\\') out="$out\\\\" ;;
      $'\b') out="$out\\b" ;;
      $'\f') out="$out\\f" ;;
      $'\n') out="$out\\n" ;;
      $'\r') out="$out\\r" ;;
      $'\t') out="$out\\t" ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'$ch"
        if [[ "$code" -ge 0 && "$code" -lt 32 ]]; then
          printf -v escaped '\\u%04x' "$code"
          out="$out$escaped"
        else
          out="$out$ch"
        fi
        ;;
    esac
  done
  printf '%s' "$out"
}

report() {
  local level="$1" check="$2" message="$3"
  # Diagnostic inputs include user-controlled paths and routing errors. Strip
  # terminal control bytes so a failed check cannot forge the report display.
  message="$(printf '%s' "$message" | LC_ALL=C tr -d '\000-\010\013\014\016-\037\177')"
  if [[ "$JSON_MODE" -eq 1 ]]; then
    [[ "$JSON_FIRST" -eq 1 ]] || JSON_REPORTS="$JSON_REPORTS,"
    JSON_FIRST=0
    JSON_REPORTS="$JSON_REPORTS{\"level\":\"$(json_escape "$level")\",\"check\":\"$(json_escape "$check")\",\"message\":\"$(json_escape "$message")\"}"
  else
    printf '%-5s %-12s %s\n' "$level" "$check" "$message"
  fi
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

# Vendor CLI availability. Runners resolve each vendor's binary through a *_BIN
# override (default name otherwise) and source local.sh, so probe the same way
# in a subshell — set +u because a machine-local local.sh may reference its own
# unset vars, and the subshell keeps any side effects out of this report.
vendor_line="$(
  set +u
  [[ -f "$OMNILANE_HOME/local.sh" ]] && . "$OMNILANE_HOME/local.sh" 2>/dev/null
  present=""; absent=""
  for spec in "codex:${CODEX_BIN:-codex}" "claude:${CLAUDE_BIN:-claude}" \
              "grok:${GROK_BIN:-grok}" "gemini:${AGY_BIN:-agy}" \
              "kimi:${KIMI_BIN:-kimi}" "qwen:${QWEN_BIN:-qwen}" \
              "opencode:${OPENCODE_BIN:-opencode}"; do
    name="${spec%%:*}"; bin="${spec#*:}"
    if command -v "$bin" >/dev/null 2>&1; then present="$present $name"; else absent="$absent $name"; fi
  done
  if [[ -n "${OPENROUTER_API_KEY:-}" ]] && command -v curl >/dev/null 2>&1; then
    present="$present openrouter"
  else
    absent="$absent openrouter"
  fi
  printf '%s|%s' "${present# }" "${absent# }"
)"
vendor_present="${vendor_line%%|*}"
vendor_absent="${vendor_line#*|}"
if [[ -n "$vendor_present" ]]; then
  report PASS vendors "present: $vendor_present${vendor_absent:+; missing: $vendor_absent}"
else
  report WARN vendors "no vendor CLI reachable${vendor_absent:+ (missing: $vendor_absent)}; every lane degrades to off"
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

if [[ "$JSON_MODE" -eq 1 ]]; then
  ok=true
  [[ "$FAIL_COUNT" -eq 0 ]] || ok=false
  printf '{"ok":%s,"checks":[%s],"summary":{"passed":%s,"warnings":%s,"failed":%s}}\n' \
    "$ok" "$JSON_REPORTS" "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
else
  warning_suffix=s
  [[ "$WARN_COUNT" -eq 1 ]] && warning_suffix=""
  printf '\nSummary: %s passed, %s warning%s, %s failed\n' \
    "$PASS_COUNT" "$WARN_COUNT" "$warning_suffix" "$FAIL_COUNT"
fi
[[ "$FAIL_COUNT" -eq 0 ]]
