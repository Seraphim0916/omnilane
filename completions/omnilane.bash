# Bash completion for omnilane. This file reads routing/job names only; it never
# invokes dispatch, provider CLIs, or the executable machine-local overlay.

_omnilane_repo() {
  if [[ -n "${OMNILANE_COMPLETION_REPO:-}" && -r "$OMNILANE_COMPLETION_REPO/routing.yaml" ]]; then
    printf '%s\n' "$OMNILANE_COMPLETION_REPO"
    return 0
  fi
  local exe target
  exe="$(command -v omnilane 2>/dev/null)" || return 1
  while [[ -L "$exe" ]]; do
    target="$(readlink "$exe")" || return 1
    case "$target" in
      /*) exe="$target" ;;
      *) exe="$(dirname "$exe")/$target" ;;
    esac
  done
  (cd "$(dirname "$exe")/.." 2>/dev/null && pwd -P)
}

_omnilane_lanes() {
  local repo home file
  local files=()
  repo="$(_omnilane_repo)" || return 0
  home="${OMNILANE_HOME:-$HOME/.omnilane}"
  for file in "$home/routing.local.yaml" "$repo/routing.yaml"; do
    [[ -f "$file" && ! -L "$file" ]] && files+=("$file")
  done
  [[ ${#files[@]} -gt 0 ]] || return 0
  awk '
    /^[a-z][a-z0-9-]*:[[:space:]]/ {
      lane=$1; sub(/:$/, "", lane)
      if (!seen[lane]++) print lane
    }
  ' "${files[@]}"
}

_omnilane_job_ids() {
  local home root dir id count=0
  home="${OMNILANE_HOME:-$HOME/.omnilane}"
  root="$home/jobs"
  [[ -d "$root" && ! -L "$root" ]] || return 0
  for dir in "$root"/*; do
    [[ -d "$dir" && ! -L "$dir" ]] || continue
    id="${dir##*/}"
    if [[ "$id" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$ ]]; then
      printf '%s\n' "$id"
      ((count += 1))
      [[ "$count" -lt 1000 ]] || break
    fi
  done
}

_omnilane() {
  local cur prev command sub words sub_index reply_line
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]:-}"
  command="${COMP_WORDS[1]:-}"
  if [[ "$COMP_CWORD" -eq 1 ]]; then
    words="version list route dispatch jobs doctor release-audit ui configure completion help"
  else
    case "$command" in
      route|dispatch)
        case "$prev" in
          --mode) words="advise work" ;;
          --vendor) words="codex claude grok gemini kimi qwen opencode openrouter exec vote" ;;
          --effort) words="low medium high xhigh max" ;;
          --workdir)
            COMPREPLY=()
            while IFS= read -r reply_line; do
              COMPREPLY+=("$reply_line")
            done < <(compgen -d -- "$cur")
            return ;;
          --model|--timeout|--job-timeout) return ;;
          *) words="--background --dry-run --help --mode --workdir --vendor --model --effort --timeout --job-timeout $(_omnilane_lanes)" ;;
        esac
        ;;
      jobs)
        sub="${COMP_WORDS[2]:-}"
        sub_index=2
        if [[ "$sub" == "--json" ]]; then
          sub_index=3
          sub="${COMP_WORDS[3]:-}"
        fi
        if [[ "$COMP_CWORD" -eq "$sub_index" ]]; then
          words="list status result tail retry stats wait audit prune help"
        elif [[ "$COMP_CWORD" -eq $((sub_index + 1)) &&
                ( "$sub" == status || "$sub" == result || "$sub" == wait ||
                  "$sub" == tail || "$sub" == retry ) ]]; then
          words="$(_omnilane_job_ids)"
        elif [[ "$sub" == list ]]; then
          words="--json"
        elif [[ "$sub" == status || "$sub" == result ]]; then
          words="--json"
        elif [[ "$sub" == tail ]]; then
          words="--lines"
        elif [[ "$sub" == retry ]]; then
          words="--background"
        elif [[ "$sub" == wait ]]; then
          words="--timeout"
        elif [[ "$sub" == stats || "$sub" == audit ]]; then
          words="--last --json"
        elif [[ "$sub" == prune ]]; then
          words="--keep --older-than --apply"
        else
          return
        fi
        ;;
      doctor) words="--json" ;;
      release-audit) words="--target --allow-dirty --require-tag --manifest --json" ;;
      ui) words="start status url stop" ;;
      completion) words="bash zsh" ;;
      *) return ;;
    esac
  fi
  COMPREPLY=()
  while IFS= read -r reply_line; do
    COMPREPLY+=("$reply_line")
  done < <(compgen -W "$words" -- "$cur")
}

complete -F _omnilane omnilane
