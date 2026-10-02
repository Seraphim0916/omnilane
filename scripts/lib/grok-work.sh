#!/usr/bin/env bash
# Operator-only routing option: never consult shipped routing or inherited flags.
grok_macos_work_enabled() {
  local file="${1:-$OMNILANE_HOME/routing.local.yaml}"
  [[ -f "$file" ]] || return 1
  awk '
    /^option[.]grok-macos-work:/ {
      count++; sub(/#.*/, ""); sub(/^[^:]*:[[:space:]]*/, "");
      sub(/[[:space:]]*$/, ""); if ($0 == "unconfined") valid++
    }
    END { exit !(count == 1 && valid == 1) }
  ' "$file"
}

grok_work_notice() {
  printf '%s\n' 'omnilane: Grok work on macOS runs without isolation (operator opt-in): commands can reach the network and write outside the workdir'
}

grok_work_option_status() {
  if grok_macos_work_enabled; then
    printf '%s\n' 'option.grok-macos-work: on; Grok work on macOS has no isolation (enabled by the operator)'
  else
    printf '%s\n' 'option.grok-macos-work: off'
  fi
}
