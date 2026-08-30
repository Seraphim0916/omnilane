#!/usr/bin/env bash

# Shared live-mailbox protocol differences. Callers provide json_escape().

live_capable_vendors() {
  printf 'claude, gemini'
}

live_vendor_capable() {
  case "$1" in
    claude|gemini) return 0 ;;
    *) return 1 ;;
  esac
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
    *) return 2 ;;
  esac
}

live_event_is_result() {
  local vendor="$1" event="$2" pattern
  case "$vendor" in
    claude) pattern='"type"[[:space:]]*:[[:space:]]*"result"' ;;
    gemini) pattern='"event"[[:space:]]*:[[:space:]]*"result"' ;;
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
