# Bash completion for omnilane. This file reads routing/job/goal names only; it never
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

_omnilane_goal_ids() {
  local home root dir id count=0
  home="${OMNILANE_HOME:-$HOME/.omnilane}"
  root="$home/goals"
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
  local cur prev command sub words sub_index reply_line thread_sub thread_index
  local goal_dispatch=0
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]:-}"
  command="${COMP_WORDS[1]:-}"
  if [[ "$command" == goal && "${COMP_WORDS[2]:-}" == dispatch && "$COMP_CWORD" -gt 3 ]]; then
    command=dispatch
    goal_dispatch=1
  fi
  if [[ "$COMP_CWORD" -eq 1 ]]; then
      words="version list route dispatch goal jobs mcp doctor whoami native-context resign benchmark release-audit ui configure completion help"
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
      --model|--timeout|--job-timeout|--thread) return ;;
          *)
            words="--background --thread --mode --workdir --vendor --model --effort --timeout --job-timeout $(_omnilane_lanes)"
            [[ "$goal_dispatch" -eq 1 ]] || words="--dry-run --help $words"
            ;;
        esac
        ;;
      goal)
        sub="${COMP_WORDS[2]:-}"
        if [[ "$COMP_CWORD" -eq 2 ]]; then
          words="open dispatch note status close"
        elif [[ "$COMP_CWORD" -eq 3 &&
                ( "$sub" == dispatch || "$sub" == note || "$sub" == status || "$sub" == close ) ]]; then
          words="$(_omnilane_goal_ids)"
        elif [[ "$sub" == open && "$COMP_CWORD" -gt 3 ]]; then
          case "$prev" in
            --budget-jobs|--budget-seconds) return ;;
            --workdir)
              while IFS= read -r reply_line; do
                COMPREPLY+=("$reply_line")
              done < <(compgen -d -- "$cur")
              return ;;
            *) words="--budget-jobs --budget-seconds --workdir" ;;
          esac
        elif [[ "$sub" == close && "$COMP_CWORD" -eq 4 ]]; then
          words="--summary"
        else
          return
        fi
        ;;
      jobs)
        sub="${COMP_WORDS[2]:-}"
        sub_index=2
        if [[ "$sub" == "--json" ]]; then
          sub_index=3
          sub="${COMP_WORDS[3]:-}"
        fi
        if [[ "$COMP_CWORD" -eq "$sub_index" ]]; then
          words="list status result complete-native tail send watch close retry stats recommend wait cancel rm threads audit prune help"
        elif [[ "$COMP_CWORD" -eq $((sub_index + 1)) &&
                ( "$sub" == status || "$sub" == result || "$sub" == wait ||
                  "$sub" == tail || "$sub" == retry || "$sub" == complete-native ||
                  "$sub" == send || "$sub" == watch || "$sub" == close ||
                  "$sub" == cancel || "$sub" == rm ) ]]; then
          words="$(_omnilane_job_ids)"
        elif [[ "$sub" == complete-native && "$COMP_CWORD" -eq $((sub_index + 2)) ]]; then
          while IFS= read -r reply_line; do
            COMPREPLY+=("$reply_line")
          done < <(compgen -f -- "$cur")
          return
        elif [[ "$sub" == threads ]]; then
          thread_index=$((sub_index + 1))
          thread_sub="${COMP_WORDS[thread_index]:-}"
          if [[ "$thread_sub" == --json ]]; then
            thread_index=$((thread_index + 1))
            thread_sub="${COMP_WORDS[thread_index]:-}"
          fi
          if [[ "$COMP_CWORD" -eq "$thread_index" ]]; then
            words="list show rm"
          elif [[ "$thread_sub" == list ||
                  ( "$thread_sub" == show && "$COMP_CWORD" -gt $((thread_index + 1)) ) ]]; then
            words="--json"
          else
            return
          fi
        elif [[ "$sub" == list ]]; then
          case "$prev" in
            --status) words="running done dead pending cancelled expired" ;;
            --lane) words="$(_omnilane_lanes)" ;;
            --vendor) words="codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras exec" ;;
            *) words="--lane --vendor --status --json" ;;
          esac
        elif [[ "$sub" == status || "$sub" == result ]]; then
          words="--json"
        elif [[ "$sub" == tail ]]; then
          words="--lines"
        elif [[ "$sub" == retry ]]; then
          words="--background"
        elif [[ "$sub" == wait ]]; then
          words="--timeout"
        elif [[ "$sub" == stats ]]; then
          words="--last --lane --vendor --json"
        elif [[ "$sub" == recommend ]]; then
          words="--last --lane --min-samples --json"
        elif [[ "$sub" == audit ]]; then
          words="--last --json"
        elif [[ "$sub" == prune ]]; then
          words="--keep --older-than --apply"
        else
          return
        fi
        ;;
      doctor) words="--json --strict --probe --probe-timeout" ;;
      benchmark)
        case "$prev" in
          --vendor) words="codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras" ;;
          --workloads)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
          --timeout|--cost-per-call) return ;;
          *) words="--json --run --vendor --timeout --workloads --cost-per-call" ;;
        esac
        ;;
      release-audit) words="--target --allow-dirty --require-tag --manifest --json" ;;
      ui) words="start status url stop" ;;
      completion) words="bash zsh fish" ;;
      *) return ;;
    esac
  fi
  COMPREPLY=()
  while IFS= read -r reply_line; do
    COMPREPLY+=("$reply_line")
  done < <(compgen -W "$words" -- "$cur")
}

complete -F _omnilane omnilane
