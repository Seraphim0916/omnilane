#!/usr/bin/env bash
set -u
# Read-only health report for routing, state, watchdog, and optional UI support.

JSON_MODE=0
STRICT_MODE=0
PROBE_VENDOR=""
PROBE_TIMEOUT=30
PROBE_TIMEOUT_GIVEN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON_MODE=1 ;;
    --strict) STRICT_MODE=1 ;;
    --probe)
      [[ $# -ge 2 ]] || { echo "omnilane: --probe needs a vendor" >&2; exit 2; }
      PROBE_VENDOR="$2"; shift ;;
    --probe-timeout)
      [[ $# -ge 2 ]] || { echo "omnilane: --probe-timeout needs seconds" >&2; exit 2; }
      PROBE_TIMEOUT="$2"; PROBE_TIMEOUT_GIVEN=1; shift ;;
    *)
      echo "usage: omnilane doctor [--json] [--strict] [--probe V [--probe-timeout SEC]]" >&2
      exit 2
      ;;
  esac
  shift
done
[[ "$PROBE_TIMEOUT_GIVEN" -eq 0 || -n "$PROBE_VENDOR" ]] || {
  echo "omnilane: --probe-timeout requires --probe V" >&2
  exit 2
}
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${OMNILANE_DOCTOR_REPO:-$SCRIPT_ROOT}"
OMNILANE_HOME="${OMNILANE_HOME:-$HOME/.omnilane}"
PROBE_SCRIPT="${OMNILANE_PROVIDER_PROBE_SCRIPT:-$REPO/scripts/provider-probe.sh}"
GOAL_LOOP="${OMNILANE_DOCTOR_GOAL_LOOP:-$REPO/scripts/lib/goal-loop.sh}"
OVERLAY_HEALTH="${OMNILANE_DOCTOR_OVERLAY_HEALTH:-$REPO/scripts/lib/overlay_health.py}"
# shellcheck disable=SC1091
source "$SCRIPT_ROOT/scripts/lib/live-protocol.sh"
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
      \\) out="$out\\\\" ;;
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

goals_store="$OMNILANE_HOME/goals"
if [[ ! -f "$GOAL_LOOP" || -L "$GOAL_LOOP" ]]; then
  report FAIL goal-orchestrator "$GOAL_LOOP must be a regular file"
elif ! /bin/bash -n "$GOAL_LOOP" 2>/dev/null; then
  report FAIL goal-orchestrator "$GOAL_LOOP has invalid bash syntax"
elif [[ -L "$goals_store" || ( -e "$goals_store" && ! -d "$goals_store" ) ]]; then
  report FAIL goal-orchestrator "$goals_store must be a real directory, not a symlink or file"
elif [[ -d "$goals_store" ]]; then
  goals_mode="$(stat -f '%Lp' "$goals_store" 2>/dev/null || true)"
  if [[ ! "$goals_mode" =~ ^[0-7]{3,4}$ ]]; then
    goals_mode="$(stat -c '%a' "$goals_store" 2>/dev/null || true)"
  fi
  if [[ "$goals_mode" =~ ^0?700$ ]]; then
    report PASS goal-orchestrator "goal-loop.sh parses; goals store mode is $goals_mode"
  elif [[ -n "$goals_mode" ]]; then
    report WARN goal-orchestrator \
      "goal-loop.sh parses; goals store mode is $goals_mode, owner-only 700 is safer"
  else
    report WARN goal-orchestrator \
      "goal-loop.sh parses; could not determine goals store permissions"
  fi
else
  report PASS goal-orchestrator "goal-loop.sh parses; goals store is not present yet"
fi

completion_plugin_state() {
  local config_dir state
  config_dir="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}"
  state="$(perl -MJSON::PP -e '
    use strict;
    use warnings;
    my ($repo, @paths) = @ARGV;
    my ($seen, $enabled, $source) = (0, undef, undef);
    for my $path (@paths) {
      next unless -e $path;
      $seen = 1;
      open my $fh, "<", $path or do { print "unknown"; exit 0 };
      local $/;
      my $settings = eval { decode_json(<$fh>) };
      close $fh;
      if ($@ || ref($settings) ne "HASH") { print "unknown"; exit 0 }
      my $plugins = $settings->{enabledPlugins};
      if (ref($plugins) eq "HASH" && exists $plugins->{"omnilane\@omnilane"}) {
        $enabled = $plugins->{"omnilane\@omnilane"};
      }
      my $markets = $settings->{extraKnownMarketplaces};
      if (ref($markets) eq "HASH" && exists $markets->{omnilane}) {
        my $market = $markets->{omnilane};
        $source = ref($market) eq "HASH" ? $market->{source} : undef;
      }
    }
    if (!$seen) { print "unknown"; exit 0 }
    my $enabled_ok = ref($enabled) &&
      eval { $enabled->isa("JSON::PP::Boolean") } && $enabled;
    my $source_ok = ref($source) eq "HASH" &&
      defined($source->{source}) && !ref($source->{source}) &&
      defined($source->{path}) && !ref($source->{path}) &&
      $source->{source} eq "directory" && $source->{path} eq $repo;
    print $enabled_ok && $source_ok ? "active" : "inactive";
  ' "$REPO" "$config_dir/settings.json" "$config_dir/settings.local.json" 2>/dev/null)" || state=unknown
  case "$state" in
    active|inactive|unknown) printf '%s\n' "$state" ;;
    *) printf '%s\n' unknown ;;
  esac
}

completion_manifest_registers_hook() {
  local manifest="$1"
  [[ -r "$manifest" ]] || return 1
  awk '
    { content = content $0 }
    END {
      if (content ~ /"UserPromptSubmit"[[:space:]]*:[[:space:]]*\[/ &&
          content ~ /"UserPromptSubmit".*report-completions\.sh/) exit 0
      exit 1
    }
  ' "$manifest"
}

completion_plugin="$(completion_plugin_state)"
completion_settings_dir="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}"
completion_script="$REPO/hooks/report-completions.sh"
completion_manifest="$REPO/hooks/hooks.json"
completion_inbox="$OMNILANE_HOME/inbox"
completion_plugin_ok=0
completion_script_ok=0
completion_manifest_ok=0
completion_inbox_ok=1
completion_pending=0
completion_reason=""

case "$completion_plugin" in
  active)
    completion_plugin_ok=1
    report PASS completion-plugin "omnilane@omnilane is enabled according to Claude Code settings"
    ;;
  inactive)
    completion_reason="omnilane@omnilane is missing or disabled"
    report WARN completion-plugin "$completion_reason according to Claude Code settings"
    ;;
  *)
    completion_reason="Claude Code plugin state is unknown"
    report WARN completion-plugin "$completion_reason; settings files were absent, unreadable, or invalid"
    ;;
esac

if [[ -x "$completion_script" ]]; then
  completion_script_ok=1
  report PASS completion-hook "$completion_script exists and is executable"
elif [[ -e "$completion_script" ]]; then
  completion_reason="${completion_reason:+$completion_reason; }$completion_script is not executable"
  report WARN completion-hook "$completion_script exists but is not executable"
else
  completion_reason="${completion_reason:+$completion_reason; }$completion_script is missing"
  report WARN completion-hook "$completion_script is missing"
fi

if completion_manifest_registers_hook "$completion_manifest"; then
  completion_manifest_ok=1
  report PASS completion-manifest "$completion_manifest from the current checkout registers UserPromptSubmit"
else
  completion_reason="${completion_reason:+$completion_reason; }$completion_manifest does not register UserPromptSubmit"
  report WARN completion-manifest "$completion_manifest from the current checkout does not register UserPromptSubmit for report-completions.sh"
fi

if [[ -L "$completion_inbox" || ( -e "$completion_inbox" && ! -d "$completion_inbox" ) ]]; then
  completion_inbox_ok=0
  completion_reason="${completion_reason:+$completion_reason; }$completion_inbox is not a real directory"
  report WARN completion-inbox "$completion_inbox must be a real directory"
elif [[ -d "$completion_inbox" ]]; then
  for completion_record in "$completion_inbox"/*.json; do
    [[ -f "$completion_record" && ! -L "$completion_record" ]] || continue
    completion_pending=$((completion_pending + 1))
  done
  report PASS completion-inbox "$completion_pending pending records in $completion_inbox"
else
  report PASS completion-inbox "0 pending records; $completion_inbox does not exist yet"
fi

if [[ "$completion_plugin_ok" -eq 1 && "$completion_script_ok" -eq 1 &&
      "$completion_manifest_ok" -eq 1 && "$completion_inbox_ok" -eq 1 ]]; then
  report PASS completion-notice "active; $completion_pending pending records; manifest read from $completion_manifest"
else
  if [[ "$completion_plugin" == inactive ]]; then
    completion_reason="$completion_reason; run: claude plugin marketplace add \"$REPO\"; claude plugin install omnilane@omnilane"
  elif [[ "$completion_plugin" == unknown ]]; then
    completion_reason="$completion_reason; inspect $completion_settings_dir/settings.json and settings.local.json"
  fi
  report WARN completion-notice "inactive: $completion_reason; manifest read from $completion_manifest"
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
  jobs_mode="$(stat -f '%Lp' "$OMNILANE_HOME/jobs" 2>/dev/null || true)"
  if [[ ! "$jobs_mode" =~ ^[0-7]{3,4}$ ]]; then
    jobs_mode="$(stat -c '%a' "$OMNILANE_HOME/jobs" 2>/dev/null || true)"
  fi
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
  # Direct-API vendors (OpenAI-compatible, no CLI): reachable iff curl exists
  # and the matching API key is set. Keep aligned with
  # OMNILANE_DIRECT_API_VENDORS in lib/common.sh.
  if command -v curl >/dev/null 2>&1; then
    for spec in "openrouter:${OPENROUTER_API_KEY:-}" "deepseek:${DEEPSEEK_API_KEY:-}" \
                "zai:${ZAI_API_KEY:-}" "mistral:${MISTRAL_API_KEY:-}" \
                "groq:${GROQ_API_KEY:-}" "cerebras:${CEREBRAS_API_KEY:-}"; do
      name="${spec%%:*}"; key="${spec#*:}"
      if [[ -n "$key" ]]; then present="$present $name"; else absent="$absent $name"; fi
    done
  else
    absent="$absent openrouter deepseek zai mistral groq cerebras"
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

# A real inference call is explicit and single-vendor only. Default doctor
# remains local/offline. Never relay provider output or runner stderr.
live_capable_present=""
live_unavailable_present=""
resolved_codex_bin="$(
  set +u
  [[ -f "$OMNILANE_HOME/local.sh" ]] && . "$OMNILANE_HOME/local.sh" 2>/dev/null
  printf '%s' "${CODEX_BIN:-codex}"
)"
resolved_grok_bin="$(
  set +u
  [[ -f "$OMNILANE_HOME/local.sh" ]] && . "$OMNILANE_HOME/local.sh" 2>/dev/null
  printf '%s' "${GROK_BIN:-grok}"
)"
for name in $vendor_present; do
  case "$name" in
    claude|gemini)
      live_capable_present="$live_capable_present $name"
      ;;
    codex)
      if codex_live_surface_available "$resolved_codex_bin"; then
        live_capable_present="$live_capable_present codex"
        report PASS codex-live "app-server initialize handshake succeeded"
      else
        live_unavailable_present="$live_unavailable_present codex"
        report PASS codex-live "unavailable: app-server initialize handshake failed; upgrade codex"
      fi
      ;;
    grok)
      if grok_live_surface_available "$resolved_grok_bin"; then
        live_capable_present="$live_capable_present grok"
        report PASS grok-live "agent stdio initialize handshake succeeded"
      else
        live_unavailable_present="$live_unavailable_present grok"
        report PASS grok-live "unavailable: agent stdio initialize handshake failed"
      fi
      ;;
    *)
      live_unavailable_present="$live_unavailable_present $name"
      ;;
  esac
done
if [[ -n "$live_capable_present" ]]; then
  report PASS live-capable "${live_capable_present# }"
else
  report WARN live-capable "none"
fi
if [[ -n "$live_unavailable_present" ]]; then
  report PASS live-unavailable "${live_unavailable_present# }"
else
  report PASS live-unavailable "none"
fi

# live-capable above answers "does this CLI support a live session", which stays
# true while the AA gate refuses every dispatch. Nothing else loads the overlay,
# so one drifted evidence hash used to go unreported by an all-green doctor.
overlay_path="$(
  set +u
  [[ -f "$OMNILANE_HOME/local.sh" ]] && . "$OMNILANE_HOME/local.sh" 2>/dev/null
  printf '%s' "${OMNILANE_AA_TRANSPORT_OVERLAY:-}"
)"
if [[ -z "$overlay_path" ]]; then
  report PASS transport-overlay "no overlay configured; every runtime mapping stays unverified"
elif ! command -v python3 >/dev/null 2>&1; then
  report WARN transport-overlay "python3 is absent; cannot load the AA transport overlay"
elif [[ ! -r "$OVERLAY_HEALTH" ]]; then
  report WARN transport-overlay "$OVERLAY_HEALTH is missing"
else
  overlay_line="$(OMNILANE_AA_TRANSPORT_OVERLAY="$overlay_path" \
    python3 "$OVERLAY_HEALTH" "$REPO" 2>&1)"
  overlay_level="${overlay_line%%	*}"
  overlay_message="${overlay_line#*	}"
  case "$overlay_level" in
    PASS|WARN|FAIL) report "$overlay_level" transport-overlay "$overlay_message" ;;
    *) report WARN transport-overlay "unreadable overlay health output: $overlay_line" ;;
  esac
fi

if [[ -n "$PROBE_VENDOR" ]]; then
  if [[ ! -x "$PROBE_SCRIPT" ]]; then
    report FAIL provider-probe "probe runner is unavailable"
  else
    probe_output="$(OMNILANE_PROBE_REPO="$REPO" "$PROBE_SCRIPT" \
      --vendor "$PROBE_VENDOR" --timeout "$PROBE_TIMEOUT" 2>/dev/null)"
    probe_rc=$?
    if [[ "$probe_rc" -eq 0 ]]; then
      report PASS provider-probe "$PROBE_VENDOR bounded live inference succeeded"
    else
      report FAIL provider-probe "$PROBE_VENDOR bounded live inference failed (exit $probe_rc)"
    fi
    : "$probe_output"
  fi
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
  [[ "$FAIL_COUNT" -eq 0 && ( "$STRICT_MODE" -eq 0 || "$WARN_COUNT" -eq 0 ) ]] || ok=false
  strict=false
  [[ "$STRICT_MODE" -eq 0 ]] || strict=true
  printf '{"ok":%s,"checks":[%s],"summary":{"passed":%s,"warnings":%s,"failed":%s},"strict":%s}\n' \
    "$ok" "$JSON_REPORTS" "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$strict"
else
  warning_suffix=s
  [[ "$WARN_COUNT" -eq 1 ]] && warning_suffix=""
  printf '\nSummary: %s passed, %s warning%s, %s failed\n' \
    "$PASS_COUNT" "$WARN_COUNT" "$warning_suffix" "$FAIL_COUNT"
fi
[[ "$FAIL_COUNT" -eq 0 && ( "$STRICT_MODE" -eq 0 || "$WARN_COUNT" -eq 0 ) ]]
