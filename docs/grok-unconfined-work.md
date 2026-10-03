# Grok work on macOS: operator opt-in without isolation

By default, explicit macOS `--vendor grok --mode work` is refused. Without an
explicit vendor, work-mode routing skips Grok and tries the next available
candidate, including in `--dry-run`. Linux behavior is unchanged.

## Enable and disable

The operator may add exactly this line to **`$OMNILANE_HOME/routing.local.yaml`**
(normally `~/.omnilane/routing.local.yaml`):

```yaml
option.grok-macos-work: unconfined
```

Remove that line to disable it. Absence means off. Other values and duplicate
option rows fail lint and remain off. This option is **local-only**: the same
line in shipped `routing.yaml` fails lint and cannot enable dispatch. Keep the
line separate from lanes; `configure set/get/unset` continues to manage lanes,
and `configure set` preserves the option. `configure list` shows local content.

Check `omnilane list`, `omnilane doctor`, and `omnilane route --validate`.
Doctor reports PASS when off and WARN when on; **`doctor --strict` fails when
the option is on**. This warning also applies when inspecting configuration on
Linux, although Linux work execution retains its existing isolation flags.

## What is lost

This is single-shot work only, not a sandbox improvement. Grok receives
`--always-approve --sandbox off`, the same flag set as sysops, while the job's
recorded mode remains `work`. There is no filesystem or agent-tool network
isolation: commands may write outside the workdir and contact the network.
Approval prompts are bypassed. A workdir and a task brief are instructions,
**not security boundaries**. Use only for tasks whose exposure the operator
accepts. This does not enable restricted live/ACP work; `--live` is refused with
a specific single-shot-only explanation. Other unsupported combinations keep
their own diagnostic.

Dispatch derives the runner decision from the local routing file and overwrites
the inherited runner flag; setting an environment variable alone is not an
opt-in. Direct invocation of internal runners is not a public policy boundary.

Dispatch stderr, dry-run output, job inspection and completion notices disclose:

> omnilane: Grok work on macOS runs without isolation (operator opt-in): commands can reach the network and write outside the workdir

Foreground dispatch repeats this notice on stderr immediately before worker
output. Metadata includes `"isolation":"none"`; `jobs status`, `jobs result`
and the completion notice print the warning before the result. Existing JSON
inspection envelopes remain machine-readable. `jobs audit` accepts the field.

## Required task-brief template

When this host has the option enabled, the commander must use this template for
**every brief sent to Grok**, keeping the following rules in this order. Replace
the directory placeholders with exact authorized absolute paths; never leave
the scope open-ended.

```text
RULES FIRST
1. Never scan env files, credentials, login files, key stores or keychains.
2. Change files ONLY inside these directories: <AUTHORIZED_DIRECTORY_1>,
   <AUTHORIZED_DIRECTORY_2>. Do not change anything elsewhere.
3. Do not post to external services. Do not install software or dependencies.
4. Acceptance: run git status and inspect the diff. Report every change outside
   the named directories as a failure; do not claim completion until the scope
   check and the task's requested tests pass.

TASK: <CONCRETE_CHANGE>
ACCEPTANCE: <TARGETED_TESTS_AND_EXPECTED_RESULTS>
REPORT: changed files, literal verification results, scope violations, blockers.
```

These rules reduce accidental scope drift; they cannot enforce isolation.
