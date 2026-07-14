---
description: Dispatch a task to the best vendor per the omnilane routing table (lane-based cross-CLI dispatch)
---

Dispatch work through the omnilane routing table.

Input: `$ARGUMENTS` — either `<lane> <task text>` or just a task description
(then classify it into a lane yourself using the `omnilane` skill's lane table;
run `scripts/dispatch.sh --list` from the plugin root to see effective lanes).

Steps:
1. Resolve the plugin root (the directory containing `routing.yaml`).
2. Run: `<plugin-root>/scripts/dispatch.sh [--mode work --workdir <dir>] <lane> "<task>"`
   - Default is `advise` (read-only worker). Use `--mode work` only when the
     worker must edit files, and pass an explicit `--workdir`.
   - Long task? Add `--background`, report the job id, and poll later with
     `scripts/jobs.sh status|result <id>`.
   - Deep task whose CLI call may outrun the 600s per-call watchdog? Raise its
     cap with `--timeout <seconds>` (e.g. `--timeout 1200`). It bounds each CLI
     call, not the whole dispatch.
3. Relay the worker's output. Judge it against the acceptance criteria you put
   in the task — do not accept "done" without evidence.
