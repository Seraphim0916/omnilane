<!-- omnilane-routing:start -->
## omnilane — model routing (persistent reminder)

Implementation work — code edits, new files, tests, builds, deploys — is
dispatched by default, even when the lane's first available model is the one
you are running as. Consult the routing table with `omnilane list` (or
`scripts/dispatch.sh --list` inside the omnilane repo), classify the subtask
into a lane, then dispatch it headlessly:

    omnilane route [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"

Advise mode is the default; pass `--mode work` only with an explicit
`--workdir`. The commander self-executes only reserved items: planning and
decomposition, writing task briefs, reviewing reports, acceptance checks,
replies to the operator, git commit/push, read-only verification, and fixes
of one line or less. "This lane is mine, so I'll do it myself" is not a
valid reason to skip dispatch.

If the user explicitly names Claude, Codex, Grok, Gemini, or a canonical model
alias, use the omnilane skill's consult rules and keep `--vendor` in the
dispatch; an explicit target must not silently fall back.

Lane definitions, modes, per-model rows, and safety rules live in the
`omnilane` skill — load it and apply the row for the model you are running
as; legacy model-routing skill variants are retired. Workers must never
dispatch again (nested dispatch is refused, exit 86).
<!-- omnilane-routing:end -->
