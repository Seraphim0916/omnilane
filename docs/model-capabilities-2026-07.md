# Model capabilities — July 2026 snapshot

Compiled 2026-07-19, re-audited 2026-07-25 against official model cards and
vendor docs, to inform omnilane's routing defaults and vendor list. Model
capabilities move fast: treat every score as of its cited date, and re-check
the provider's own docs before pinning a model. Benchmark numbers depend
heavily on scaffold: when quoting one, record the model variant/effort, the
tool harness, and single- vs multi-attempt, and never compare raw numbers
across harnesses. Evidence authority is claim-specific: official model cards /
vendor docs control IDs, availability, pricing, and context limits; original
benchmark publishers and Artificial Analysis control their measured results.
Secondary aggregators (Vellum, BenchLM, Morph) are cross-check-only fallbacks.

## Coding benchmarks

### Artificial Analysis Coding Agent Index — do not quote a number

⚠️ **This index has re-based three times in two months and its published values
are not comparable across versions. Cite it for ordering, never for a number.**

Observed values for GPT-5.6 Sol, all describing "leads the index":

| Version | Sol figure | Where it came from |
|---------|-----------:|--------------------|
| v1.1 | 80.0 (max, Codex env) | earlier snapshot in this file |
| v1.2 | 80 (max, Codex harness) | AA GPT-5.6 launch article, 2026-07-09 |
| v1.3 | 78 (xhigh) / 77 (max) | secondary summaries, unverified |
| v1.3 | 67 (tied with Opus 5) | press coverage citing an AA chart |

The v1.1 row that used to sit here (Sol 80.0 / Terra 77.4 / Fable 5 77.2 /
Luna 74.6) is retained above only as provenance. It is **two versions stale**,
and pairing it with a current figure produces a false comparison.

v1.3 composition (AA, current): equal weight over **DeepSWE** (113 tasks,
Datacurve), **Terminal-Bench v2** (84 tasks, Laude Institute), and
**SWE-Atlas-QnA** (124 tasks, Scale AI), each scored as pass@1 averaged over
three attempts.

What can be stated safely, from AA's own articles:

- 2026-07-09: GPT-5.6 Sol (max) in the Codex harness led every one of the three
  component evaluations (tying Grok 4.5 in Grok Build on SWE-Atlas-QnA), at
  ~40% lower cost per task than Claude Fable 5 (max) and ~10% lower than
  Claude Opus 4.8 (max) in Claude Code.
- 2026-07-24: Claude Opus 5 (`xhigh`) with Claude Code is **joint first** on the
  index and holds the highest SWE-Atlas-QnA score.

Retrieval note: AA renders these leaderboards to canvas, so the numbers are not
in the DOM and cannot be scraped. Anyone re-checking this section needs the AA
API or a Premium account; do not substitute a secondary summary for it.

### SWE-Bench Pro

The harder, less-saturated successor to SWE-Bench Verified (OpenAI has flagged
training-data contamination concerns across frontier models on Verified).

| Model | Score |
|-------|------:|
| Claude Fable 5 | 80.0% |
| GPT-5.6 Sol | 64.6% |
| Kimi K2.6 | 58.6 |
| GPT-5.4 | 57.7 |

The 15.4-point Fable 5 vs Sol gap on Pro contrasts with Sol's lead on the
Coding Agent Index — the two measure different things (raw problem-solving vs
agentic tool use). Pin models per lane accordingly.

### SWE-Bench Verified

Now saturated — the frontier clusters in the high 80s, so the meaningful ranking
has moved to SWE-Bench Pro (above). OpenAI stopped reporting Verified in early
2026. Frontier scores per public leaderboard aggregates:

| Model | Score |
|-------|------:|
| Claude Opus 4.8 | 88.6% |
| GPT-5.3 Codex | 85.0% |
| Claude Opus 4.5 | 80.9% |
| Gemini 3.1 Pro | 80.6% |
| Grok 4 (4.3) | ~75% |

Open-weight coders on Verified:

| Model | Score | Notes |
|-------|------:|-------|
| Kimi K2 | 65.8% single / 71.6% multi | K2.6 open-weight, multimodal, long-horizon |
| DeepSeek-V3.2 | ~70% | V4 adds 1M context, open weights, aggressive pricing |
| Qwen3-Coder-480B | 69.6% | Qwen3-Coder-Next is an 80B-total / 3B-active variant |

Open-weight coding leaders (mid-2026): GLM-5.2 (1M context, long-horizon),
MiniMax M3, Kimi K2.7 Code, DeepSeek V4, Qwen3-Coder.

Note: the `qwen3-coder-plus` alias now resolves to the 2025-09-23 snapshot on
Alibaba's side, and Qwen Code ships the newer Qwen 3.6 Plus. Re-evaluate the
coding-overflow backup before swapping — whether the newer alias works on the
local CLI login is unverified.

### Artificial Analysis Intelligence Index (updated 2026-07-24)

Index points, not percentages. Figures below are quoted verbatim from the
Artificial Analysis Opus 5 launch article (2026-07-24).

| Model | Score | Cost/task | Median tok/s | Context |
|-------|------:|----------:|-------------:|---------|
| Claude Opus 5 (max) | 61 | $2.03 | 53 | 1M |
| Claude Opus 5 (xhigh) | 60 | $1.56 | 53 | 1M |
| Claude Fable 5 (with fallback) | 60 | $2.75 | 71 | 1M+ |
| GPT-5.6 Sol (max) | 59 | $1.04 | 66 | 1.05M |
| Claude Opus 5 (high) | 59 | $1.06 | 57 | 1M |
| GPT-5.6 Sol (xhigh) | 58 | $0.68 | 70 | 1.05M |
| Kimi K3 | 57 | $0.95 | 32 | 1.05M |
| GPT-5.6 Sol (high) | 56 | $0.45 | 63 | 1.05M |
| Claude Opus 4.8 (max) | 56 | $1.80 | 56 | 1M |
| GPT-5.6 Terra (max) | 55 | $0.82 | 136 | 1M |
| Grok 4.5 (high) | 54 | $0.31 | 61 | 500K |
| Claude Sonnet 5 (max) | 53 | $1.53 | 76 | 1M |
| GPT-5.6 Luna (max) | 51 | $0.21 | 188 | 1M |
| Gemini 3.6 Flash | 50 | $0.50 | 231 | 1M |
| Gemini 3.5 Flash | 50 | $0.59 | 185 | 1M |
| Gemini 3.1 Pro Preview | 46 | $0.29 | 124 | 1M |
| Claude Haiku 4.5 | 30 | $0.24 | 92 | 200K |

Per-effort rows are what justify the shipped effort levels, and both of the
defaults that look conservative are the cheap side of a flat trade:

- **Opus 5 `xhigh` over `max`** — 1 index point for 30% more cost per task.
- **Sol `xhigh` over `max`** — 1 index point for 53% more cost per task.
  `hardest-coding` nonetheless keeps Sol at `max`, because that lane should be
  ordered by the Coding Agent Index rather than this one, and that index cannot
  currently be quoted (see above). Revisit if AA publishes retrievable v1.3
  per-effort figures.
- **Gemini 3.6 Flash over 3.5 Flash** — same score, 15% cheaper, 25% faster.
- **Gemini 3.1 Pro scores 46**, below 3.6 Flash's 50 and at roughly half its
  throughput. It holds first place in `long-context` on context-retrieval
  grounds, not general intelligence — see the long-context section.

Artificial Analysis calls Opus 5 and Fable 5 "effectively tied" and Opus 5 only
"narrowly the most intelligent" — a 1-point gap here is not a capability verdict.
Epoch AI's Capability Index ranks them the other way (Fable 5 161, Opus 5 159,
tied at 161 on the software-engineering subset), which is the honest reading:
these two are level on general intelligence.

Where they are *not* level is agentic professional output, and that gap is large:

| Benchmark | Claude Opus 5 (max) | Claude Fable 5 | Gap |
|-----------|--------------------:|---------------:|-----|
| AA-Briefcase (agentic knowledge work, Elo) | 1720 | 1574 | +146 |
| GDPval-AA v2 (Elo) | 1861 | 1747 | +114 |
| Cost per AA-Briefcase task | $17.79 | $22.30 | -20% |
| Cost per Intelligence Index task | $2.03 | $2.75 | -26% |

Opus 5's max / xhigh / high tiers sweep the top three AA-Briefcase places, and
the `high` tier ($10.41/task) still beats Fable 5 at under half the cost.

Two results cut the other way and are load-bearing for routing:

- **Factual knowledge**: Opus 5 remains below Fable 5 on AA-Omniscience, as
  expected from the size classes, and answers more readily when uncertain — its
  hallucination rate is 50%, up 14 points from Opus 4.8. Fable 5 is the better
  pick when breadth of recall matters more than agentic execution.
- **Presentation quality**: Opus 5 scores 1628 Presentation Elo against
  GPT-5.6 Sol's 1666 at max. Pure layout/presentation taste is the one axis
  where Sol leads Claude, which is why `taste-final` keeps Sol as its backup
  rather than treating it as a formality.

One operational difference favours Opus 5 over Fable 5 beyond the scores:
Anthropic states Opus 5's cyber classifiers intervene roughly **85% less often**
than Fable 5's, and flagged requests fall back to Opus 4.8 rather than failing.
Refusal rate is a real cost on security, reverse-engineering, and dark-themed
creative work; a lane that silently loses a fraction of its dispatches is worse
than its benchmark position suggests.

## Writing benchmarks — the evidence behind `taste-final`

Until 2026-07-26 this lane was ordered from general and agentic indexes, none of
which measure prose. Three writing-specific benchmarks now cover it. All figures
below were read directly from the publishers, not from secondary summaries.

### EQ-Bench Creative Writing v3 (Elo)

| Model | Elo | Rubric | Slop |
|-------|----:|-------:|-----:|
| **claude-opus-5** | **2429.5** | **85.35** | **0.9** |
| kimi-k3 | 2340.4 | 84.25 | 1.3 |
| gpt-5.6-sol | 2091.8 | 83.90 | 1.6 |
| claude-fable-5 | 2064.6 | 84.05 | 1.4 |
| claude-opus-4-7 | 2031.8 | 82.85 | 1.5 |
| claude-opus-4-8 | 1889.4 | 83.30 | 1.8 |
| grok-4.5 | 1581.3 | 81.25 | 2.5 |
| gemini-3.1-pro-preview | 1459.5 | 80.20 | 4.2 |

`Slop` counts LLM-typical filler phrasing; lower is better. Opus 5's 0.9 is the
lowest on the board, which matters more than the Elo margin for user-facing copy.

### EQ-Bench Longform Writing — closest proxy for docs and reports

| Model | Score | Slop | Degradation |
|-------|------:|-----:|------------:|
| **claude-opus-5** | **86.3** | 5.6 | 0.000 |
| claude-fable-5 | 83.0 | 8.3 | 0.000 |
| claude-opus-4-7 | 81.8 | 9.1 | 0.000 |
| gpt-5.6-sol | 81.7 | 12.0 | 0.000 |
| claude-opus-4-8 | 80.8 | 9.4 | 0.000 |
| kimi-k3 | 79.6 | 9.7 | 0.067 |

`Degradation` measures quality decay across a long passage. Kimi K3 places second
on short-form CW v3 but drops to seventh here and is the only model in this group
that degrades — which is why it stays in `coding-overflow` and does not enter
`taste-final`, whose real workload is long documents.

### Lech Mazur Story-Writing Benchmark (pairwise, 2026-07-18)

| Rank | Model | Score | Est. win rate |
|-----:|-------|------:|--------------:|
| 1 | Claude Fable 5 (high) | 3.3 | 91% |
| 2 | GPT-5.5 (xhigh) | 3.0 | 88% |
| 3 | Kimi K3 | 2.9 | 87% |
| 4 | GPT-5.6 Sol (xhigh) | 2.9 | 87% |
| 8 | Claude Opus 4.7 (adaptive) | 2.4 | 82% |
| 12 | Claude Opus 4.8 (xhigh) | 1.3 | 69% |
| 31 | Gemini 3.1 Pro Preview | -1.8 | 24% |
| **39** | **Grok 4.5 (high)** | **-4.6** | **2%** |

**Claude Opus 5 is absent** — the leaderboard's last refresh predates its
release, so this board cannot yet arbitrate the current default.

Two findings worth carrying forward: Fable 5 leads the one writing board Opus 5
has not entered, and **Grok 4.5 places last of 39** with a 2% expected win rate,
which is a hard veto against ever routing Grok into a prose lane.

### What this settles

`taste-final: claude claude-opus-5 high | codex gpt-5.6-sol max` is confirmed on
both boards that have measured Opus 5, on which it ranks first outright. Sol is
third and fourth respectively — the strongest non-Claude option, so its backup
slot is earned rather than nominal. The open question is Mazur, where Fable 5
leads and Opus 5 is untested.

A cross-check on the "newer is better at prose" assumption: Opus 4.8 scores below
Opus 4.7 on **both** CW v3 (1889.4 vs 2031.8) and Mazur (1.3 vs 2.4), matching
Anthropic's coding/agentic tuning focus for that release. Opus 5 then recovers
and leads. Tuning direction can cost prose quality, so a new release does not
inherit this lane by default — re-measure each time.

## Long-context retrieval vs synthesis — unresolved for `long-context`

⚠️ **Secondary sources only, and the published figures cover prior model
generations (Opus 4.6, Gemini 3 Pro) rather than the current defaults. Treat
this section as a flagged question, not as settled evidence.**

A shared 1M context window does not imply shared behaviour at that length. The
public benchmarks split along one axis:

| Task shape | Leader | Reported figure |
|------------|--------|-----------------|
| Single-needle retrieval @ 1M | Gemini 3.1 Pro | only model sustaining >90% on RULER |
| Single-needle MRCR @ 1M | DeepSeek V4 Pro 83.5% | Gemini 3.1 Pro 76.3% |
| **Multi-needle / multi-hop @ 1M** | **Claude** | Opus 4.6 ~76-78% vs Gemini 3 Pro 24.5%, GPT-5.4 36.6% |
| Either shape @ 128K | tied | Gemini 3.1 Pro ≈ Claude Sonnet 4.6, both 84.9% |

The gap on multi-hop work is not marginal — roughly threefold in the numbers
above. Pulling one fact out of a large corpus and integrating facts scattered
across it are different capabilities, and the leaders differ.

This matters because `long-context` ships Gemini 3.1 Pro first while its comment
described the lane as long-document *synthesis*, which is the multi-hop shape.
The lane comment has been narrowed to say retrieval and volume, and to point at
the Claude candidate for integration work. **The ordering is unchanged**: the
evidence above is secondary and generation-stale, which is not a sufficient basis
for moving a shipped default. Re-check against first-party numbers for Gemini 3.1
Pro and Opus 5 before reordering.

## Pricing and context windows (July 2026)

Pay-per-token API list price, USD per 1M tokens (input / output). Prices move
often — treat as of the cited date and confirm on each provider's pricing page.
Filling one 1M-token input request ranges from ~$0.14 (DeepSeek V4 Flash) to ~$10
(Claude Fable 5), a ~70x spread, so routing cheap lanes to cheap models matters.

| Model | Input $/1M | Output $/1M | Context |
|-------|-----------:|------------:|---------|
| GPT-5.4 | 2.50 | 15.00 | 1M |
| GPT-5.5 | 5.00 | 30.00 | 1M |
| GPT-5.6 Sol | 5.00 | 30.00 | 1.05M |
| GPT-5.6 Terra | 2.50 | 15.00 | 1.05M |
| GPT-5.6 Luna | 1.00 | 6.00 | 1.05M |
| Claude Opus 5 | 5.00 | 25.00 | 1M |
| Claude Opus 4.8 | 5.00 | 25.00 | 200K |
| Claude Sonnet 4.6 | 3.00 | 15.00 | 1M+ |
| Claude Sonnet 5 | 2.00* | 10.00* | 1M |
| Claude Fable 5 | 10.00 | 50.00 | 1M+ |
| Gemini 3.1 Pro | 2.00** | 12.00** | 1M |
| Gemini 3.6 Flash | 1.50 | 7.50 | 1M |
| Gemini 3.5 Flash | 1.50 | 9.00 | 1M |
| Gemini 3.5 Flash-Lite | 0.30 | 2.50 | — |
| Grok 4.5 | 2.00 | 6.00 | 500K |
| DeepSeek V3 | 0.27 | 1.10 | — |
| DeepSeek V4 Flash | 0.14 | 0.28 | 1M (384K output) |
| Z.ai GLM-5.2 | 1.40 | 4.40 | 1M |
| Mistral Ministral 3 (3B) | 0.10 | 0.10 | — |
| Kimi K3 | 3.00*** | 15.00 | 1M |

\* Sonnet 5 launch pricing runs through 2026-08-31; standard pricing is $3/$15.
\** Gemini 3.1 Pro requests above 200K tokens are $4/$18.
\*** Kimi K3 cache-miss input is $3; cache-hit input is $0.30.

Long-context tiers materially change comparisons: GPT-5.6 requests above 272K
input tokens charge 2x input and 1.5x output, while Grok 4.5 requests above 200K
charge $4 input / $12 output.

Groq and Cerebras are fast-inference hosts (Llama / Qwen / gpt-oss families); the
per-token price depends on which hosted model you pick — see their pricing pages.
GPT-5.6 Sol / Terra / Luna prices and 1.05M context are from OpenAI's official
model catalog (checked 2026-07-21). Gemini Flash prices are from Google's
Gemini API pricing page (checked 2026-07-22, post 3.6 Flash launch: 3.6 Flash
$1.50/$7.50, 3.5 Flash $1.50/$9.00, 3.5 Flash-Lite $0.30/$2.50).

## Frontier models omnilane routes (codex / claude / gemini / grok lanes)

- **Gemini 3.1 Pro** (Google, 2026-02): SWE-bench Verified 80.6%, LiveCodeBench
  Pro ~2,439 Elo (leads), 1M context per the official model card (earlier "~2M"
  figures came from secondary aggregators and are wrong). The model card lists
  it as agentic-capable — Terminal-Bench 70.3%, MCP Atlas 78.2%; Gemini 3.5
  Flash scores 76.2% / 83.6% on the same pair. Routing prefers Flash for fast
  agentic loops on speed/cost grounds, not because Pro cannot run loops.
  omnilane reaches it through the `gemini` lane (the `agy` CLI).
- **Gemini 3.6 Flash** (Google, released 2026-07-21): the new Flash workhorse.
  Google reports 17% fewer output tokens than 3.5 Flash at a lower output
  price; AA measures Intelligence Index 50 (#21/186) and 303.6 tok/s output
  speed (#1/186), 1M context. Replaces 3.5 Flash as the gemini candidate in
  fast-agentic / triage / bulk-mechanical. Released alongside 3.5 Flash-Lite
  (fastest/cheapest 3.5-class, not exposed by the agy CLI, so not routable)
  and 3.5 Flash Cyber (security-specialized, government/trusted-partner
  pilot only — no public API or CLI).
- **Grok 4.5** (xAI, GA 2026-07-16): a low-cost frontier coder ($2/$6);
  Terminal-Bench 83.3%, SWE-Bench Pro 64.7% per xAI's launch post. Artificial
  Analysis measures a 54% hallucination rate — more capable but also more
  willing to guess, so verify every factual claim it ships. omnilane's `grok`
  lane.
- **Claude Opus 5** (Anthropic, GA 2026-07-24): API ID
  `claude-opus-5`; 1M default/max context, 128K max output, adaptive thinking
  on by default, and five effort levels through `max`. Artificial Analysis
  measures Intelligence 61 at `max`, ahead of Sol 59, and joint-first coding at
  `xhigh`. Its $5/$25 list price matches Opus 4.8.
- **Claude Fable 5** (Anthropic, GA): Anthropic positions it above Opus 5.
  Its absence from omnilane's default lanes is a cost / guardrail / main-loop
  policy choice — the top Claude tier is usually the main loop itself — not a
  capability verdict. On the writing boards it still leads Mazur, the one board
  Opus 5 has not yet entered, while trailing on both EQ-Bench boards; see the
  writing benchmarks section.
- **GPT-5.6 Sol / Terra / Luna** and **Claude Opus 5 / Opus 4.8 / Fable 5** are covered in
  the benchmark and pricing tables above; they back the `codex` and `claude`
  lanes.

## Terminal-native coding CLIs / harnesses

Context for where omnilane sits (a router across these, not a replacement).

| Tool | GitHub stars (approx) | Note |
|------|----------------------:|------|
| OpenCode | ~165k | Provider-agnostic harness, 75+ providers |
| OpenAI Codex CLI | ~85k | Open source, tuned for OpenAI models |
| Cline | ~62k | Model-agnostic across IDE/CLI/SDK |
| Pi | 50k+ | 2026 entrant (Zechner / Ronacher) |
| Aider | — | Git-native terminal workflow |
| OpenHands | — | Fully autonomous feature delegation |
| Crush (Charm) | — | Ollama + OpenAI-compatible endpoints |

(Roo Code, a Cline fork, was archived May 2026.)

## Direct-API vendors omnilane supports

All OpenAI-compatible `/chat/completions` (advise-only inference lanes via
`run-openai-compat.sh`; curl + an API key, no CLI). Base URLs verified against
each provider's July 2026 docs; override any with `<VENDOR>_BASE_URL`.

| Vendor | Base URL | API key env | Suggested coding models |
|--------|----------|-------------|-------------------------|
| openrouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | catalog slugs (`anthropic/…`, `openai/…`) |
| deepseek | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `deepseek-chat`, `deepseek-reasoner` → `deepseek-v4-flash` after 2026-07-24 |
| zai | `https://api.z.ai/api/openai/v1` | `ZAI_API_KEY` | `glm-4.6` |
| mistral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` | `devstral-latest`, `codestral-latest`, `mistral-medium-latest` |
| groq | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `openai/gpt-oss-120b`, `qwen/qwen3.6-27b` (131k ctx, very fast) |
| cerebras | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` | `gpt-oss-120b`, `qwen-3-32b`, `llama-3.3-70b` (very fast) |

DeepSeek and Z.ai also expose Anthropic-compatible endpoints
(`https://api.deepseek.com/anthropic`, `https://api.z.ai/api/anthropic`); Z.ai's
Coding Plan uses a separate `/api/coding/paas/v4` path. Exact model slugs change
— confirm against each provider's `/models` endpoint.

## Sources

Authority depends on the claim: tier 1 controls model IDs, availability, prices,
and context limits; tier 2 controls the benchmark results it measured. Tier 3
is only a cross-check when tiers 1-2 do not publish the needed fact.

Tier 1 — official vendor docs:

- OpenAI model catalog (GPT-5.6 pricing/context): <https://developers.openai.com/api/docs/models>
- OpenAI GPT-5.6 launch and benchmark table: <https://openai.com/index/gpt-5-6/>
- Gemini 3.1 Pro model card: <https://deepmind.google/models/model-cards/gemini-3-1-pro>
- Gemini official evals (3.5 Flash): <https://deepmind.google/models/gemini/>
- xAI Grok 4.5 launch post: <https://x.ai/news/grok-4-5>
- xAI Grok 4.5 model details: <https://docs.x.ai/developers/models/grok-4.5>
- Anthropic Claude Opus 5 model and migration notes: <https://platform.claude.com/docs/en/about-claude/models/whats-new-opus-5>
- Anthropic Claude Fable 5 / Mythos 5: <https://www.anthropic.com/news/claude-fable-5-mythos-5>
- Anthropic Claude Sonnet 5: <https://www.anthropic.com/news/claude-sonnet-5>
- Moonshot Kimi K3 launch and pricing: <https://www.kimi.com/blog/kimi-k3>
- Qwen Code updates (Qwen 3.6 Plus): <https://qwenlm.github.io/qwen-code-docs/en/blog/updates/weekly-update-2026-04-09/>
- Alibaba Model Studio pricing (qwen3-coder-plus snapshot mapping): <https://help.aliyun.com/en/model-studio/model-pricing>
- DeepSeek API docs: <https://api-docs.deepseek.com/>
- Z.ai developer docs: <https://docs.z.ai/devpack/quick-start>
- Mistral Codestral: <https://mistral.ai/news/codestral/>
- Groq OpenAI compatibility: <https://console.groq.com/docs/openai>
- Cerebras model catalog: <https://inference-docs.cerebras.ai/models/overview>

Tier 2 — benchmark publishers:

- Artificial Analysis — Coding Agent Index: <https://artificialanalysis.ai/agents/coding-agents>
- Artificial Analysis — model leaderboard (index, cost/task, throughput; the one AA surface that renders to DOM): <https://artificialanalysis.ai/leaderboards/models>
- Artificial Analysis — Claude Opus 5 evaluation (2026-07-24): <https://artificialanalysis.ai/articles/opus-5>
- Artificial Analysis — GPT-5.6 evaluation (2026-07-09): <https://artificialanalysis.ai/articles/gpt-5-6-has-landed>
- Artificial Analysis — Grok 4.5 analysis (hallucination rate): <https://artificialanalysis.ai/articles/grok-4-5-brings-spacexai-to-the-the-intelligence-frontier>
- EQ-Bench Creative Writing v3 (Elo, rubric, slop): <https://eqbench.com/creative_writing.html>
- EQ-Bench Longform Writing (score, slop, degradation): <https://eqbench.com/creative_writing_longform.html>
- Lech Mazur Story-Writing Benchmark (pairwise): <https://github.com/lechmazur/writing>
- Anthropic Claude Opus 5 announcement (classifier intervention rate, effort behaviour): <https://www.anthropic.com/news/claude-opus-5>

Tier 3 — secondary aggregators (cross-check before trusting; the retired "~2M
Gemini context" claim came from this tier):

- Artificial Analysis Intelligence Index (BenchLM mirror, 2026-07-18): <https://benchlm.ai/benchmarks/artificialAnalysis>
- GPT-5.6 benchmarks explained (Vellum): <https://www.vellum.ai/blog/gpt-5-6-benchmarks-explained>
- Best open-weight AI models 2026 (Kingy): <https://kingy.ai/news/best-open-weight-ai-models-in-2026-glm-5-2-vs-deepseek-v4-vs-kimi-k2-6-vs-qwen-vs-mistral/>
- awesome-cli-coding-agents: <https://github.com/bradAGI/awesome-cli-coding-agents>
- LLM API pricing (BenchLM, July 2026): <https://benchlm.ai/llm-pricing>
- LLM context window comparison (Morph): <https://www.morphllm.com/llm-context-window-comparison>
- SWE-bench Verified leaderboard (BenchLM): <https://benchlm.ai/benchmarks/sweVerified>
- Google Gemini 3 benchmarks (Vellum): <https://www.vellum.ai/blog/google-gemini-3-benchmarks>
