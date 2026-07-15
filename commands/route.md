---
description: Route a natural-language task or explicit model consultation through omnilane
---

Dispatch work through the omnilane routing table.

Input: `$ARGUMENTS` — a `<lane> <task text>`, a free-form task description, an
explicit vendor/model request, or a capability-only question. Apply the
`omnilane` skill's natural-language consultation rules before running anything.

Steps:
1. Resolve the plugin root (the directory containing `routing.yaml`).
2. Decide the action:
   - Capability-only question: classify the need, answer with the first available
     model shown for that lane by `dispatch.sh --list`, and do not dispatch.
   - Explicit vendor/model: use `consult` and keep its `--vendor V`; canonical
     aliases also pass the skill table's exact `--model` and `--effort`.
   - Explicit lane or unnamed task: use that lane or classify it from the skill.
   - Unknown or ambiguous nickname: ask for clarification; do not guess.
3. When dispatching, run:
   `<plugin-root>/scripts/dispatch.sh [--vendor V] [--mode work --workdir <dir>] <lane> "<task>"`
   - Default is `advise` (read-only worker). Use `--mode work` only when the
     worker must edit files, and pass an explicit `--workdir`.
   - An explicit target must never silently fall back. Do not remove `--vendor`
     after a target error.
   - Long task? Add `--background`, report the job id, and poll later with
     `scripts/jobs.sh status|result <id>`.
   - Deep task whose CLI call may outrun the 600s per-call watchdog? Raise its
     cap with `--timeout <seconds>` (e.g. `--timeout 1200`). It bounds each CLI
     call, not the whole dispatch.
   - Need one aggregate fuse across lock wait, retries, voters, and rounds? Add
     `--job-timeout <seconds>`. Deep full-repository audits typically need
     7200–14400 seconds; it is disabled by default and expiry returns 124.
4. Relay the worker's output. Judge it against the acceptance criteria you put
   in the task — do not accept "done" without evidence.
