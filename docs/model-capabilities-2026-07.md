# Model capabilities — July 2026 snapshot

Compiled 2026-07-19 from public benchmarks and each provider's own API docs, to
inform omnilane's routing defaults and vendor list. Model capabilities move
fast: treat every score as of its cited date, and re-check the provider's own
docs before pinning a model. Benchmark numbers depend heavily on scaffold and
are quoted from the sources listed at the bottom, not re-measured here.

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

### SWE-Bench Verified (open-weight coders)

| Model | Score | Notes |
|-------|------:|-------|
| DeepSeek-V3.2 | ~70% | V4 preview adds 1M context, open weights, aggressive pricing |
| Qwen3-Coder-480B | 69.6% | Qwen3-Coder-Next is an 80B-total / 3B-active efficient variant |
| Kimi K2 | 65.8% single / 71.6% multi | K2.6 is open-weight, natively multimodal, long-horizon |

Open-weight coding leaders (mid-2026): GLM-5.2 (1M context, long-horizon),
MiniMax M3, Kimi K2.7 Code, DeepSeek V4, Qwen3-Coder.

### Artificial Analysis Intelligence Index (2026-07-18, 165 models)

| Model | Score | Context |
|-------|------:|---------|
| Claude Fable 5 | 59.9% | 1M+ |
| GPT-5.6 Sol | 58.9% | 1M |
| Kimi K3 | 57.1% | 1.05M |

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

- Artificial Analysis — Coding Agent Index: <https://artificialanalysis.ai/agents/coding-agents>
- Artificial Analysis Intelligence Index (BenchLM mirror, 2026-07-18): <https://benchlm.ai/benchmarks/artificialAnalysis>
- GPT-5.6 benchmarks explained (Vellum): <https://www.vellum.ai/blog/gpt-5-6-benchmarks-explained>
- Best open-weight AI models 2026 (Kingy): <https://kingy.ai/news/best-open-weight-ai-models-in-2026-glm-5-2-vs-deepseek-v4-vs-kimi-k2-6-vs-qwen-vs-mistral/>
- awesome-cli-coding-agents: <https://github.com/bradAGI/awesome-cli-coding-agents>
- DeepSeek API docs: <https://api-docs.deepseek.com/>
- Z.ai developer docs: <https://docs.z.ai/devpack/quick-start>
- Mistral Codestral: <https://mistral.ai/news/codestral/>
- Groq OpenAI compatibility: <https://console.groq.com/docs/openai>
- Cerebras model catalog: <https://inference-docs.cerebras.ai/models/overview>
