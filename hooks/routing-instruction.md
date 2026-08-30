<!-- omnilane-routing:start -->
## omnilane — model routing (persistent reminder)

Implementation work — code edits, new files, tests, builds, deploys — is
dispatched by default, even when the lane's first available model is the one
you are running as. Consult the routing table with `omnilane list` (or
`scripts/dispatch.sh --list` inside the omnilane repo), classify the subtask
into a lane, then dispatch it headlessly:

    omnilane route [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"

The commander self-executes only reserved items: planning and decomposition,
writing task briefs, reviewing reports, acceptance checks, replies to the
operator, git commit/push, read-only verification, and fixes of one line or
less. "This lane is mine, so I'll do it myself" is not a valid reason to
skip dispatch.

If the user explicitly names Claude, Codex, Grok, Gemini, or a canonical model
alias, use the omnilane skill's consult rules and keep `--vendor` in the
dispatch; an explicit target must not silently fall back.

Lane definitions, modes, and safety rules live in the `omnilane` skill.
Workers must never dispatch again (nested dispatch is refused, exit 86).
<!-- omnilane-routing:end -->
