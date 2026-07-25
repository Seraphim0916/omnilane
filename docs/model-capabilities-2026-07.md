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

### Artificial Analysis Coding Agent Index (v1.1)

Agentic coding — terminal workflows, tool coordination, real codebase
navigation. This is the index omnilane's routing defaults track.

| Model | Score |
|-------|------:|
| GPT-5.6 Sol (max reasoning, Codex env) | 80.0 |
| GPT-5.6 Terra | 77.4 |
| Claude Fable 5 | 77.2 |
| GPT-5.6 Luna | 74.6 |

2026-07-24 addendum: Artificial Analysis reports Claude Opus 5 (`xhigh`,
Claude Code harness) joint first on the Coding Agent Index and highest on
SWE-Atlas-QnA. The publisher did not expose a stable numeric aggregate in the
article text, so the earlier numeric snapshot above remains dated rather than
being silently overwritten.

Sol beats Fable 5 by 2.8 points while using less than half the output tokens.

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

| Model | Score | Context |
|-------|------:|---------|
| Claude Opus 5 (max) | 61 | 1M |
| Claude Fable 5 | 59.9% | 1M+ |
| GPT-5.6 Sol | 58.9% | 1M |
| Kimi K3 | 57.1% | 1M |

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
  capability verdict.
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
- Artificial Analysis — Claude Opus 5 evaluation (2026-07-24): <https://artificialanalysis.ai/articles/opus-5>
- Artificial Analysis — Grok 4.5 analysis (hallucination rate): <https://artificialanalysis.ai/articles/grok-4-5-brings-spacexai-to-the-the-intelligence-frontier>

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
