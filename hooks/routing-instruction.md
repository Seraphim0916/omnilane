<!-- omnilane-routing:start -->
## omnilane — model routing (persistent reminder)

Every task — not only implementation — is dispatched by default, even when
the lane's first available model is the one you are running as: code edits,
search, investigation, file reads, verification, tests, builds, deploys.
Consult the routing table with `omnilane list` (or `scripts/dispatch.sh --list`
inside the omnilane repo), classify the subtask into a lane, then dispatch it
headlessly:

    omnilane route [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"

Advise mode is the default; pass `--mode work` only with an explicit
`--workdir`. The commander self-executes only the reserved list: planning and
decomposition, writing task briefs, reading worker output (`out.txt`,
`events.jsonl`, inbox records), acceptance judgment, replies to the operator,
git commit/push, and governance-file edits. The commander never runs commands
itself: re-verify a worker's claim by reading its attached evidence or by
dispatching a second worker with a different `--vendor`. Read-only work goes
out in advise mode through named lanes — `triage` for high-volume scans,
`long-context` for large documents, `live-search` for web or X,
`hard-judgment` for second opinions. Invalid reasons to skip dispatch: "this
lane is mine", "I am not dispatching so the rule does not apply", "it is only
a file read", "dispatch is slower", "it is one line".

Implementation dispatches carry `--mode work --workdir DIR --timeout 3600` or
more; the advise default is read-only under a 600 s watchdog and yields no
output there. Add `--background` for long tasks: on Claude Code the completion
inbox delivers the result into your next prompt, elsewhere use `jobs.sh wait
<id>`. Follow up a live Claude or Gemini worker with `jobs.sh send <id> "<text>"`
/ `jobs.sh close <id>`. Wrap multi-dispatch exploratory objectives in
`omnilane goal open`; dispatch a single obvious task directly.

If the user explicitly names Claude, Codex, Grok, Gemini, or a canonical model
alias, use the omnilane skill's consult rules and keep `--vendor` in the
dispatch; an explicit target must not silently fall back.

Lane definitions, modes, per-model rows, and safety rules live in the
`omnilane` skill — load it and apply the row for the model you are running
as; legacy model-routing skill variants are retired. Workers must never
dispatch again (nested dispatch is refused, exit 86).
<!-- omnilane-routing:end -->
