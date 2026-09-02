---
name: omnilane
description: 'Universal model-routing table + cross-vendor dispatch for ANY harness (Claude Code, Codex, Grok Build, Antigravity). Use when delegating subtasks, choosing a model for work, planning multi-part tasks, or when asked about model routing, delegate, dispatch, which model, tier selection, escalate, 派工, 模型路由. One routing table; implementation work dispatches by default via dispatch.sh — the main loop self-executes only reserved commander items.'
---

# omnilane — one routing table, every harness

You (the main loop) may be Claude, GPT, Grok, or Gemini. The procedure is identical:

1. **Identify your main model.** You know which model you are running as.
2. **Split the work into subtasks and classify each into a lane** (table below).
3. **Dispatch implementation work by default — even when the lane's model is
   you.** Self-execute only reserved commander items: planning and
   decomposition, writing task briefs, reviewing reports, acceptance checks,
   replies to the operator, git commit/push, read-only verification, and
   fixes of one line or less. Dispatch:
   `<repo>/scripts/dispatch.sh [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"`
   Add `--background` for long tasks; poll with `scripts/jobs.sh status|result <id>`.
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
| hardest-coding | Claude Fable 5.1 (xhigh) | GPT-5.6 Sol (xhigh) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| bulk-mechanical | GPT-5.6 Sol (high) | Gemini 3.7 Flash (High) → Claude Sonnet 5 (high) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| triage | GPT-5.6 Luna (high) | Gemini 3.7 Flash (Low) → Claude Haiku 4.5 | High-volume scans, first-pass filtering |
| hard-judgment | Claude Fable 5.1 (xhigh) | GPT-5.6 Sol (max) → Grok 4.6 | Architecture arbitration, deep reasoning, second opinions |
| taste-final | Claude Fable 5.1 (high) | GPT-5.6 Sol (max) | User-facing prose, prompt/doc polish, Chinese phrasing, style arbitration |
| consult | GPT-5.6 Sol (max) | Claude Fable 5.1 (high) → Grok 4.6 → Gemini 3.7 Flash (High) | Direct named-model consultation; always keep `--vendor` |
| ui-draft | GPT-5.6 Sol (xhigh) | Claude Fable 5.1 (high) | UI drafts only WITH a design system / reference images; open-ended visual taste goes to taste-final |
| long-context | Gemini 3.7 Flash (Medium) | GPT-5.6 Terra (max) → Claude Opus 5 (medium) | Long-context synthesis ordered on AA-LCR, then cost and throughput |
| fast-agentic | Gemini 3.7 Flash (Medium) | GPT-5.6 Luna (high) | Fast multi-step agentic loops, multimodal checks |
| live-search | Grok 4.6 | — (off) | Realtime X/web search and social context |
| coding-overflow | Grok 4.6 | Gemini 3.7 Flash (High) → Kimi K3 → Qwen3 Coder Plus → OpenCode | Codex-quota relief valve for mid-tier coding; verify factual claims |
| arbitrate | off (opt-in vote panel) | — | Disabled by default. Enable with `arbitrate: vote codex,claude,grok -` in routing.local.yaml or via the configurator (any 1-4 voters). One quota hit PER VOTER PER ROUND; you chair: read the opinions and own the decision. Effort field 2 = debate round (voters rebut each other) |

Claude Fable 5.1 (`claude-fable-5-1`) is in the judgment, taste, and
hardest-coding defaults because it leads Opus 5 on every Artificial Analysis
axis at the same effort. It is not in bulk or triage because it prices at twice
Opus 5 per token and consumes the most subscription quota per turn. Opus 5
remains the lower-hallucination, lower-price Claude choice and can return to any
lane via `~/.omnilane/routing.local.yaml`, for example:
`hard-judgment: claude claude-opus-5 xhigh`.

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
| Fable 5.1 | claude | claude-fable-5-1 | high |
| Sonnet | claude | claude-sonnet-5 | high |
| Haiku | claude | claude-haiku-4-5 | - |
| Sol | codex | gpt-5.6-sol | max |
| Terra | codex | gpt-5.6-terra | max |
| Luna | codex | gpt-5.6-luna | high |
| Grok 4.6 | grok | grok-4.6 | - |
| Gemini 3.1 Pro | gemini | Gemini 3.1 Pro (High) | - |
| Gemini 3.7 Flash | gemini | Gemini 3.7 Flash (High) | - |
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

## Rules

- **Dispatch in `advise` mode by default** (read-only worker). Use `--mode work`
  only when the worker must edit files, and give it an explicit `--workdir`.
- **`--mode sysops`** is `work` minus the vendor sandbox, for service
  operations the sandbox denies (launchctl, system daemons). Codex runs with
  `-s danger-full-access`; other vendors treat it as `work`. Explicit
  per-dispatch opt-in only — never a lane default, and the task text must
  name the exact service commands the worker is authorized to run.
  Codex `work`/`sysops` still needs a git-repo `--workdir` (non-git
  directories trip the whole-job fuse).
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

- **Claude Fable 5.1 main**: hard judgment, taste finalization, and the hardest
  coding are yours. Dispatch bulk work to Sol high and long-context or fast
  loops to Gemini 3.7 Flash.
- **Claude Opus 5 main**: judgment and taste remain strong self-execute lanes;
  use local overrides when its lower hallucination rate or price is preferred.
- **Claude Sonnet main**: coordination/tools/mid-tier coding only; never
  self-assign top judgment or hardest implementation.
- **GPT Sol main**: hardest coding + hard judgment are yours (use max for
  judgment turns, xhigh for coding); cross to taste-final for style calls.
- **GPT Terra main**: long-context Codex fallback work is yours at max;
  bulk-mechanical now defaults to Sol high, and genuinely hardest pieces
  escalate to Sol xhigh.
- **Grok 4.6 main**: live-search and coding overflow are yours; its measured
  hallucination rate is the lowest among the frontier rows, but still verify
  every API signature and cited fact before shipping.
- **Gemini 3.7 Flash main**: long-context and fast agentic/multimodal loops
  are yours at the lane's configured effort; bulk and overflow use the high row.
  Never self-assign top judgment.
- **Gemini 3.1 Pro main**: it remains directly selectable, but the default
  long-context lane now prefers Gemini 3.7 Flash on LCR, cost, and throughput;
  route hardest coding and judgment to the stronger Codex and Claude lanes.
