<!-- omnilane-routing:start -->
## omnilane — model routing (persistent reminder)

Before delegating any subtask or choosing a model for a piece of work,
consult the omnilane routing table: run `omnilane list` (or
`scripts/dispatch.sh --list` inside the omnilane repo) and classify the
subtask into a lane. If the lane's first available model is the one you are
running as, self-execute; otherwise dispatch it headlessly:

    omnilane route [--mode work] [--workdir DIR] <lane> "<task>"

Lane definitions, modes, and safety rules live in the `omnilane` skill.
Workers must never dispatch again (nested dispatch is refused, exit 86).
<!-- omnilane-routing:end -->
