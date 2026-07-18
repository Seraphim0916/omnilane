# Fish completion for omnilane. Static command/option completion only; it never
# invokes dispatch, provider CLIs, or the executable machine-local overlay.
# Dynamic lane-name and job-id completion remain Bash/Zsh-only for now.

# Top-level subcommands (offered only before a subcommand is chosen).
complete -c omnilane -f -n __fish_use_subcommand -a version       -d 'installed release version'
complete -c omnilane -f -n __fish_use_subcommand -a list          -d 'effective routing table'
complete -c omnilane -f -n __fish_use_subcommand -a route         -d 'dispatch or consult a model'
complete -c omnilane -f -n __fish_use_subcommand -a dispatch      -d 'dispatch or consult a model'
complete -c omnilane -f -n __fish_use_subcommand -a jobs          -d 'inspect background jobs'
complete -c omnilane -f -n __fish_use_subcommand -a mcp           -d 'MCP stdio server'
complete -c omnilane -f -n __fish_use_subcommand -a doctor        -d 'read-only health report'
complete -c omnilane -f -n __fish_use_subcommand -a release-audit -d 'offline release gate'
complete -c omnilane -f -n __fish_use_subcommand -a ui            -d 'Live Board server'
complete -c omnilane -f -n __fish_use_subcommand -a configure     -d 'lane routing (menu or set/get/unset/list/diff)'
complete -c omnilane -f -n __fish_use_subcommand -a completion    -d 'print a shell completion script'
complete -c omnilane -f -n __fish_use_subcommand -a help          -d 'usage'

# route / dispatch options.
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l mode        -x -a 'advise work'                                                                              -d 'read-only advise or write-enabled work'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l vendor      -x -a 'codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras exec vote' -d 'pin one vendor, no fallback'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l effort      -x -a 'low medium high xhigh max'                                                                -d 'reasoning effort'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l workdir     -r                                                                                              -d 'working directory'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l model       -x                                                                                              -d 'routed model override'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l timeout     -x                                                                                              -d 'per-call timeout in seconds'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l job-timeout -x                                                                                              -d 'whole-job timeout in seconds'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l background                                                                                                  -d 'run as a background job'
complete -c omnilane -n '__fish_seen_subcommand_from route dispatch' -l dry-run                                                                                                     -d 'resolve the plan and stop'

# jobs subcommands (only before a jobs subcommand is chosen).
complete -c omnilane -f \
  -n '__fish_seen_subcommand_from jobs; and not __fish_seen_subcommand_from list status result tail retry stats wait audit prune cancel rm help' \
  -a 'list status result tail retry stats wait audit prune cancel rm help' -d 'jobs subcommand'
complete -c omnilane -n '__fish_seen_subcommand_from jobs' -l json -d 'JSON output'
# jobs list / stats filters.
complete -c omnilane -n '__fish_seen_subcommand_from jobs; and __fish_seen_subcommand_from list stats' -l lane   -x                                                                                    -d 'filter by lane'
complete -c omnilane -n '__fish_seen_subcommand_from jobs; and __fish_seen_subcommand_from list stats' -l vendor -x -a 'codex claude grok gemini kimi qwen opencode openrouter deepseek zai mistral groq cerebras exec' -d 'filter by vendor'
complete -c omnilane -n '__fish_seen_subcommand_from jobs; and __fish_seen_subcommand_from list'       -l status -x -a 'running done'                                                                    -d 'filter by status'

# configure subcommands (non-interactive).
complete -c omnilane -f -n '__fish_seen_subcommand_from configure; and not __fish_seen_subcommand_from set get unset list diff' -a 'set get unset list diff' -d 'configure action'

# doctor / release-audit / ui / completion.
complete -c omnilane -n '__fish_seen_subcommand_from doctor' -l json -d 'JSON output'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l target -x       -d 'target version'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l allow-dirty     -d 'permit a dirty tree'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l require-tag     -d 'require an annotated tag'
complete -c omnilane -n '__fish_seen_subcommand_from release-audit' -l json            -d 'JSON output'
complete -c omnilane -f -n '__fish_seen_subcommand_from ui'         -a 'start status url stop' -d 'Live Board action'
complete -c omnilane -f -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'         -d 'target shell'
