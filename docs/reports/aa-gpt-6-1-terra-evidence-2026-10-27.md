# GPT-6.1 Terra 收進評分表的證據（2026-10-27）

來源：claude-code-s／MacStudio；工作樹 `feat/aa-gpt-6-1-terra`，基底 `c4f5b6a`（main）。

## 官方發布

- OpenAI 於 2026-10-26 發布 GPT-6.1 Terra，在 ChatGPT（Work）、Codex 與 API 提供。
- 官方模型頁 <https://developers.openai.com/api/docs/models/gpt-6.1-terra>（2026-10-27 讀取）：
  - 模型 ID `gpt-6.1-terra`，預設快照也是 `gpt-6.1-terra`。
  - `reasoning.effort` 支援 `none`、`low`、`medium`（預設）、`high`、`xhigh`、`max`；工具呼叫需要 Responses API。
  - 價格（每百萬 token）：輸入 $1.00、快取輸入 $0.05、快取寫入 $1.25、輸出 $5.00。
- 官方公告 <https://openai.com/index/introducing-gpt-6-1-terra/>：直接抓取回 HTTP 403。網址取自 9to5Mac 報導內的連結，內文未直接讀到。
- 第三方報導：<https://9to5mac.com/2026/10/26/openai-upgrades-chatgpt-and-codex-with-new-gpt-6-1-terra-model/>。
- 本機 Codex CLI 0.164.0 的 `~/.codex/models_cache.json` 有 `gpt-6.1-terra`，推理強度列出 low、medium、high、xhigh、max、ultra，沒有 `none`。

## AA 資料

- AA 文章 <https://artificialanalysis.ai/articles/gpt-6-1-terra-pushes-the-cost-efficiency-frontier>（2026-10-26）。
- 抽取檔：`docs/reports/aa-v4.3.2-extract-2026-10-27.json`，用 `aa_rebaseline.py fetch --slug gemini-4-argon` 抓取。
  - sha256 `7562dc0103b9d8d5aa6d6217af8fb4a953387c373331d704095802b20e9d3697`。
  - `fetched_at` `2026-10-27T07:08:28+08:00`，頁面 sha256 `5bb3868b9806b43689f67adf86e1a37156baac6e90a046c942452087a3b40ec7`，頁面 715 筆、保留 237 筆廠商紀錄。
- 為什麼不用 Terra 本頁：`fetch --slug gpt-6-1-terra` 只帶 17 筆廠商紀錄，`build` 會把抽取檔裡沒有的 108 個現行列退到 unknown。Argon 頁涵蓋現行政策檔全部 125 個 `aa_slug`，做法同 2026-10-14。
- 核對：六個 Terra 本頁（`gpt-6-1-terra`、`-xhigh`、`-high`、`-medium`、`-low`、`-non-reasoning`）各自抓一次，與 Argon 頁的同一筆逐欄相同，只有 max 列的 `name` 不同（本頁 `GPT-6.1 Terra`，Argon 頁 `GPT-6.1 Terra (Max)`）。

| 評分列 | AA slug | AA 名稱 | intelligenceIndex | 估計值 | score_raw / score | 跑完整個指數的成本 ($) |
|---|---|---|---:|---|---:|---:|
| `codex/gpt-6-1-terra` | `gpt-6-1-terra` | GPT-6.1 Terra (Max) | 48.1061836463044 | 否 | 48.11 / 48 | 909 |
| `codex/gpt-6-1-terra-xhigh` | `gpt-6-1-terra-xhigh` | GPT-6.1 Terra (Xhigh) | 45.3640518262171 | 否 | 45.36 / 45 | 365 |
| `codex/gpt-6-1-terra-high` | `gpt-6-1-terra-high` | GPT-6.1 Terra (High) | 43.8688591051329 | 否 | 43.87 / 44 | 239 |
| `codex/gpt-6-1-terra-medium` | `gpt-6-1-terra-medium` | GPT-6.1 Terra (Medium) | 41.318827405477 | 否 | 41.32 / 41 | 175 |
| `codex/gpt-6-1-terra-low` | `gpt-6-1-terra-low` | GPT-6.1 Terra (Low) | 36.2133370409149 | 否 | 36.21 / 36 | 115 |
| `codex/gpt-6-1-terra-non-reasoning` | `gpt-6-1-terra-non-reasoning` | GPT-6.1 Terra (Non-reasoning) | 33.4885115837089 | 否 | 33.49 / 33 | 154 |

六筆共同欄位：`releaseDate` 2026-10-26、`contextWindowTokens` 1000000、`deprecated` false、價格每百萬 token 輸入 1、輸出 5。

缺的欄位（六筆相同）：`apexAgents`、`gdpvalNormalized`、`gpqa`、`ifbench`、`itBenchSre`、`mlcrOverall`、`omniscienceBreakdown`、`tau2`、`tauBanking`、`terminalBench21`、`terminalbenchHard`。沒有從其他抓取補值。`mlcrOverall` 缺，所以 Terra 不進 long-context 的量測；`omniscienceBreakdown` 缺，所以沒有幻覺率。

## 政策檔

- `NEW_ROWS` 加六列，形狀各自複製 GPT-5.6 Terra 同強度的列（`codex/gpt-5-6-terra{,-xhigh,-high,-medium,-low,-non-reasoning}`）。GPT-6.1 Sol 沒有 non-reasoning 列，Terra 有，因為 AA 有評分、官方 API 也接受 `none`。
- `NEW_ALIASES` 加 `gpt-6.1-terra`（複製 `gpt-5.6-terra` 的別名）；`VALUE_FAMILIES` 加 `gpt-6.1-terra`。
- `build --base <10-18 政策檔> --as-of 2026-10-27` 連跑兩次，雜湊都是 `0fa2df6e95445069b1fa46474bc43460d018dac8587c503035c0734c37ad285f`。
- 結果：計分 124 → 130（Codex 57 → 63），估計值 26 不變，未知 9 不變，沒有列退場。既有列的 `score`、`score_raw` 與排序全部不變。
- `approval.status` 是 `proposed`。

## 車道

`aa_rebaseline.py value` 的價值選擇把 Terra 放進四條車道。`routing.yaml` 只在這些位置插入 Terra，其他梯級與每條車道的首選不變：

| 車道 | 插入 | 位置 |
|---|---|---|
| hardest-coding | xhigh、high、medium | GPT-6.1 Sol medium 之後 |
| bulk-mechanical | high、medium | GPT-6.1 Sol high 之後 |
| ui-draft | high；low | GPT-6.1 Sol high 之後；GPT-6 Astra low 之後 |
| fast-agentic | medium、low | GPT-6.1 Sol medium 之後 |

hard-judgment、taste-final、long-context 的價值選擇沒有 Terra。triage、consult、live-search、coding-overflow 不是價值車道，不動。max 與 non-reasoning 列不進任何車道。

## 沒有處理、待決定的事

這次抓取裡 AA 重測了既有列的多個欄位（`mlcrOverall` 116 筆、`gdpvalNormalized` 124 筆、`timeToFirstAnswerToken` 167 筆等）。例如 Claude Opus 5 high 的 `mlcrOverall` 從 0.594 變成 0.117。用 10-18 政策檔搭這次抓取跑 `value`，long-context 的選擇會從 Claude Opus 5 換成 GPT-6 Astra medium、GPT-6.1 Sol medium 與 GPT-6 Sol 各列；其他車道的選擇沒有因為重測而改變。long-context 換人是另一個路由決定，這次沒有改，留給主控與 Vincent。

## 傳輸

- `build_overlay.py` 的 `PROVEN` 加入 `codex/gpt-6-1-terra*` 六列，證據檔名 `cx-avail-gpt-6_1-terra`（high）、`cx-gpt-6_1-terra-{max,xhigh,medium,low,none}`。
- `config/transport-compatibility.json` 登錄 `aa-v4.3.2-2026-10-27-v1`；舊快照條目一字未改，新條目裡既有列的 row 與 selector 合約與 10-18 相同。
- 本機 Codex 快取沒有 `none`，所以 non-reasoning 的探測可能記成 unproven；這不是錯誤，其他列不受影響。
- 快照代號改變，合併後每台主機都要執行 `omnilane resign`。
