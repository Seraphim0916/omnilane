---
name: omnilane
description: 'Universal model-routing table + cross-vendor dispatch for ANY harness (Claude Code, Codex, Grok Build, Antigravity). Use when delegating subtasks, choosing a model for work, planning multi-part tasks, or when asked about model routing, delegate, dispatch, which model, tier selection, escalate, 派工, 模型路由. One routing table; every task dispatches by default via dispatch.sh — the main loop self-executes only reserved commander items.'
---

# omnilane — one routing table, every harness

You (the main loop) may be Claude, GPT, Grok, or Gemini. The procedure is identical:

1. **Identify the main model from current runtime metadata.** If the identity is unavailable, report it as unverified instead of guessing from a skill name or prior session.
2. **Split the work into subtasks and classify each into a lane** (table below).
3. **Dispatch every task by default — even when the lane's model is you:**
   implementation, search, investigation, file reads, verification, tests,
   builds, deploys. The commander self-executes only: planning and
   decomposition, writing task briefs, reading worker reports and job files
   (`out.txt`, `events.jsonl`, inbox records), acceptance judgment, replies to
   the operator, git commit/push, and edits to governance files. Read-only
   work goes out in advise mode: `triage` for high-volume scans, `long-context`
   for large documents, `live-search` for web or X, `hard-judgment` for second
   opinions. Editing work uses `--mode work --workdir <repo> --timeout 3600`
   or more. Re-verify a worker's claim by reading its attached evidence or by
   dispatching a second worker (change `--vendor`); the commander runs no
   commands itself. Invalid reasons to skip dispatch: "this lane is mine",
   "I am not dispatching so the rule does not apply", "it is only a file
   read", "dispatch is slower", "it is one line". Dispatch:
   `<repo>/scripts/dispatch.sh [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"`
   Add `--background` for long tasks; poll with `scripts/jobs.sh status|result <id>`.
   Use `--thread NAME` when later claude, codex, grok or gemini dispatches
   must retain earlier context. Threads in 0.33.0 pin vendor, model, effort and
   physical workdir; inspect or remove local state with `scripts/jobs.sh threads`,
   `threads show NAME`, and `threads rm NAME` (removal leaves the vendor session).
   Implementation dispatches (code edits, new files, tests, builds, deploys)
   must carry `--mode work --workdir <repo>` and a `--timeout` of at least
   3600 seconds. The advise default is a read-only worker under a 600 s
   per-call watchdog, and on an implementation task it yields zero output.
   Advise stays the default for reviews, questions, and second opinions.
   Before changing lane order from anecdotal outcomes, run
   `scripts/jobs.sh recommend [--last N] [--lane L] [--min-samples N]` and report
   its evidence threshold. The command is read-only and never changes routing.
   Preview old completed-job cleanup with `scripts/jobs.sh prune --keep <N>`;
   deletion requires the explicit `--apply` flag and never targets running jobs.
   A deep task whose CLI call may outrun the 600s per-call watchdog can raise its
   cap with `--timeout <seconds>` (e.g. `--timeout 1200` for hard-judgment /
   long-context). It bounds each CLI call, not the whole dispatch.
   For one aggregate fuse across lock wait, retries, voters, and rounds, add
   `--job-timeout <seconds>`. It is disabled by default; deep full-repository
   audits typically need 7200–14400 seconds, and expiry returns 124. The one
   automatic exception is non-Git Codex `work`: without an explicit, lane, or
   global job timeout, its resolved per-call timeout becomes the whole-job fuse,
   capped at the supervisor's 999999999-second maximum. If the bundled Perl
   supervisor is unavailable, it warns and continues through the existing
   per-call watchdog path.

Run `scripts/dispatch.sh --list` to see the effective table (local overrides win).
When routing is unexpectedly unavailable, run `bin/omnilane doctor` before
changing configuration; it reports state and dependencies without repairing them.
Doctor remains offline unless the operator explicitly adds `--probe V`; that
bounded probe returns metadata only. Use `bin/omnilane benchmark` for a fixed
no-call route plan, and add `--run` only when actual advise-mode comparison calls
were explicitly requested. Neither command changes routing.
Lanes are fallback chains — dispatch uses the first vendor CLI actually installed,
so the same table works with any subset of subscriptions.

## Lanes (defaults; see routing.yaml for the live values)

Each lane's **backup** is the next candidate in its `routing.yaml` chain —
what dispatch picks when the first-choice vendor CLI is not installed.

| Lane | First choice | Backup | When |
|---|---|---|---|
| hardest-coding | Claude Fable 5.1 (max) | GPT-6 Astra (xhigh) → Grok 4.6 → Gemini 3.8 Flash (High) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| bulk-mechanical | GPT-5.6 Sol (high) | Gemini 3.8 Flash (High) → Claude Sonnet 5 (high) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| triage | GPT-5.6 Luna (high) | Gemini 3.8 Flash (Low) → Claude Haiku 4.5 | High-volume scans, first-pass filtering |
| hard-judgment | Claude Fable 5.1 (xhigh) | GPT-6 Astra (xhigh) → Grok 4.6 | Architecture arbitration, deep reasoning, second opinions |
| taste-final | Claude Fable 5.1 (xhigh) | GPT-6 Astra (xhigh) → Grok 4.6 → Gemini 3.8 Flash (High) | User-facing prose, prompt/doc polish, Chinese phrasing, style arbitration |
| consult | GPT-6 Astra (xhigh) | Claude Fable 5.1 (xhigh) → Grok 4.6 → Gemini 3.8 Flash (Medium) | Direct named-model consultation; always keep `--vendor` |
| ui-draft | GPT-5.6 Sol (high) | Claude Fable 5.1 (xhigh) → Gemini 3.8 Flash (High) | UI drafts only WITH design system / reference images; open-ended taste goes taste-final |
| long-context | Gemini 3.8 Flash (Medium) | GPT-5.6 Terra (max) → Claude Opus 5 (medium) | Long-context synthesis; context size alone is not a quality result |
| fast-agentic | Gemini 3.8 Flash (Low) | GPT-5.6 Luna (high) → Claude Haiku 4.5 | Fast multi-step agentic loops and multimodal checks |
| live-search | Grok 4.6 | Gemini 3.8 Flash (High) → Claude Sonnet 5 (high) → off | Realtime X/web search; non-Grok fallbacks provide generic web search, not equivalent X context |
| coding-overflow | Grok 4.6 | Gemini 3.8 Flash (High) → Kimi K3 → Qwen3 Coder Plus → OpenCode → off | Explicit Codex-quota relief; no automatic cross-vendor retry after provider failure |
| arbitrate | off (opt-in vote panel) | — | Disabled by default. Enable with `arbitrate: vote codex,claude,grok -` in routing.local.yaml or via the configurator (any 1-4 voters). One quota hit PER VOTER PER ROUND; you chair: read the opinions and own the decision. Effort field 2 = debate round (voters rebut each other) |

Claude Fable 5.1 leads the current hardest-coding, hard-judgment, and
 taste-final defaults at task-specific max/xhigh efforts. GPT-6 Astra is the
 Codex-family fallback and independent-review path. Fable max is the quality-first prompt-level controller. Opus high/xhigh is
 the balanced controller/independent-review option; Astra is the existing-
 Codex-quota backup/reviewer. These are role recommendations, not a lane or
 automatic selector. Opus remains explicitly selectable and in long-context
 fallback.

Astra defaults to `xhigh` on the high-difficulty lanes. For an explicitly needed
upgrade, use `--vendor codex --effort max`; no automatic risk classification or
failure-triggered effort escalation is added. AA API task costs do not prove
subscription-quota savings.

## Natural-language consultation

Users may speak normally; they do not need lane names.

1. Capability-only question (`which model`, `what can Claude do`, `哪個模型`,
   `誰適合`) → classify the need, then answer with the first available model
   shown for that lane by `dispatch.sh --list`; do not dispatch unless execution
   is also requested.
2. Generic vendor name (`Claude`, `Codex`, `Grok`, `Gemini`, `OpenCode`) → run
   `dispatch.sh --vendor <vendor> consult "<task>"`.
3. Canonical model alias → pass its vendor, model, and effort from the table
   below. Never silently substitute another model family.
4. No named target → classify into an existing lane and dispatch normally.
5. Unknown or ambiguous nickname → ask for clarification; do not guess or run.

| Alias | Vendor | Model | Effort |
|---|---|---|---|
| Opus | claude | claude-opus-5 | high |
| Fable 5.1 | claude | claude-fable-5-1 | xhigh |
| Sonnet | claude | claude-sonnet-5 | high |
| Haiku | claude | claude-haiku-4-5 | - |
| Sol | codex | gpt-5.6-sol | high |
| Terra | codex | gpt-5.6-terra | max |
| Luna | codex | gpt-5.6-luna | high |
| Astra | codex | gpt-6-astra | xhigh |
| Grok 4.6 | grok | grok-4.6 | - |
| Gemini 3.1 Pro | gemini | Gemini 3.1 Pro (High) | - |
| Gemini 3.8 Flash High | gemini | gemini-3.8-flash-high | - |
| Gemini 3.8 Flash Medium | gemini | gemini-3.8-flash-medium | - |
| Gemini 3.8 Flash Low | gemini | gemini-3.8-flash-low | - |
| Gemini 3.7 Flash | gemini | gemini-3.7-flash-high | - |
| Kimi | kimi | kimi-k3 | - |
| Qwen | qwen | qwen3-coder-plus | - |
| OpenCode | opencode | provider/model form, or `-` for its own default | - |
| OpenRouter | openrouter | explicit OpenRouter slug (e.g. anthropic/claude-sonnet-5) | - |

OpenCode is the multi-provider aggregator CLI (75+ providers): work-capable,
last resort in coding-overflow. OpenRouter is direct-API — no CLI needed, only
`OPENROUTER_API_KEY` — and is **advise/consult only** (it cannot edit files);
its model slug is mandatory. "Ask <any hosted model> via OpenRouter" →
`dispatch.sh --vendor openrouter --model <slug> consult "<task>"`.

Examples:

- Ask Opus to challenge this architecture →
  `dispatch.sh --vendor claude --model claude-opus-5 --effort high consult "challenge this architecture"`
- 請 Grok 查最新公開資訊 →
  `dispatch.sh --vendor grok consult "查最新公開資訊"`
- 哪個模型適合檢查大型 repo？ → answer only; do not dispatch.

Consultation defaults to `advise`. Use `--mode work --workdir <dir>` only for
an explicit edit request. Missing explicit targets fail clearly; never remove
`--vendor` to obtain a fallback.

## Live UI is observation only

The optional Live UI is a read-only observer, not a prompt or dispatch path.
It displays existing jobs' `task.txt` and public `out.txt`, but never raw logs;
its history search and state filters can export only the currently visible public
metadata as local JSON; tokens and task/result bodies are excluded from export.
it cannot interpret natural language, choose routes, dispatch, retry, cancel,
delete jobs, or edit configuration. Natural-language interpretation and
dispatch stay in this skill and the CLI. Manage the local board with
`omnilane ui start|status|url|stop`, and stop it when monitoring is finished.

## Job lifecycle defaults

- **Completion inbox**: with the Claude Code plugin's hooks installed, a
  finished `--background` job is delivered into the foreman's next prompt by
  the bundled `UserPromptSubmit` hook, so do not poll for it. Outside Claude
  Code, block on `scripts/jobs.sh wait <id> [--timeout N]` instead.
- **Live mailbox**: Claude and Gemini retain automatic resident background
  sessions for supported modes. Codex and Grok remain single-shot by default;
  explicit `--live` opts in. Grok live requires explicit `--mode sysops` because
  ACP has no enforceable restricted-mode boundary. Send follow-up instructions
  with `scripts/jobs.sh send <id> "<text>"` and finish with
  `scripts/jobs.sh close <id>`. `--single-shot` forces one-shot execution.
- **Goal orchestration**: when the next step depends on the previous result,
  wrap the dispatches in `omnilane goal open "<objective>" --workdir DIR`, then
  `goal dispatch <goal-id> ...`, `goal note`, `goal status`, `goal close --summary`.
  Budgets are unlimited unless `--budget-jobs` / `--budget-seconds` is passed.
  A single obvious task is dispatched directly, never through a goal.
- **Job hygiene**: `scripts/jobs.sh cancel <id>` stops a runaway job.
  `stats`, `recommend`, and `audit` are read-only and never change routing.

## Rules

- **Dispatch in `advise` mode by default** (read-only worker). Use `--mode work`
  only when the worker must edit files, and give it an explicit `--workdir`.
- **Mode contract**: `advise` is read-only with supported native web tools;
  `work` confines file/command changes to explicit `--workdir` and disables
  agent-tool networking, not the model connection. Codex and Claude have
  distinct policies for these modes. Agy 1.1.27 work has bounded new/resume
  acceptance with native sandboxed commands and four validated tools; external
  temp/cache reads are also restricted. A separate two-turn work live/FIFO
  check passed readback, outside-write denial and normal close. macOS Grok work remains blocked because native child-network
  isolation is Linux-only. Do not turn gaps into sysops implicitly or claim
  every provider/mode/session path has passed the runtime matrix.
- **Agy work tools** are `view_file`, `write_to_file`, `run_command`, and `finish`.
  The native `commandExecutionPolicy: sandbox`, `--sandbox`, and
  `proceed-in-sandbox` policy permits tested workspace edits/builds and denies
  tested outside writes, shell networking and explicit unsandboxed execution.
  Settings are rewritten explicitly before each start/resume: native omission
  of false/empty fields has not been proven default-equivalent. Workspace-local
  caches and the verified empty owned policy directory remain; external cached
  dependencies may be inaccessible, and the tested successful C build still
  emitted an xcrun default-cache denial warning. See the dated capability notes
  for the exact evidence boundary; complete effective SBPL was not captured.
- **`--mode sysops`** explicitly selects unrestricted native policies for
  Codex, Claude, Grok, and Agy; it is not an alias for work. It is a per-dispatch
  opt-in, never a lane default, and task text must name the allowed operations.
  Codex `work`/`sysops` supports non-Git directories through
  `--skip-git-repo-check`. Without an existing whole-job timeout, dispatch
  adds one when its supervisor is available; otherwise it warns and retains
  the per-call watchdog path.
  The CLI defaults an omitted `--workdir` to the caller’s current directory;
  task briefs must still specify it explicitly. The MCP work interface
  separately requires an explicit `workdir`.
- **Grok advise web tools** use internal `web_search` / `web_fetch` selectors,
  while permission rules keep their native `WebSearch` / `WebFetch` class names.
  The complete single-shot `plain` path has real search, fetched-page, and native
  denied-write evidence on Grok 1.0.13. MCP readiness is job-local because this
  mode denies MCPTool; hooks and their security checks remain enabled. A caller
  supplied nonempty `CONTEXT_MODE_MCP_SENTINEL_DIR` is a conflict and stops before provider
  startup rather than being overwritten. Do not extend this result to restricted
  live or macOS work.
- **Every dispatched task states acceptance criteria and the exact verification
  command.** Do not accept "done" without evidence.
- **No nested dispatch**: workers must not fan out again (enforced via
  `OMNILANE_DEPTH`). Escalate back to the main loop instead.
- **Same-directory codex dispatches are serialized automatically** (lock);
  do not try to parallelize them yourself.
- Escalate without asking: two failed attempts on a lane → move one lane up
  (triage → bulk-mechanical → hardest-coding).
- Vendor quota exhausted (429 / "stream disconnected" / usage-limit message):
  send mid-tier coding through coding-overflow instead; never silently downgrade
  hardest-coding — wait or escalate to the user.

## Per-model notes (apply the row matching YOUR main model)

These notes never expand the commander's reserved self-execution scope. If the verified main model has no matching row, use the configured lane table under the current user request and `rules.d/60`; do not assume the nearest older model is equivalent or silently override vendor/model/effort. If a required capability or explicit model choice is unresolved, report that exact gap before dispatch rather than inventing a fallback.

- **Claude Fable 5.1 main**: recommended prompt-level controller for
  quality-sensitive work (not a lane or automatic selector). Hardest coding
  uses max; judgment and taste use xhigh. Dispatch bulk work to Sol high,
  long/fast work to Gemini 3.8 Flash, and use Astra as an independent Codex
  review path.
- **Claude Opus 5 main**: balanced prompt-level controller and independent
  reviewer when explicitly selected (`high`, or `xhigh` for deeper review),
  plus Claude long-context fallback. This is a role/opt-in choice, not a new
  lane or the current hard-judgment default.
- **Claude Sonnet main**: coordination/tools/mid-tier coding only, plus fallback
  duty in bulk-mechanical and live-search; never self-assign top judgment or
  hardest implementation.
- **GPT Astra main**: prompt-level controller backup and independent reviewer.
  Default to xhigh for hardest coding/judgment and consult/taste; use
  `--vendor codex --effort max` only for an explicitly requested upgrade. Explicit
  model and effort always outrank these defaults.
- **GPT Sol main**: bulk mechanical work and constrained UI drafts are yours at
  high; escalate hardest coding and judgment to Fable/Astra.
- **GPT Terra main**: long-context Codex fallback work is yours at max; bulk
  stays on Sol high and hard work escalates to Fable/Astra.
- **GPT Luna main**: high-volume triage is yours at high; do not promote its
  low price into correctness-critical or controller work.
- **Grok 4.6 main**: live-search and coding-overflow are yours, plus fallback
  duty in hard lanes. Grok effort remains ignored; verify API signatures and
  cited facts before shipping.
- **Gemini 3.8 Flash main**: long-context uses medium, fast-agentic and triage
  use low, and bulk/overflow/web fallbacks use high. Do not infer visual taste
  or controller authority from agent/coding benchmarks.
- **Gemini 3.1 Pro main**: remains directly selectable, but is not promoted by
  this refresh; route hard coding and judgment to the stronger configured lanes.
