# AA v5.0.1 重拍快照（2026-11-10）：收進 Claude Fable 5.2，Gemini 4 Carbon 只當參考列

來源：claude-code-s／MacStudio；工作樹 `feat/aa-fable-5-2`，基底 `ccaf389`。

狀態：**proposed**（`approval.status = "proposed"`、`approval.scope = "aa-v5.0.1-rebaseline"`），等 Vincent 核准。核准前這份快照不具權威；主機尚未重簽。

快照：`aa-v5.0.1-2026-11-10-v1`，AA Intelligence Index 5.0.1，`as_of` 2026-11-10（Asia/Taipei）。
政策檔 SHA-256：`af518566e61a67685789d55bf09478818f7fa55f18e31fde8917ed376f2ae974`。
抽取檔：`docs/reports/aa-v5.0.1-extract-2026-11-10.json`，SHA-256 `18d62899f41e5cc096b04fef765180e9243b123adeae2885f85a94cf08cf11e9`（等於政策檔 `snapshot.source.extract_sha256`）。

## 來源頁與抓取方式

- 5.0.1 的每一頁只帶部分模型的紀錄（每頁 205～734 筆），不像 4.3.2 單一頁就有幾乎全部模型。所以比照 2026-10-08 的先例逐頁抓、再合併。
- 共 85 頁，原始抽取都放在 `docs/reports/aa-pages-2026-11-10/`：主控先抓 11 頁，另外對現行政策表缺分數的 74 個 slug 各抓自己的模型頁，74 頁全部成功。
- 抓取用 `scripts/aa_rebaseline.py fetch`；`intelligenceIndex` 為 null 的紀錄不收。每頁的網址、抓取時間、頁面 SHA-256、頁上紀錄數與廠商紀錄數都記在政策檔 `snapshot.source.pages`。
- 補抓 74 頁後，有分數的廠商 slug 聯集仍是 66 個：這 74 個 slug 在自己的頁上 `intelligenceIndex` 都是 null。

## 合併規則

- 用 `scripts/aa_rebaseline.py merge`，規則 `own-page-then-latest-populated-v1`：每個欄位優先取非空值；非空值中優先取該模型自己的頁，再取最晚抓取的頁；仍平手時依來源網址與頁面 SHA-256 排序。全部為空的欄位維持空值。
- 結果：85 頁、66 筆廠商紀錄、79 個欄位衝突。每個欄位取自哪一頁記在抽取檔 `record_sources.<slug>.fields`，所有不一致的觀測記在 `conflicts`。
- `intelligenceIndex` 與 `intelligenceIndexIsEstimated` 沒有任何衝突；衝突集中在 `name`（例如「Claude Fable 5.2」對「Claude Fable 5.2 (Max, Default Fallback)」）與各項基準測試欄位。

## 新舊計數

以 `git show HEAD:config/aa-model-policy.json`（4.3.2，`aa-v4.3.2-2026-10-28-v1`）與現行政策檔逐 id 比對：

| 項目 | 4.3.2（2026-10-28） | 5.0.1（本快照） |
|---|---|---|
| 有分數（`scored_configs`） | 136 | 68 |
| 沒分數（`unknown_configs`） | 9 | 82 |
| 參考列（`reference_configs`） | 1 | 4 |
| 別名（`aliases`） | 55 | 56 |
| 估算分數（`estimated`） | — | 0 |

- 136 列中 63 列在 5.0.1 仍有分數，改用 5.0.1 分數留在 scored；加上 Claude Fable 5.2 五列＝68。
- 73 列在 5.0.1 沒有分數，全部移到 `unknown_configs`：9＋73＝82。被直接丟掉的列：0。
- 參考列 1→4：原有 `gemini-3-1-pro-preview`，加上 Gemini 4 Carbon 三列。
- 依廠商：claude 27、codex 26、gemini 11、grok 4（政策檔 `coverage.by_vendor`）。

主要分數（5.0.1，四捨五入後）：

| 模型 | max | xhigh | high | medium | low |
|---|---|---|---|---|---|
| Claude Fable 5.2 | 65 | 62 | 62 | 56 | 54 |
| Claude Opus 5.5 | 59 | 57 | 52 | 48 | 35 |
| Claude Sonnet 5.5 | 55 | 50 | 42 | 31 | 26 |
| Claude Fable 5.1 | 49 | 50 | 47 | 43 | 39 |
| Claude Haiku 5.5 | 36 | 35 | 30 | 25 | 19 |
| GPT-6 Astra | 57 | 55 | 55 | 52 | 43 |
| GPT-6.1 Sol | 55 | 52 | 50 | 46 | 35 |
| GPT-6.1 Terra | 51 | 45 | 42 | 38 | 29 |
| GPT-6 Luna | 28 | 23 | 21 | 18 | 10 |

只剩 max 有分數的：Claude Opus 5（44）、GPT-6 Sol（44）、GPT-5.6 Terra（32）、GPT-5.6 Luna（24）。Gemini 4 Argon high 50；Gemini 3.8 Flash high 31、medium 30、low 23；Grok 4.7 xhigh 35、high 35、low 31；Grok 4.6 xhigh 31。

## 沒分數列的處理

- 4.3.2 有分數、5.0.1 沒分數的 73 列一律移到 `unknown_configs`（`score: null`），**不帶 4.3.2 舊分數、不估算**。
- 依 AA 頁面原始資料，這些 slug 都還列在頁上：其中 68 個標 `deprecated: true`，其餘是 `deprecated: false` 但尚未有分數（如 `gpt-5-3-codex`、`gpt-oss-20b`）。
- 沒有跑 `fill`：它不檢查基準版本，會把 4.3.2 的基準測試數字填進 5.0.1 紀錄。
- `build_overlay.PROVEN` 照 2026-10-02 提交 9cdf7ae 的先例，明列拿掉不再有分數的列（104→64；含之後 Carbon 改為參考列時拿掉的 3 列）。
- 車道上失去分數的目標已從 `routing.yaml` 拿掉，車道依新分數重排（見下節）。

## 缺的欄位從哪裡補

- 沒有用任何非 5.0.1 的來源補欄位。合併時同一模型的欄位可以取自別的 5.0.1 頁（規則見上），來源逐欄記在 `record_sources`。
- 5.0.1 沒公布的欄位（例如首字延遲）維持空值，`lanes` 輸出寫 `not published`。
- 新增列的形狀（`reasoning`、`fallback`、`transport_mapping` 等非分數欄位）：`build` 先用 `NEW_ROWS` 指定的形狀列；該列在基底已沒分數時，改用同廠商仍有分數的列（`shape_of`）。分數相關欄位全部由抽取檔覆寫。Fable 5.2 各列依 AA 標示寫 `fallback: "default"`（`NEW_ROW_FALLBACKS`）。
- Claude Fable 5.2 的 CLI 代號 `claude-fable-5-2` 與五個強度，依 https://code.claude.com/docs/en/model-config 查證；Claude Code 2.1.321 以上才認得，`fable` 別名從 2.1.321 起才解析成 5.2。

## Gemini 4 Carbon

- AA 有分數：60（high）、58（medium）、54（low），三列放在 `reference_configs`，不在 `scored_configs`。
- 理由：Google 尚未公布 Gemini 4 Carbon 的模型代號或強度選擇器，agy 1.5.3 的模型清單也沒有它（2026-11-10 查證）。沒有選擇器就無法探測、無法派工；放進 scored 會出現一個永遠證明不了的列。
- 因此沒有別名、沒有探測項目、不在任何車道，也不在 `PROVEN`。等官方公布選擇器後再另案收進。
- `gemini-3-1-pro-preview` 同樣是參考列，但它在 5.0.1 沒分數，仍帶 `benchmark_version: "4.3.2"`、score 30，只供比較。

## 車道重排

依新分數價值優先重排，方法沿用 `VALUE_LANES`／`value_pool`／`MEASURES`，鏈上不放 max。三條車道的第一候選改變：

| 車道 | 4.3.2 | 5.0.1 | 原因 |
|---|---|---|---|
| hardest-coding | codex gpt-6-astra xhigh | claude claude-fable-5-2 xhigh | Fable 5.2 xhigh 62，高於所有可派的列 |
| hard-judgment | claude claude-opus-5-5 xhigh | claude claude-fable-5-2 xhigh | 同上；後面接 Fable 5.2 medium、low |
| long-context | claude claude-opus-5 high | codex gpt-6-astra medium | Opus 5 high 已沒分數；Astra medium 的 mlcrOverall 最高（0.383） |

- hardest-coding 的 Gemini 4 Argon high 移到 Gemini 尾段。
- `vote` 的 Claude 投票者從 Fable 5.1 xhigh（50）改為 Fable 5.2 low（54），`scripts/dispatch.sh` 與 `scripts/runners/run-vote.sh` 兩處。上限 50～52 的主控原本派得到舊投票者、派不到新的；上限低於 50 的兩者都派不到。
- 逐條車道的候選與分數：`docs/reports/aa-v5.0.1-lanes-2026-11-10.txt`。

## 已知限制

- **fast-agentic 沒有重排**：這條車道以「首字延遲 ≤10 秒」為門檻，但 5.0.1 沒有公布首字延遲，`value` 會把整條車道算成空的。所以只拿掉沒分數的列、維持原順序，門檻怎麼改另待決定。
- 73 列移到 unknown 後，這些模型只能等 AA 重新計分才會回到評分表；目前不估算。
- `aa_policy.py` 的 `APPROVED_*` 釘值已指向本快照（版本 5.0.1、政策檔雜湊 `af518566…`），但 `approval.status` 仍是 proposed；核准與否以 Vincent 的決定為準。
- 核准後每台主機都要跑 `omnilane resign`，overlay 才會對新評分表重新證明；Claude Fable 5.2 的探測需要 Claude Code 2.1.321 以上。
