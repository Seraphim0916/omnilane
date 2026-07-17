# Round 1 experiment: safe shell completion

Branch: `codex/idea-shell-completion`

Commit: `36a0db5`

Status: PARTIAL. Functional, adversarial, regression, and runtime acceptance
pass. Local ShellCheck is unverified because `shellcheck` is not installed on
the current MacStudio.

## Hypothesis

The public wrapper can emit Bash and Zsh completion for commands, options,
routing lanes, and local job IDs without invoking a provider or loading
executable machine-local routing overrides.

## Red/green evidence

- Red: the focused shell oracle reported `0 passed, 1 failed`; `omnilane
  completion` was an unknown command and its output was not valid completion
  source.
- Green: the complete shell suite passed `51 passed, 0 failed`, including the
  new completion oracle and the existing timeout, process-group, installer,
  routing, and hostile-text cases.
- The Python suite initially had one local UI server-start failure in
  `test_unresponsive_recorded_server_blocks_replacement`. That exact case then
  passed four consecutive runs, followed by the complete suite passing with
  `36 passed, 11 subtests passed`. No UI code changed on this branch.
- Bash syntax, Zsh syntax, Perl syntax, Python compilation, and
  `git diff --check` passed.

## Runtime evidence

- `bin/omnilane completion bash` and `bin/omnilane completion zsh` were
  byte-compared with the checked-in completion definitions.
- An isolated PATH containing only an install-style `omnilane` symlink loaded
  Bash completion with `eval "$(omnilane completion bash)"` and Zsh completion
  with `source <(omnilane completion zsh)`.
- Representative command, option, lane, and job-ID completion was exercised in
  both shells without creating job state or calling a provider.

## Adversarial evidence

- Unknown shells and extra arguments fail closed with usage exit 2.
- Missing job stores return no candidates. Job-store and job-directory symlinks,
  malformed job names, malformed lane names, and shell-shaped routing text are
  ignored.
- A hostile `local.sh` marker proved completion never loads the executable
  machine-local overlay.
- Lane parsing accepts only `[a-z][a-z0-9-]*`, preserves local-first routing
  precedence, and deduplicates names without exposing models or task bodies.
- A 1001-job fixture returned exactly 1000 legal IDs, proving the interactive
  scan has a hard resource bound.

## Review

Completion reads only the optional non-symlink `routing.local.yaml`, the
repository `routing.yaml`, and non-symlink job-directory basenames. It never
calls `dispatch.sh --list`, because that runtime path deliberately loads
`local.sh`. The public wrapper only prints static scripts and retains the normal
usage exit contract.

## Known limits

- Completion is session-local; users must add the documented Bash or Zsh line
  to their own shell startup file if they want persistence.
- Job candidates are capped at 1000 and follow filesystem iteration order.
- Custom vendor values and arbitrary model names are not enumerated; their
  arguments remain free text.
- ShellCheck must run before this branch can receive an overall PASS.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates `36a0db5`, revert that commit to remove the
feature. `main` remains unchanged pending Vincent's final judgment.
