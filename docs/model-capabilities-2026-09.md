# Model capabilities — September 2026 snapshot

All external records and pricing in this document were retrieved 2026-09-02.
This is the dated evidence behind the defaults in `routing.yaml`; the July
snapshot remains historical and is not rewritten.

## A. Sources and method

- **Artificial Analysis (AA):** per-configuration flight records from
  `artificialanalysis.ai/models/claude-fable-5-1` and the corresponding model
  records, retrieved 2026-09-02.
- **`$/task`:** AA Intelligence-Index cost per task, reconstructed as the sum
  of the nine evaluation components' `weightedCostPerTask` values.
- **Coding Index:** Terminal-Bench v2.1 × ⅔ + SciCode × ⅓.
- **Base slugs:** Sol, Terra, Luna, Kimi K3, and Claude resolve to their maximum
  effort rows; Grok 4.6 and Gemini Flash resolve to high; Gemini 3.1 Pro Preview
  has no effort label. Fable 5.1 rows are AA's “Default Fallback” composite.
- **Runtime caveats:** `run-grok.sh` ignores effort, so the AA effort row
  reproduced by the CLI is unknown. AA `deprecated` is a dataset flag and
  does not mean a model ID is retired from a CLI.
- **Cross-checks:** Anthropic's Fable 5.1 announcement table (2026-09-01) and
  Google's Gemini 3.7 Flash launch post (2026-08-13), both retrieved 2026-09-02.

### First-party pricing context

| Model | Input / output per million tokens | Cache-hit input | Pricing note |
|---|---:|---:|---|
| Claude Fable 5.1 | $10 / $50 | $0.25 (0.025×) | Standard price, retrieved 2026-09-02 |
| Claude Opus 5 | $5 / $25 | $0.50 | Standard price, retrieved 2026-09-02 |
| Gemini 3.7 Flash | $0.75 / $3.75 | — | Introductory through 2026-12-31; then $1.50 / $7.50 |
| GPT-5.6 Sol | OpenAI Codex rate card | — | Promotional pricing through at least 2026-11-21 |

AA cost per task does not model cache-hit pricing. Subscription CLI quota is a
separate operational constraint from API dollars.

## B. Candidate configurations

All rows were retrieved 2026-09-02.

| Config | Intel | Agentic | Coding | LCR | Omni | Halluc | GDPval | $/task | tok/s | TTFA s |
|---|---|---|---|---|---|---|---|---|---|---|
| Claude Fable 5.1 (max) | 65.7 | 61.3 | 81.6 | .80 | 43.5 | .73 | 1853 | 3.689 | 66 | 157 |
| Claude Fable 5.1 (xhigh) | 64.8 | 59.8 | 80.7 | .78 | 42.4 | .71 | 1835 | 2.651 | 59 | 42 |
| Claude Fable 5.1 (high) | 62.5 | 55.8 | 79.1 | .77 | 40.8 | .69 | 1743 | 1.430 | 48 | 7.6 |
| Claude Fable 5.1 (medium) | 60.5 | 52.7 | 77.1 | .79 | 37.6 | .69 | 1670 | 1.001 | 48 | 4.8 |
| Claude Fable 5.1 (low) | 58.1 | 49.2 | 75.2 | .79 | 34.1 | .66 | 1587 | 0.774 | 56 | 5.7 |
| Claude Opus 5 (max) | 63.1 | 59.2 | 78.0 | .76 | 37.1 | .61 | 1824 | 2.337 | 54 | 35 |
| Claude Opus 5 (xhigh) | 62.5 | 58.4 | 77.0 | .76 | 35.4 | .60 | 1797 | 1.801 | 52 | 25 |
| Claude Opus 5 (high) | 61.5 | 56.1 | 76.5 | .76 | 33.7 | .61 | 1719 | 1.227 | 48 | 13 |
| Claude Opus 5 (medium) | 58.6 | 50.4 | 74.3 | .79 | 31.0 | .61 | 1613 | 0.724 | 48 | 6.0 |
| Claude Opus 5 (low) | 52.5 | 42.1 | 66.9 | .77 | 28.6 | .62 | 1454 | 0.425 | 49 | 3.1 |
| Claude Sonnet 5 (max) | 55.3 | 49.7 | 71.5 | .77 | 16.4 | .39 | 1584 | 1.717 | 72 | 134 |
| Claude Sonnet 5 (high) | n/a | n/a | n/a | n/a | n/a | n/a | 1397 | n/a | 61 | 7.0 |
| Claude Haiku 4.5 (reasoning) | 29.9 | 16.5 | 43.9 | .74 | -4.4 | .27 | 915 | 0.217 | 92 | 9.9 |
| GPT-5.6 Sol (max) | 60.9 | 57.8 | 77.4 | .78 | 22.0 | .92 | 1710 | 0.953 | 75 | 99 |
| GPT-5.6 Sol (xhigh) | 59.0 | 53.6 | 78.3 | .76 | 21.0 | .92 | 1672 | 0.628 | 77 | 30 |
| GPT-5.6 Sol (high) | 57.3 | 50.6 | 77.2 | .75 | 20.4 | .91 | 1616 | 0.427 | 76 | 15 |
| GPT-5.6 Sol (medium) | 55.6 | 47.9 | 76.3 | .74 | 19.4 | .91 | 1543 | 0.290 | 71 | 6.1 |
| GPT-5.6 Terra (max) | 56.6 | 50.2 | 76.7 | .80 | 0.1 | .88 | 1566 | 0.526 | 105 | 104 |
| GPT-5.6 Terra (xhigh) | 52.8 | 46.5 | 70.6 | .75 | -3.0 | .89 | 1563 | 0.318 | 88 | 19 |
| GPT-5.6 Luna (max) | 52.3 | 46.9 | 71.4 | .78 | -10.3 | .93 | 1569 | 0.049 | 128 | 101 |
| GPT-5.6 Luna (xhigh) | 50.1 | 44.4 | 68.6 | .73 | -10.8 | .92 | 1515 | 0.033 | 120 | 41 |
| GPT-5.6 Luna (high) | 47.0 | 41.0 | 63.3 | .74 | -12.0 | .92 | 1457 | 0.022 | 120 | 11 |
| GPT-5.6 Luna (medium) | 38.9 | 31.8 | 50.7 | .72 | -13.2 | .91 | 1269 | 0.012 | 115 | 2.5 |
| Gemini 3.7 Flash (High) | 56.0 | 45.1 | 76.1 | .80 | 26.5 | .65 | 1516 | 0.402 | 285 | 7.0 |
| Gemini 3.7 Flash (Medium) | 53.4 | 45.1 | 71.5 | .81 | 23.7 | .66 | 1492 | 0.263 | 282 | 5.4 |
| Gemini 3.7 Flash (Low) | 50.9 | 41.6 | 71.0 | .78 | 22.1 | .68 | 1446 | 0.165 | 292 | 1.0 |
| Gemini 3.6 Flash (High) | 51.6 | 40.5 | 69.2 | .79 | 22.1 | .56 | 1414 | 0.344 | 167 | 14 |
| Gemini 3.1 Pro Preview | 47.7 | 23.0 | 68.8 | .79 | 31.9 | .51 | 965 | 0.335 | 103 | 25 |
| Grok 4.6 (high, base) | 60.9 | 58.7 | 76.8 | .75 | 30.5 | .34 | 1730 | 0.937 | 53 | 38 |
| Grok 4.6 (xhigh) | 60.0 | 56.6 | 75.9 | .76 | 29.3 | .24 | 1755 | 1.230 | 57 | 45 |
| Grok 4.6 (low) | 51.7 | 47.8 | 66.3 | .79 | 25.9 | .31 | 1552 | 0.255 | 53 | 4.2 |
| Kimi K3 (max) | 59.7 | 54.3 | 76.2 | .83 | 19.7 | .53 | 1668 | 0.837 | 38 | 53 |
| Qwen3.8-Flash-Next (no CLI here) | 55.8 | 56.4 | 73.1 | .77 | -9.7 | .45 | 1743 | 0.097 | 89 | 25 |
| DeepSeek V4 Flash 0731 | 51.8 | 48.4 | 69.1 | .74 | -14.3 | .92 | 1547 | 0.112 | 108 | 20 |

Halluc is AA hallucination rate (lower is better). Omni is AA Omniscience.
TTFA is median time to first answer token in seconds. MMMU-Pro where measured:
Opus 5 high 0.82, Sol xhigh 0.83, Gemini 3.7 Flash 0.85; Fable 5.1 has not
been measured.

## C. Per-lane decisions

All comparisons were retrieved 2026-09-02.

| Lane | Criterion | Previous | New | Deciding figures |
|---|---|---|---|---|
| hardest-coding | Coding capability | Fable 5.1 xhigh → Sol xhigh | Fable 5.1 xhigh → Sol xhigh → Grok 4.6 → Gemini 3.7 Flash (High) | Fable leads Sol on Terminal-Bench 91.0 vs 89.5 and SciCode 60.1 vs 56.0. Anthropic also puts Fable above Opus on Terminal-Bench 4.0 and CursorBench; Sol's coding component costs about one sixth as much. Fallback-depth pass: Grok (Coding 76.8, hallucination .34) and Flash High (Coding 76.1 at .402/task) keep the lane alive when neither subscription is reachable. |
| bulk-mechanical | Endurance per dollar | Terra max → Sonnet high → Gemini 3.6 Flash High | Sol high → Gemini 3.7 Flash High → Sonnet high | Sol beats Terra on Intel 57.3/56.6, Agentic 50.6/50.2, Coding 77.2/76.7, $/task .427/.526, and TTFA 15/104. Flash 3.7 beats 3.6 on Intel 56.0/51.6, Agentic 45.1/40.5, Coding 76.1/69.2, throughput 285/167, and TTFA 7/14; its introductory token price is half. Sonnet high remains because per-effort index data are missing and Opus consumes several times more subscription quota per turn. |
| triage | Cost per task at usable intelligence | Luna medium → Gemini 3.6 Flash Low → Haiku | Luna high → Gemini 3.7 Flash Low → Haiku | Luna high gains 8.1 Intel points (47.0 vs 38.9) for about one extra cent per task (.022 vs .012). Flash Low moves to the current generation; Haiku remains the cheapest Claude row. |
| hard-judgment | Agentic knowledge work per cost | Fable 5.1 xhigh → Sol max → Grok 4.6 | Opus 5 xhigh → Sol max → Grok 4.6 | Opus xhigh returns 97.7% of Fable xhigh's Agentic score (58.4/59.8) at 68% of the cost (1.801/2.651 per task) and a lower hallucination rate (.60/.71). Grok's Agentic 58.7 is near Sol's 57.8 and its .34 hallucination rate is much lower than Sol's .92, but its effective CLI effort is unknown. |
| taste-final | Prose and polish | Fable 5.1 high → Sol max | Fable 5.1 high → Sol max → Grok 4.6 → Gemini 3.7 Flash (High) | At the same effort Fable leads Opus high on Intel 62.5/61.5 and Omni 40.8/33.7 for about 17% more per task (1.430/1.227); Opus retained the lower hallucination rate, .61 vs .69. Fallback-depth pass: Grok matches Sol's Intel (60.9) with far better Omni (30.5 vs 22.0) and hallucination rate (.34 vs .92); Flash High closes out the lane at Intel 56.0 and .402/task. |
| consult | Named-model direct question | Sol max → Opus high → Grok → Gemini 3.1 Pro High | Sol max → Fable 5.1 high → Grok → Gemini 3.7 Flash High | The Claude slot becomes the strongest current Claude. Flash beats 3.1 Pro on Intel 56.0/47.7, Agentic 45.1/23.0, and Coding 76.1/68.8; Pro retains only Omni, 31.9/26.5. |
| ui-draft | Coding and measured multimodality with references | Sol xhigh → Fable 5.1 high | Sol xhigh → Fable 5.1 high → Gemini 3.7 Flash (High) | Fable high beats Opus high, the prior fallback, on Intel 62.5/61.5 and Coding 79.1/76.5. MMMU-Pro is unmeasured for Fable; measured rows are Opus high .82, Sol xhigh .83, and Flash .85. Fallback-depth pass: Flash High holds the highest measured MMMU-Pro (.85) among the shipped candidates and is the cheapest, keeping the lane alive as a third option. |
| long-context | AA-LCR, then secondary axes | Gemini 3.1 Pro High → Sol high → Opus high | Gemini 3.7 Flash Medium → Terra max → Opus medium | Flash edges Pro on LCR .81/.79 inside AA's 10k–100k caveat, with Intel 53.4/47.7, $/task .263/.335, and throughput 282/103. Terra's .80 replaces Sol high's .75. Opus medium matches Fable medium at .79 LCR for .724 vs 1.001 per task. |
| fast-agentic | Interactive multi-step latency | Gemini 3.7 Flash Medium → Luna high | Gemini 3.7 Flash Medium → Luna high → Claude Haiku 4.5 | Luna max has Agentic 46.9 vs Flash 45.1, but TTFA is 101 vs 5.4 seconds and throughput 128 vs 282 tok/s. Luna high is the Codex fallback on TTFA, 11 seconds vs xhigh's 41. Fallback-depth pass: Haiku adds a third, low-latency Claude candidate at TTFA 9.9s and .217/task. |
| live-search | Native live X/web access | Grok 4.6 → off | Grok 4.6 → Gemini 3.7 Flash (High) → Claude Sonnet 5 (high) → off | The lane is still defined by Grok's native live-search surface rather than an AA score. Fallback-depth pass: Flash High and Sonnet are not native X/live-search candidates — they fall back to their own web-search tools, a strictly weaker but non-zero substitute, so a missing Grok CLI no longer strands the lane. |
| coding-overflow | Coding value outside Codex | Grok 4.6 → Kimi → Qwen → OpenCode → off | Grok 4.6 → Gemini 3.7 Flash High → Kimi → Qwen → OpenCode → off | Flash Coding 76.1 is near Grok's 76.8 at .402 vs .937 per task. Grok's .34 hallucination rate is roughly a third of Opus max's .61 and well under half of Fable max's .73. Qwen3.8-Flash-Next has Coding 73.1, Agentic 56.4, and .097/task; re-evaluate when a Qwen CLI makes its alias testable. |
| arbitrate | Explicit multi-model vote | off | off | Unchanged; the quota-multiplying panel remains opt-in. |

**2026-09-03 decisions (Vincent).** Value rule: the published table stays
capability-correct on the AA data above, but where two configurations sit
close on a lane's own criterion, the cheaper one wins — the deciding factor
behind the hard-judgment swap to Opus 5. Fallback-depth rule: every active
lane (`arbitrate` intentionally excluded) now carries at least three vendor
candidates, so a single missing CLI cannot strand it — the reason
hardest-coding, taste-final, ui-draft, fast-agentic, and live-search each
gained new fallbacks in this pass.

## D. First-party cross-checks

Both vendor tables were retrieved 2026-09-02.

### Anthropic Fable 5.1 announcement

| Benchmark | Fable 5.1 | Opus 5 |
|---|---:|---:|
| Terminal-Bench 4.0 | 55.8% | 52.3% |
| GDPval-AA v2 | 1853 | 1824 |
| OSWorld 2.0 strict | 41.7% | 39.6% |
| AutomationBench | 31.4% | 26.9% |
| CursorBench 3.2.0 | 73.4% | 70.0% |
| HLE with tools | 65.0% | 63.6% |

### Google Gemini 3.7 Flash launch post

| Benchmark | Gemini 3.7 Flash | Gemini 3.6 Flash |
|---|---:|---:|
| FrontierCode 1.1 | 43.6% | 34.4% |
| DeepSWE v1.1 | 65.3% | 49.0% |
| AutomationBench | 30.4% | 17.0% |
| GDP.pdf | 34.0% | 22.0% |
| WebDev Arena Elo | 1588 | 1538 |

## E. Runtime gate

Each model newly entering a default lane was dispatched for real on 2026-09-02
and returned `rc=0`.

| Model/config | Runtime evidence |
|---|---|
| `claude-fable-5-1` | `20260902-115824-88685-16859`, `rc=0` |
| `Gemini 3.7 Flash (High)` | `20260902-123741-49851-14429`, `rc=0` |
| `Gemini 3.7 Flash (Medium)` | `20260902-132038-76075-22911`, `rc=0` |
| `gpt-5.6-luna high` | real dispatch, `rc=0` |
