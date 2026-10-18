# GPT-5.4 mini 放回評分表的證據（2026-10-18）

來源：claude-code-s／MacStudio；工作樹 `feat/gpt-5-4-mini-score-row`，基底 `e1a86aa`（release/0.56.0）。

## 為什麼放回

2026-10-02 的快照 `aa-v4.3.2-2026-10-02-v2`（提交 9b1d888）把三列拿掉了，理由是本機 Codex 用 ChatGPT 帳號呼叫時回 HTTP 400。

omnilane 是給別人用的產品，評分表收錄哪些模型，應該看廠商開放什麼，不看某一個帳號能不能呼叫。所以三列放回評分表。但本機證明不了傳輸，所以不進任何車道。依據是 Vincent 2026-10-18 的選項回答：「放回，但不排進車道」。

## 資料來源

- 抽取檔：`docs/reports/aa-v4.3.2-extract-2026-10-14.json`，這次沒有重新抓取。
  - 抽取檔 sha256 `41588b6710e724f0393b1745bdfc1b468590f93d126eaf6fd81b7022485e4817`。
  - `fetched_at` 是 `2026-10-14T14:11:20+08:00`，`benchmark_version` 是 `4.3.2`。
- 三筆記錄都在這份抽取檔裡，所以不需要重新抓取。

| 評分列 | AA slug | AA 名稱 | intelligenceIndex | 是否為估計值 | score_raw / score |
|---|---|---|---:|---|---:|
| `codex/gpt-5-4-mini` | `gpt-5-4-mini` | GPT-5.4 mini (Xhigh) | 24.0682169231964 | 否 | 24.07 / 24 |
| `codex/gpt-5-4-mini-medium` | `gpt-5-4-mini-medium` | GPT-5.4 mini (Medium) | 19.7484210566215 | 是 | 19.75 / 20 |
| `codex/gpt-5-4-mini-non-reasoning` | `gpt-5-4-mini-non-reasoning` | GPT-5.4 mini (Non-reasoning) | 11.1447193424847 | 是 | 11.14 / 11 |

三筆的其他欄位一致：`releaseDate` 2026-03-17、`contextWindowTokens` 400000，價格是每百萬 token 輸入 0.75、輸出 4.5（混合價 0.6525）。分數和 9b1d888 之前的快照相同，都是 24 / 20 / 11。

AA 把三筆都標成 `deprecated: true`。這不是排除條件：現行評分表裡還有 65 列在 AA 上同樣標成 deprecated，例如 `claude/claude-opus-5`、`codex/gpt-6-sol`、`grok/grok-4-6`。

## 缺的欄位

- `gpt-5-4-mini`（Xhigh）：只缺 `omniscienceBreakdown`。
- `gpt-5-4-mini-medium` 缺：`terminalBench21`、`scicode`、`gdpvalNormalized`、`itBenchSre`、`apexAgents`、`intelligenceIndexOutputTokensPerTask`、`terminalBench40`、`automationBenchPartialScore`、`tauBanking`、`mlcrOverall`、`omniscienceBreakdown`、`briefcaseBreakdown`、`intelligenceIndexCost`、`intelligenceIndexTimePerTask`。
- `gpt-5-4-mini-non-reasoning` 缺：`terminalBench21`、`scicode`、`apexAgents`、`intelligenceIndexOutputTokensPerTask`、`terminalBench40`、`automationBenchPartialScore`、`tauBanking`、`mlcrOverall`、`omniscienceBreakdown`、`briefcaseBreakdown`、`intelligenceIndexCost`、`intelligenceIndexTimePerTask`、`effort_label`。

**這次不補這些欄位。** 評分表的分數只用 `intelligenceIndex`，三筆都有。缺的是 `routing.yaml` 排車道用的指標，而三列不進任何車道。以後如果要讓它們進車道，再用 `aa_rebaseline.py fill --earlier` 從較早的抓取補齊，並寫明來源。

## 怎麼放回

- `scripts/aa_rebaseline.py`
  - `RETIRED_ROWS` 改成空的 `{}`，退役機制保留給以後用。
  - 三列加進 `NEW_ROWS`，傳輸形狀分別複製 `codex/gpt-5-4`（xhigh、medium）和 `codex/gpt-5-4-non-reasoning`。
  - `NEW_ALIASES` 加 `"gpt-5.4-mini": "gpt-5.4"`。
  - 選這個做法的原因：現行政策檔裡這三列在 `unknown_configs`，`build` 的評分迴圈只處理基底的 `scored_configs`，所以光把 `RETIRED_ROWS` 清空不會把它們加回來。
  - 放進 `NEW_ROWS` 之後，因為 slug 在抽取檔裡，`build` 會重新計分；同時用 `added` 集合把 `unknown_configs` 裡同身分的三筆拿掉。這和其他新列走同一條路，不需要特殊處理。
- `build --extract docs/reports/aa-v4.3.2-extract-2026-10-14.json --as-of 2026-10-18 --base <2026-10-14 政策檔副本>` 連跑兩次，雜湊都是 `6ac4158930fac4f4b435e837e374fa1434ce9c29e8a122e9be57ff68e1a3e1b5`。
- 快照是 `aa-v4.3.2-2026-10-18-v1`，`approval.status=proposed`。
- 和 2026-10-14 政策檔比對：
  - 評分列從 121 增加到 124，多出來的正好是這三列，沒有移除任何列；
  - 其他列的分數、是否為估計值、相對順序都沒變；
  - `unknown_configs` 從 12 減到 9；
  - 別名從 53 增加到 54。新別名 `gpt-5.4-mini` 的狀態是 `unknown`，`resolved_config_id` 是 null，`runtime_verified` 是 false，和 9b1d888 之前那份完全相同。
- 三列的 `transport_mapping`：`status=unknown`、`candidate_model_ids=["gpt-5.4-mini"]`、`runtime_verified=false`，也和 9b1d888 之前相同。
- `scripts/lib/build_overlay.py` 還原三個探測候選：
  - 帳號回 400 的主機，會把探測記成未證明；
  - 帳號能呼叫的主機，執行 `omnilane resign` 就能證明傳輸。
- `scripts/configure.sh` 選單還原 `gpt-5.4-mini`，`tests/run.sh` 的選單序號從 11 改回 12。

## 車道

`routing.yaml` 沒有改。`python3 scripts/aa_rebaseline.py lanes --extract docs/reports/aa-v4.3.2-extract-2026-10-14.json` 的輸出共 165 行，`gpt-5-4-mini` 出現 0 次；`routing.yaml` 裡 `gpt-5.4-mini`／`gpt-5-4-mini` 也是 0 次。
