---
name: omnilane
description: 'Universal model-routing table + cross-vendor dispatch for ANY harness (Claude Code, Codex, Grok Build, Antigravity). Use when delegating subtasks, choosing a model for work, planning multi-part tasks, or when asked about model routing, delegate, dispatch, which model, tier selection, escalate, 派工, 模型路由. One routing table; the main loop self-executes its own lane and shells out to every other vendor via dispatch.sh.'
---

# omnilane — one routing table, every harness

You (the main loop) may be Claude, GPT, Grok, or Gemini. The procedure is identical:

1. **Identify your main model.** You know which model you are running as.
2. **Split the work into subtasks and classify each into a lane** (table below).
3. **If the lane's model is you, self-execute.** Otherwise dispatch:
   `<repo>/scripts/dispatch.sh [--mode work] [--workdir DIR] <lane> "<task>"`
   Add `--background` for long tasks; poll with `scripts/jobs.sh status|result <id>`.

Run `scripts/dispatch.sh --list` to see the effective table (local overrides win).
Lanes are fallback chains — dispatch uses the first vendor CLI actually installed,
so the same table works with any subset of subscriptions.

## Lanes (defaults; see routing.yaml for the live values)

| Lane | Default model | When |
|---|---|---|
| hardest-coding | GPT-5.6 Sol (xhigh) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| bulk-mechanical | GPT-5.6 Terra (max) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| triage | GPT-5.6 Luna (medium) | High-volume scans, first-pass filtering |
| hard-judgment | GPT-5.6 Sol (max) | Architecture arbitration, deep reasoning, second opinions |
| taste-final | Claude Opus 4.8 | User-facing prose, prompt/doc polish, Chinese phrasing, style arbitration |
| ui-draft | GPT-5.6 Sol (xhigh) | UI drafts only WITH a design system / reference images; open-ended visual taste goes to taste-final |
| long-context | Gemini 3.1 Pro (High) | 1M-token synthesis across giant docs — analysis only, never agentic loops |
| fast-agentic | Gemini 3.5 Flash (High) | Fast multi-step agentic loops, multimodal checks |
| live-search | Grok 4.5 | Realtime X/web search and social context |
| coding-overflow | Grok 4.5 | Codex-quota relief valve for mid-tier coding; verify factual claims |
| arbitrate | vote: codex+claude+grok | Built-in opinion panel for big calls — one quota hit PER VOTER PER ROUND; you chair: read the opinions and own the decision. Effort field 2 = debate round (voters rebut each other); add gemini for a 4-voter panel |

## Rules

- **Dispatch in `advise` mode by default** (read-only worker). Use `--mode work`
  only when the worker must edit files, and give it an explicit `--workdir`.
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

- **Claude (Fable/Opus main)**: top judgment and taste are yours — self-execute;
  push mechanical coding volume out to the codex lanes.
- **Claude Sonnet main**: coordination/tools/mid-tier coding only; never
  self-assign top judgment or hardest implementation.
- **GPT Sol main**: hardest coding + hard judgment are yours (use max for
  judgment turns, xhigh for coding); cross to taste-final for style calls.
- **GPT Terra main**: bulk work is yours at max; escalate the genuinely hardest
  pieces to Sol instead of grinding.
- **Grok 4.5 main**: mid-tier coding + live-search are yours; verify every API
  signature and cited fact before shipping (measured high hallucination rate).
- **Gemini Flash main**: fast agentic/multimodal loops are yours; never
  self-assign top judgment.
- **Gemini 3.1 Pro main**: 1M-context synthesis is yours; route almost
  everything else to the stronger lanes. Never take agentic tool-loop chains
  (bottom-tier agentic score) — hand those to fast-agentic or a codex lane.
