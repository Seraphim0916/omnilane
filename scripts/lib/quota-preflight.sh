#!/usr/bin/env bash
# Sourced only by dispatch. Never sourced by a worker, retry, or resume path.
QUOTA_DECISION=""
QUOTA_EXCLUDED_VENDOR=""
QUOTA_NOTICE=""

quota_emit_notice() {
  [[ -z "$QUOTA_NOTICE" ]] || printf '%s\n' "$QUOTA_NOTICE" >&2
  return 0
}

quota_candidate_capable() {
  local vendor="$1" model="$2" effort="$3" rc=0
  local lane_job_var
  lane_job_var="OMNILANE_JOB_TIMEOUT_$(printf '%s' "${LANE//-/_}" | tr '[:lower:]' '[:upper:]')"
  [[ "$vendor" != "off" ]] || return 1
  if [[ "$vendor" == "vote" && ",$model," == *",$QUOTA_EXCLUDED_VENDOR,"* ]]; then
    return 1
  fi
  if [[ "$RESOLVE_WITH_CONTEXT" -eq 1 ]]; then
    python3 "$OMNILANE_REPO/scripts/lib/quota_preflight.py" capability \
      --executor="$EXECUTOR" --vendor="$vendor" --model="$model" --effort="$effort" \
      --mode="$MODE" --workdir="$WORKDIR" --session="$SESSION_REQUEST" \
      --thread="$THREAD_NAME" --context="$NATIVE_CONTEXT" --background="$BACKGROUND" \
      --job-timeout="${OVERRIDE_JOB_TIMEOUT:-${!lane_job_var:-${OMNILANE_JOB_TIMEOUT:-}}}" \
      --idle-timeout="${OVERRIDE_IDLE_TIMEOUT:-${OMNILANE_IDLE_TIMEOUT:-}}" || rc=$?
    [[ "$rc" -ne 0 ]] || return 0
    [[ "$rc" -eq 10 ]] || return 1
    routing_candidate_available "$vendor" "$model" || return 1
  fi
  if [[ -n "$THREAD_NAME" ]]; then
    case "$vendor" in codex|claude|grok|gemini) ;; *) return 1 ;; esac
  fi
  if [[ "$SESSION_REQUEST" == "live" ]]; then
    live_vendor_capable "$vendor" || return 1
    [[ "$vendor" != "grok" || "$MODE" == "sysops" ]] || return 1
  fi
  return 0
}

quota_preflight() {
  [[ "${OMNILANE_QUOTA_ENABLED:-1}" != "0" ]] || return 0
  local original_spec="$RESOLVED_SPEC" original_idx="$RESOLVED_IDX"
  local original_aa="$AA_SELECTED_DECISION" original_last="$AA_LAST_DECISION"
  local original_vote="$AA_VOTE_PREFLIGHT"
  local original_fields=("${RESOLVED_FIELDS[@]}")
  local original_vendor="${RESOLVED_FIELDS[0]}"
  local original_model="${OVERRIDE_MODEL:-${RESOLVED_FIELDS[1]:-}}"
  local original_effort="${OVERRIDE_EFFORT:-${RESOLVED_FIELDS[2]:-}}"
  local near status snapshot action=unchanged rc=0 final_vendor final_model final_effort
  local quota_vendor="$original_vendor"
  if [[ "$original_vendor" == "vote" && ",$original_model," == *",codex,"* ]]; then
    quota_vendor=codex
  fi
  IFS=$'\t' read -r near status snapshot <<< "$(python3 "$OMNILANE_REPO/scripts/lib/quota_preflight.py" snapshot "$quota_vendor" "$OMNILANE_HOME")"
  if [[ "$AA_EXPLICIT_TARGET" -eq 1 ]]; then
    action=explicit
    QUOTA_NOTICE="omnilane: quota status=$status explicit target preserved"
    [[ "$near" != "1" ]] || QUOTA_NOTICE="$QUOTA_NOTICE quota_near_limit"
  elif [[ "$near" == "1" ]]; then
    QUOTA_EXCLUDED_VENDOR="$quota_vendor"
    resolve_chain "$CHAIN" || rc=$?
    QUOTA_EXCLUDED_VENDOR=""
    if [[ "$rc" -eq 0 ]]; then
      action=rerouted
    else
      RESOLVED_SPEC="$original_spec"; RESOLVED_IDX="$original_idx"
      RESOLVED_FIELDS=("${original_fields[@]}")
      AA_SELECTED_DECISION="$original_aa"; AA_LAST_DECISION="$original_last"
      AA_VOTE_PREFLIGHT="$original_vote"
      case "$LANE" in
        hardest-coding|hard-judgment) action=refused ;;
        *) action=quota_near_limit ;;
      esac
    fi
  fi
  final_vendor="${RESOLVED_FIELDS[0]}"
  final_model="${OVERRIDE_MODEL:-${RESOLVED_FIELDS[1]:-}}"
  final_effort="${OVERRIDE_EFFORT:-${RESOLVED_FIELDS[2]:-}}"
  if [[ "$action" == "refused" ]]; then
    final_vendor=""; final_model=""; final_effort=""
  fi
  QUOTA_DECISION="$(python3 "$OMNILANE_REPO/scripts/lib/quota_preflight.py" decision \
    --snapshot "$snapshot" --lane "$LANE" --action "$action" \
    --original "$original_vendor" "$original_model" "$original_effort" \
    --final "$final_vendor" "$final_model" "$final_effort")"
  if [[ "$action" == "refused" ]]; then
    printf '{"allowed":false,"code":"quota_near_limit_no_alternative","message":"Weekly quota is near its limit and no compliant alternative exists; operator decision required.","quota_preflight":%s}\n' "$QUOTA_DECISION" >&2
    return 7
  fi
  if [[ "$action" == "rerouted" || "$action" == "quota_near_limit" ]]; then
    QUOTA_NOTICE="omnilane: quota_near_limit action=$action original=$original_vendor final=$final_vendor"
  fi
  return 0
}
