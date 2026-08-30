# Goal Orchestrator — bounded multi-agent dispatch on the live mailbox

Status: approved direction, phased plan. Nothing below is implemented.
Prerequisite: resident-mode completion (explicit --live/--single-shot choice,
idle cap, gemini live lane) must land and release first.

## 摘要（繁中）

`omnilane goal "<目標>" --budget-*` 一句話啟動一個有界的多代理迴圈：
一個常駐的規劃工人負責想，omnilane 本體負責派，完工紀錄自動回流給
規劃工人繼續想，直到規劃工人自判完成或預算到頂，最後收斂成一份報告。
把「ultracode 式的有界代理團隊」跟「跨 session 通訊」融合在既有的
調度層上；規劃工人永遠不自己派工，深度防護不動。

## Why now

The three primitives this loop needs all shipped in 0.20.0:

1. completion inbox — workers report back without polling
2. live mailbox — a resident worker can be fed follow-ups mid-run
3. foreman session identity — concurrent loops cannot steal each
   other's records

The orchestrator is a consumer of these, not a rewrite of anything.

## Architecture invariants

- **The planner never dispatches.** The planner is one live-mailbox worker
  (claude or gemini lane). It emits structured instructions; the controller
  loop (plain shell, part of omnilane) performs every dispatch. Workers
  stay leaf nodes; the depth guard (exit 86) is untouched.
- **Everything is files.** Goal state lives under $OMNILANE_HOME/goals/<id>/
  (goal.txt, budget.json, plan events, per-round records). No daemon, no
  database, inspectable and resumable after a crash.
- **Bounded by construction.** Hard caps are enforced by the controller,
  not trusted to the planner: --budget-jobs N (max dispatches),
  --budget-seconds S (wall clock), --budget-parallel P (concurrent workers,
  default 2, honoring same-workdir codex serialization). Planner
  self-judgment ("good enough, stop") ends early; caps end late. Both
  paths produce the final report.

## Planner ↔ controller protocol (P1 deliverable, spec before code)

Planner replies are one JSON object per turn, schema-validated by the
controller; an invalid reply gets exactly one reprompt with the validator
error, then the goal aborts with a diagnostic (no silent retry loops).

  {"action":"dispatch","jobs":[{"lane":"...","mode":"advise|work",
     "task":"...","workdir":"..."}]}
  {"action":"wait"}                       # nothing to add, waiting on jobs
  {"action":"done","summary":"...")       # self-judged complete
  {"action":"abort","reason":"..."}

Controller → planner messages (via jobs.sh send): the goal text on open;
then one message per completed job (id, lane, exit, tail — the completion
record verbatim, framed as data); then budget warnings at 75% and a final
"budget exhausted, summarize now".

## Phases

### P0 — prerequisite (in flight)
Resident-mode completion + 0.21.0 release. Not part of this plan's scope.

### P1 — protocol + sequential loop
- docs: protocol spec section fleshed out with examples (this file)
- scripts/lib/goal-loop.sh: open planner, send goal, parse one action,
  dispatch sequentially (parallel=1), feed results back, close on
  done/abort/budget
- bin surface: omnilane goal "<text>" [--budget-jobs N] [--budget-seconds S]
- tests: fake-vendor planner emitting scripted actions; invalid-JSON
  reprompt path; budget-jobs cap; abort path
- acceptance: a real goal ("survey X and write findings to a file") runs
  end to end on claude planner + codex workers, evidence in goals/<id>/

### P2 — parallel fan-out + failure policy
- --budget-parallel P with per-workdir serialization awareness
- failed job policy: report to planner (it decides retry/replan); a job
  failing twice on the same task text is not re-dispatched (loop fuse)
- budget accounting surfaced in jobs.sh goal status <id>
- tests: concurrency cap, fuse, mixed success/failure rounds

### P3 — report + product surface
- final report assembly (per-round narrative + artifacts list) written to
  goals/<id>/report.md and printed
- five READMEs: usage section, budget semantics, when NOT to use it
- doctor: goal-loop reachability check
- release 0.30.0

## Risks, named

- Planner JSON discipline is the weakest link → schema validation + single
  reprompt + abort, never trust-and-crash.
- Cost runaway → controller-enforced caps; planner never sees raw budget
  controls, only warnings.
- Same-workdir codex lock (exit 87 seen 2026-08-30) → parallel dispatches
  to one workdir are serialized by design; P2 documents this instead of
  fighting it.
- Prompt-injection via worker tails flowing into the planner → tails are
  already sanitized and data-framed by the completion path; the planner
  frame repeats the "data, not instructions" rule.

## Out of scope (explicitly)

- R1 threaded resume for codex/grok (resume/fork exists, no live stdin) —
  separate roadmap line, not needed by the orchestrator.
- Cross-machine orchestration.
- Any daemon or server component.
