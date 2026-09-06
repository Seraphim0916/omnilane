# Model capabilities — September 2026 snapshot

External records are dated per section. The 2026-09-05 decision below is the
current basis for `routing.yaml`; the earlier 2026-09-02 v4.1.1-era snapshot is
kept as historical evidence rather than relabelled as v4.2.

## Current routing decision — 2026-09-05

### Comparable evidence and limits

- **AA Intelligence Index v4.2, same-condition comparison:** Claude Fable 5.1
  scored 57 at max and 54 at xhigh; GPT-6 Astra scored 55 at max and 54 at
  xhigh. Retrieved 2026-09-05 from the AA Fable/Astra comparison.
- **AA Briefcase:** Fable/Astra scored 1666/1566 at max and 1657/1540 at
  xhigh. Retrieved 2026-09-05.
- **Native coding-agent comparison:** Fable max completed 70 at $9.18/task in
  24 minutes; Astra max completed 67 at $4.72/task in 26.8 minutes. Sol high
  completed 64 at $3/task in 6.2 minutes; Luna high completed 52 at $0.18/task
  in 5.7 minutes. Retrieved 2026-09-05.
- **Flash native-agent rows:** Antigravity SDK Gemini 3.8 Flash medium
 completed 59.09 at $2.009/task in 7.63 minutes; OpenCode Gemini 3.8
 Flash high completed 61.15 at $2.038/task in 11.85 minutes. They are
 different harness/model-effort rows, so neither is evidence that one
 generation or effort is categorically faster. The separate 2026-09-02
 Flash article uses AA v4.1.1; its per-task time/cost can guide an effort
 choice, but its aggregate score is not compared directly with v4.2 totals.
 Flash 3.8 high also costs about 40% more per task than 3.7 high in that
 article, so the refresh does not claim every 3.8 configuration is cheaper.
- **Capability boundaries:** UI taste has no matching benchmark here; one
  million tokens of context does not establish long-context task quality;
  Grok Build comparison data still names 4.5 high and is not represented as a
  Grok 4.6 runtime result; Qwen scores from another harness do not establish
  that this repository's Qwen CLI alias is equivalent.

Sources: [Fable 5.1 vs Astra v4.2](https://artificialanalysis.ai/models/releases/comparisons/gpt-6-astra-vs-claude-fable-5-1),
[AA Briefcase](https://artificialanalysis.ai/evaluations/aa-briefcase),
[native Claude Code vs Codex](https://artificialanalysis.ai/agents/coding-agents/comparisons/claude-code-vs-codex),
[native Antigravity vs Claude Code](https://artificialanalysis.ai/agents/coding-agents/comparisons/antigravity-sdk-vs-claude-code), and
[Gemini 3.8 Flash article](https://artificialanalysis.ai/articles/gemini-3-8-flash).

### Full-roster v4.2 check — 2026-09-05

The public leaderboard was extracted as 643 unique model/configuration rows and
identified itself as AA Intelligence Index v4.2. The machine-readable inventory
is [`aa-model-coverage-2026-09-05.json`](aa-model-coverage-2026-09-05.json).
An AA slug is evidence identity, not a CLI/API model ID.

| Current/relevant row | Intel | Agentic | Coding | AA-LCR v1.1 | Accuracy | Hallucination rate | 7:2:1 $/M tokens | tok/s | TTFA s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Claude Opus 5 high | 52.03 | 52.91 | 76.52 | .79 | 58.9% | 61.2% | $3.85 | 53.4 | 19.5 |
| Claude Opus 5 xhigh | 53.37 | 55.67 | 77.00 | .8033 | 59.5% | 59.5% | $3.85 | 54.5 | 33.8 |
| Claude Sonnet 5 high | n/a | n/a | n/a | n/a | n/a | n/a | $1.54 | 69.4 | 11.0 |
| Claude Sonnet 5 max | 45.11 | 44.53 | 71.55 | .82 | 40.1% | 39.4% | $1.54 | 75.0 | 203.7 |
| Claude Haiku 4.5 reasoning | 22.46 | 10.43 | 43.89 | .74 | 18.0% | 27.3% | $0.77 | 94.2 | 20.2 |
| Claude Haiku 4.5 non-reasoning | 17.36 | n/a | n/a | .50 | 14.4% | 25.7% | $0.77 | 82.8 | 0.8 |
| GPT-5.6 Sol high | 48.30 | 44.95 | 77.16 | .82 | 58.4% | 91.2% | $3.08 | 71.9 | 13.8 |
| GPT-5.6 Terra max | 46.77 | 43.94 | 76.66 | .83 | 46.8% | 87.9% | $1.74 | 111.4 | 168.8 |
| GPT-5.6 Luna high | 37.36 | 35.85 | 63.34 | .80 | 41.8% | 92.4% | $0.174 | 123.1 | 19.7 |
| GPT-5.6 Luna medium | 30.19 | 25.49 | 50.73 | .75 | 40.7% | 90.9% | $0.174 | 107.1 | 3.0 |

`lcr` is AA-LCR v1.1; it is not `mlcrOverall`. AA's hallucination rate is
`incorrect / (incorrect + partial + not attempted)`, not a general error rate,
so accuracy is shown beside it. Null leaderboard cells remain `n/a`.

This current snapshot changes the role explanation, not the 12 product chains:

- **Fable 5.1 max** remains the quality-first controller recommendation.
- **Opus 5 high/xhigh** is the balanced controller and independent-review
  option. Opus high slightly exceeds Fable high on current Intel (52.03 vs
  51.67) and has lower AA hallucination rate (61.2% vs 68.8%). A separate
  native-agent result put Opus xhigh at 68.15 and Astra max at 66.97, but that
  is a different harness and is not used as a same-condition ordering claim.
- **Astra** remains the existing-Codex-quota controller backup, independent
  reviewer, and coding-oriented hard-work choice.
- **Sol high** stays on bulk/UI: native-agent 64.12 / $3 / 6.17m versus Sol
  xhigh 63.34 / $3.74 / 7.34m. Sol xhigh still has an explicit difficult-repo
  role because its DeepSWE result (66.96%) exceeds high (64.90%); high does not
  dominate every subtest.
- **Terra max** remains the long-context fallback (LCR .83 versus Flash 3.8
  Medium .84), not a controller promotion.
- **Luna high** remains triage primary / fast-agentic fallback. Luna medium is
  an explicit low-cost-family first-pass option with a quality gate, not a new
  default.
- **Sonnet high** remains a bulk/search fallback. Sonnet max's current score,
  low hallucination rate and high latency do not prove the unscored high row.
- **Haiku 4.5** remains simple triage/fast fallback. Its lower hallucination
  rate must be read with its low accuracy and absent native-agent comparison.

No current measurement supports saying Gemini 3.8 Flash is categorically
faster than 3.7. Low/medium/high encode intended operating points; the route
uses them without a cross-generation speed claim.

### Explicit candidates outside the default chains

The public leaderboard also contains GLM-5.3 max (Intel 48.58), DeepSeek V4 Pro
0813 max (42.11), DeepSeek V4 Flash 0731 max (40.84), and Mistral Medium 3.5
(22.66). Their official IDs are exposed for **explicit advise-only** use where
the repository has that API adapter; they are not work/default candidates.
GLM-5.3 is reasoning-only (`low`/`high`/`max`, default `max`), and equality to
AA max remains unverified until the adapter is shown to propagate thinking and
effort. Muse Spark 1.3 and GLM-5.3-Flash remain `runtime-gap`: leaderboard
scores do not establish a local executable ID.

Kimi K3 has AA-LCR v1.1 of 88.67%, so it is documented as a named long-context
candidate. Local Kimi CLI prompt/context transport limits have not been verified,
therefore it does not replace the current long-context default or fallbacks.

### Product defaults: old → new

| Lane | Previous first/chain | 2026-09-05 first/chain | Decision boundary |
|---|---|---|---|
| hardest-coding | Fable 5.1 xhigh → Sol xhigh → Grok 4.6 → Flash 3.7 high | Fable 5.1 max → Astra max → Grok 4.6 → Flash 3.8 high | Correctness-first same-condition and native coding evidence |
| bulk-mechanical | Sol high → Flash 3.7 high → Sonnet 5 high | Sol high → Flash 3.8 high → Sonnet 5 high | Keep proven migration/endurance first choice; refresh Flash fallback |
| triage | Luna high → Flash 3.7 low → Haiku 4.5 | Luna high → Flash 3.8 low → Haiku 4.5 | Keep cheap first-pass route; refresh Flash fallback |
| hard-judgment | Opus 5 xhigh → Sol max → Grok 4.6 | Fable 5.1 xhigh → Astra max → Grok 4.6 | Strongest current judgment rows; this is not a controller selector |
| taste-final | Fable 5.1 high → Sol max → Grok 4.6 → Flash 3.7 high | Fable 5.1 xhigh → Astra xhigh → Grok 4.6 → Flash 3.8 high | General-quality ordering with an explicit no-aesthetic-benchmark caveat |
| consult | Sol max → Fable 5.1 high → Grok 4.6 → Flash 3.7 high | Astra xhigh → Fable 5.1 xhigh → Grok 4.6 → Flash 3.8 medium | Four-vendor explicit consultation; `--vendor` still pins family |
| ui-draft | Sol xhigh → Fable 5.1 high → Flash 3.7 high | Sol high → Fable 5.1 xhigh → Flash 3.8 high | Native high is faster/cheaper than xhigh here; references remain required |
| long-context | Flash 3.7 medium → Terra max → Opus 5 medium | Flash 3.8 medium → Terra max → Opus 5 medium | No same-task reason to replace Terra/Opus fallbacks |
| fast-agentic | Flash 3.7 medium → Luna high → Haiku 4.5 | Flash 3.8 low → Luna high → Haiku 4.5 | Low is the intended latency operating point; no cross-generation speed claim |
| live-search | Grok 4.6 → Flash 3.7 high → Sonnet 5 high → off | Grok 4.6 → Flash 3.8 high → Sonnet 5 high → off | Preserve native X first; backups are generic web search |
| coding-overflow | Grok 4.6 → Flash 3.7 high → Kimi K3 → Qwen3 Coder Plus → OpenCode → off | Grok 4.6 → Flash 3.8 high → Kimi K3 → Qwen3 Coder Plus → OpenCode → off | Explicit quota relief; preserve supported non-Codex vendors and aliases |
| arbitrate | off | off | Opt-in only; each voter and round consumes quota |

`voter_spec` is a separate model table: Astra xhigh, Fable 5.1 xhigh,
Flash 3.8 medium, and Grok 4.6 back the same four voters. It does not add
voters or rounds. Fable max is the quality-first prompt-level controller,
Opus high/xhigh is the balanced controller/independent-review option, and Astra
is the existing-Codex-quota backup/reviewer; controller remains a role, not a
lane or automatic selection table.

### Runtime-gate status

- Codex CLI 0.153.4 app-server `model/list` returned `gpt-6-astra` as visible,
  default effort medium, with low/medium/high/xhigh/max/ultra available; stdin
  remained open through the response and EOF exited 0. A subscription-CLI
  generation with `gpt-6-astra` max returned the exact nonce and exited 0.
- Claude Fable 5.1 already existed in the local catalog and had prior real
  dispatch evidence listed in the historical runtime table below.
- Antigravity CLI 1.1.27 advertises the exact IDs
  `gemini-3.8-flash-high`, `gemini-3.8-flash-medium`, and
  `gemini-3.8-flash-low`. A high-ID generation reported
  `init.model=gemini-3.8-flash-high` and returned the exact nonce. The command
  resolved to resident `session_mode=live`; it was manually terminated after
  the result instead of being closed through its mailbox, so `exit=143` is not
  treated as a provider or model failure and is not a lifecycle acceptance
  result. Medium/low rely on model-list plus offline exact-ID routing tests.

## Historical 2026-09-02 evidence

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


## F. Mode runtime gate (2026-09-06)

Model selection and a successful provider response do not establish mode-policy
correctness. The table records dated runtime evidence and known platform or
coverage limits. Final source-freeze and release-test results belong to the
release evidence report, not to a prospective runtime claim.

| Vendor | Verified scope | Known limitations / evidence boundary |
|---|---|---|
| Codex | Three-mode real checks; same local endpoint network negative control; live close leaves zero tracked descendants and preserves an unrelated process | These tested native/session controls do not establish every tool or workload |
| Claude | Three-mode real checks using explicitly approved Opus; two-turn live close and latest-result recovery; work-local temporary directory | Fable quota-limited attempt is not reclassified as a pass; the actual model-specific acceptance used Opus |
| Agy 1.1.27 | Native isolated settings; prior advise actual search and sysops checks. Product work new/resume passed read/write, partial edit, synchronous wait, C build/run, error propagation and denied outside/policy writes. Earlier native controls denied shell networking and explicit unsandboxed execution; `/tmp` workspace inside/outside controls passed. A separate real two-turn work live/FIFO check passed readback, outside-write denial and normal close | External temp/cache reads are restricted; xcrun default-cache denial warning remains despite successful C build. Native settings bytes change and omitted defaults are not proven equivalent; each start rewrites explicit policy. Complete effective SBPL remains unverified; two tested live turns do not establish arbitrary long-running or asynchronous workloads |
| Grok 1.0.13 | Complete single-shot `plain` advise with the full tool set: real backend keyword search, an HTTP 200 official page, and a native Bash-denied write with the outside canary absent. No auxiliary-model or compatibility-hook override. Prior sysops one-shot/live checks remain separate | macOS work is still gated because child-network isolation is Linux-only. Restricted advise/work live remains unavailable. The new advise acceptance does not revalidate sysops/live or establish Linux work network isolation |

`init.agent` echoes a selector even when native selection falls back. Agy
`init.tools` lists the global registry rather than the selected agent's effective
tool set. Neither field alone proves custom policy loading. Native fallback/error
controls and real positive/negative tool checks are required.

The contract keeps model/provider networking distinct from agent-tool networking.
A local process exit of zero, unchanged files, or absence of a tool call is not a
substitute for a successful positive control and a policy-denied negative control.

An earlier selected-MD Agy attempt returned a generic pre-tool error with no tool
events; that historical failure is superseded for the following bounded work
scope, not rewritten as a successful run. The accepted product runner new/resume
checks used `view_file`, `write_to_file`, `run_command`, and `finish` with native
`commandExecutionPolicy: sandbox`, `--sandbox`, and `proceed-in-sandbox`. See the
official [subagent policy](https://www.agy.dev/docs/subagents) and
[terminal sandbox](https://www.agy.dev/docs/cli/sandbox/) interfaces.

The real checks preserved surrounding text during a partial edit, waited eight
seconds synchronously, compiled and ran a C program with exit zero, and retained
an expected failing command's exit seven. Outside, symlink, `.agents`, private-app
writes and policy-link removal returned errno 1; original probe hashes stayed
unchanged. Earlier native controls also denied eight external temp/cache write
canaries, shell networking (zero local-listener connections), and an explicit
unsandboxed command. A `/tmp` workspace allowed inside writes while denying an
outside sibling and `.agents` writes. These are tested boundaries, not an
exhaustive inventory of native writable roots.

Native settings omitted `allowNonWorkspaceAccess: false` and `ask: []` after a
run; their default-equivalence is unverified, so each start/resume explicitly
regenerates the policy. The tested permission rules and sandbox-enabled fields
remained identical, but settings are not claimed byte-immutable. External cache
reads are also denied; dependency caches outside the workspace may be unusable.
The successful C build still logged an xcrun default-cache denial. Workspace-local
caches and a verified empty owned policy directory remain intentionally; cleanup
checks ownership and preserves replacement directories. The 24 mode tests and
four ownership/race tests passed. Complete effective SBPL was not captured.
A subsequent single formal work live/FIFO attempt sent two messages: both
returned SUCCESS, round two read back round one, and the outside write was denied
(errno 1, canary absent). It closed normally with exit zero in 19.42 seconds;
owned links/markers and active state were removed, the empty owned directory was
retained, and product sources were unchanged. This is two-turn acceptance, not
a claim about arbitrary long-running or asynchronous workloads.
The first product fixture had a Python quoting error; the accepted evidence is
the corrected second attempt in the same workspace followed by actual resume.

The final Grok advise acceptance used the real runner and original `plain` CLI
output, not the earlier diagnostic wrappers. Native replay evidence recorded a
completed backend `WebSearch` with `action.type=search`, its query and ten source
URLs; a completed `WebFetch` read the official [Settings page](https://docs.x.ai/build/settings)
(HTTP 200, heading `Settings`); the write attempt failed with the native Bash deny
rule. A cross-host redirect was recorded separately before the successful fetch.
The run took 55.822 seconds and the runner source hash was unchanged before/after.

Two independent mechanisms were repaired: hosted search requires the internal
`web_search` selector rather than the client alias `WebSearch`, and context-mode
must not infer this MCP-denied job's readiness from another session's MCP marker.
The private readiness scope preserves hooks: a real hook control still denied an
explicitly prohibited Bash command while removing only the unavailable-MCP
redirect. Existing nonempty caller readiness overrides fail with a conflict before model
startup instead of being replaced. Five Grok-specific tests cover scope creation,
native policy retention, override conflicts, non-advise preservation and cleanup.

The [official hosted-tool gate](https://github.com/xai-org/grok-build/blob/72a61251fcffb464bcc687aeb5a998e5a98ec0c9/crates/codegen/xai-grok-agent/src/config.rs#L1348-L1356)
explains the strict selector match. That public-source revision is not asserted
to be the installed binary's identical build; the recorded real tool controls,
not source inspection alone, establish this dated acceptance.
