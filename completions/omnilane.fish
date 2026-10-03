# Fish completion for omnilane. Static command/option completion only; it never
# invokes dispatch, provider CLIs, or the executable machine-local overlay.
# Dynamic lane-name and job/goal-id completion remain Bash/Zsh-only for now.

# Match the command hierarchy, so a goal's nested dispatch cannot inherit
# direct-dispatch-only flags. jobs accepts --json anywhere in its arguments.
function __omnilane_command_path
    set -l words (commandline -opc)
    set -e words[1]
    if test "$words[1]" = jobs
        set words (string match -v -- --json $words)
    end
    for component in $argv
        if not test "$words[1]" = "$component"
            return 1
        end
        set -e words[1]
    end
    return 0
end

# Top-level subcommands (offered only before a subcommand is chosen).
complete -c omnilane -f -n __fish_use_subcommand -a version       -d 'installed release version'
complete -c omnilane -f -n __fish_use_subcommand -a list          -d 'effective routing table'
complete -c omnilane -f -n __fish_use_subcommand -a route         -d 'dispatch or consult a model'
complete -c omnilane -f -n __fish_use_subcommand -a dispatch      -d 'dispatch or consult a model'
complete -c omnilane -f -n __fish_use_subcommand -a goal          -d 'budgeted goal ledger'
complete -c omnilane -f -n __fish_use_subcommand -a jobs          -d 'inspect background jobs'
complete -c omnilane -f -n __fish_use_subcommand -a mcp           -d 'MCP stdio server'
complete -c omnilane -f -n __fish_use_subcommand -a doctor        -d 'read-only health report'
complete -c omnilane -f -n __fish_use_subcommand -a which-bin -d 'resolved dispatch executable'
complete -c omnilane -f -n '__fish_seen_subcommand_from which-bin' -a 'codex claude grok gemini kimi qwen opencode'
complete -c omnilane -f -n __fish_use_subcommand -a whoami        -d 'caller-context file for the launching CLI'
complete -c omnilane -f -n __fish_use_subcommand -a resign        -d 're-probe and re-sign the transport overlay'
complete -c omnilane -f -n __fish_use_subcommand -a native-context -d 'capability file for this harness sub-agents'
complete -c omnilane -f -n __fish_use_subcommand -a benchmark     -d 'fixed quality/cost comparison'
complete -c omnilane -f -n __fish_use_subcommand -a release-audit -d 'offline release gate'
complete -c omnilane -f -n __fish_use_subcommand -a ui            -d 'Live Board server'
complete -c omnilane -f -n __fish_use_subcommand -a configure     -d 'lane routing (menu or set/get/unset/list/diff)'
complete -c omnilane -f -n __fish_use_subcommand -a completion    -d 'print a shell completion script'
complete -c omnilane -f -n __fish_use_subcommand -a help          -d 'usage'

# route / dispatch options.
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l mode        -x -a 'advise work'                                                                              -d 'read-only advise or write-enabled work'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l vendor      -x -a 'codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras exec vote' -d 'pin one vendor, no fallback'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l effort      -x -a 'low medium high xhigh max'                                                                -d 'reasoning effort'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l workdir     -r                                                                                              -d 'working directory'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l model       -x                                                                                              -d 'routed model override'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l timeout     -x                                                                                              -d 'per-call timeout in seconds'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l job-timeout -x                                                                                              -d 'whole-job timeout in seconds'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l background                                                                                                  -d 'run as a background job'
complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch' -l dry-run                                                                                                     -d 'resolve the plan and stop'

complete -c omnilane -n '__omnilane_command_path route; or __omnilane_command_path dispatch; or __omnilane_command_path goal dispatch' -l thread -x -d 'continue named Claude thread'

# goal subcommands and their own options. Dispatch shares supported options above.
complete -c omnilane -f \
  -n '__omnilane_command_path goal; and not __fish_seen_subcommand_from open dispatch note status close' \
  -a 'open dispatch note status close' -d 'goal subcommand'
complete -c omnilane -n '__omnilane_command_path goal open' -l budget-jobs -x -d 'maximum jobs'
complete -c omnilane -n '__omnilane_command_path goal open' -l budget-seconds -x -d 'maximum elapsed seconds'
complete -c omnilane -n '__omnilane_command_path goal open' -l workdir -r -d 'working directory'
complete -c omnilane -n '__omnilane_command_path goal close' -l summary -x -d 'final report summary'

# jobs subcommands (only before a jobs subcommand is chosen).
complete -c omnilane -f \
  -n '__fish_seen_subcommand_from jobs; and not __fish_seen_subcommand_from list status result complete-native tail send watch close retry stats recommend wait cancel rm threads audit prune help' \
  -a 'list status result complete-native tail send watch close retry stats recommend wait cancel rm threads audit prune help' -d 'jobs subcommand'
complete -c omnilane -n '__fish_seen_subcommand_from jobs; and not __omnilane_command_path jobs threads rm' -l json -d 'JSON output'
# Nested thread actions remain static; no provider/session state is loaded.
complete -c omnilane -f \
  -n '__omnilane_command_path jobs threads; and not __fish_seen_subcommand_from list show rm' \
  -a 'list show rm' -d 'thread action'
# jobs list / stats filters.
complete -c omnilane -n '__omnilane_command_path jobs list; or __omnilane_command_path jobs stats; or __omnilane_command_path jobs recommend' -l lane -x -d 'filter by lane'
complete -c omnilane -n '__omnilane_command_path jobs list; or __omnilane_command_path jobs stats' -l vendor -x -a 'codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras exec' -d 'filter by vendor'
complete -c omnilane -n '__omnilane_command_path jobs list'       -l status -x -a 'running done dead pending cancelled expired'                                             -d 'filter by status'
complete -c omnilane -n '__omnilane_command_path jobs stats; or __omnilane_command_path jobs recommend; or __omnilane_command_path jobs audit' -l last -x -d 'maximum recent jobs'
complete -c omnilane -n '__omnilane_command_path jobs recommend' -l min-samples -x -d 'minimum completed samples'

# configure subcommands (non-interactive).
complete -c omnilane -f -n '__fish_seen_subcommand_from configure; and not __fish_seen_subcommand_from set get unset list diff' -a 'set get unset list diff' -d 'configure action'

# doctor / release-audit / ui / completion.
complete -c omnilane -n '__fish_seen_subcommand_from doctor' -l json -d 'JSON output'
complete -c omnilane -n '__fish_seen_subcommand_from doctor' -l strict -d 'fail when any warning is reported'
complete -c omnilane -n '__fish_seen_subcommand_from doctor' -l probe -x -a 'codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras' -d 'run one opt-in live probe'
complete -c omnilane -n '__fish_seen_subcommand_from doctor' -l probe-timeout -x -d 'probe timeout in seconds'
complete -c omnilane -n '__fish_seen_subcommand_from benchmark' -l json -d 'JSON output'
complete -c omnilane -n '__fish_seen_subcommand_from benchmark' -l run -d 'invoke providers in advise mode'
complete -c omnilane -n '__fish_seen_subcommand_from benchmark' -l vendor -x -a 'codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras' -d 'vendor to compare; repeatable'
complete -c omnilane -n '__fish_seen_subcommand_from benchmark' -l timeout -x -d 'per-workload timeout'
complete -c omnilane -n '__fish_seen_subcommand_from benchmark' -l workloads -r -d 'TSV workload file'
complete -c omnilane -n '__fish_seen_subcommand_from benchmark' -l cost-per-call -x -d 'user-supplied VENDOR=USD estimate'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l target -x       -d 'target version'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l allow-dirty     -d 'permit a dirty tree'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l require-tag     -d 'require an annotated tag'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l json            -d 'JSON output'
complete -c omnilane -f -n '__fish_seen_subcommand_from ui'         -a 'start status url stop' -d 'Live Board action'
complete -c omnilane -f -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'         -d 'target shell'
