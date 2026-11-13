# AA v5.0.1 快照（2026-11-13）：Gemini 4 Hydrogen 進評分表與車道

來源：claude-code-s／MacStudio 工人（omnilane 原生子代理，claude-opus-5-5）；工作樹 `feat/aa-gemini-hydrogen`，基底 `5a584b8`；任務書 `~/.ssot/root/task-records/2026-11-13-aa-hydrogen/TASK.md`（MacStudio 主控端派）。

狀態：**approved**（2026-11-13）。Vincent 2026-11-13 原話「Gemini 4 Hydrogen 釋出 更新AA表 並放出新版」，主控判定為核准新快照，並在驗收後指示以 `--approval approved` 重產。主機尚未重簽。

- 快照：`aa-v5.0.1-2026-11-13-v1`。
- SHA-256：`a95b811703c654115014122e8651578b70915d26736cfa754c3e0b2908bd8076`。
  - 指令：`build --extract docs/reports/aa-v5.0.1-extract-2026-11-13.json --as-of 2026-11-13 --base <2026-11-12 核准政策檔> --approval approved`（基底是 `git show 5a584b8:config/aa-model-policy.json`，`753709bb…16f5a25`）。
  - 連跑兩次，位元組相同。
  - 和先前 proposed 版（`cf20a554…b3b8b2`）只差 `snapshot.approval` 的 `status` 與 `estimated_scores` 兩個欄位。
  - 已重釘五處：`APPROVED_REGISTRY_SHA256`、`compat-entry --write`、`transport_compat.APPROVED_ARCHIVE_SHA256`，以及 `tests/test_origin_integration.py` 的兩個雜湊。
- 傳輸相容性封存檔：`config/transport-compatibility.json` 新增 `aa-v5.0.1-2026-11-13-v1` 一項，舊的各項不變。
  - 封存檔 SHA-256：`85246fd69e90564b18f25b3e8aa29bdd71b1aa79726e2352d519eeef4103fe77`。
  - `scripts/lib/transport_compat.py` 的 SHA-256：`c278e93b7fc44872b140d079a6cf0a4232dea6acb3165278760586fd05253080`。

## 抽取檔

AA 上 Hydrogen 有四頁，主控 2026-11-13 抓下：

| 頁面 | 檔案 | fetched_at | page_sha256 |
|---|---|---|---|
| gemini-4-hydrogen（xhigh） | `docs/reports/aa-v5.0.1-hydrogen-extract-2026-11-13.json` | 08:06:05 | `dd59d10f…a2efa` |
| gemini-4-hydrogen-high | `docs/reports/aa-v5.0.1-hydrogen-high-extract-2026-11-13.json` | 08:11:25 | `75375798…06a91` |
| gemini-4-hydrogen-medium | `docs/reports/aa-v5.0.1-hydrogen-medium-extract-2026-11-13.json` | 08:11:25 | `5ae2db5d…03fb4` |
| gemini-4-hydrogen-low | `docs/reports/aa-v5.0.1-hydrogen-low-extract-2026-11-13.json` | 08:11:25 | `3e3b0371…f578f` |

這幾頁各自只帶十幾個廠商記錄，`build` 會檢查每一條既有計分列都能在抽取檔找到；直接拿單頁去 build，會因為大部分既有列找不到而中止。所以這次的抽取檔是用工具合併的：

- `aa_rebaseline.py merge`，輸入是 `docs/reports/aa-pages-2026-11-10/` 的 85 頁加上 Hydrogen 四頁。
- 輸出 `docs/reports/aa-v5.0.1-extract-2026-11-13.json`，SHA-256 `6544aa0e3ae5ab5b73aeeac38a34e22290a551bf09615aea654f3bdbe73fc475`。
- 同一個指令只用 85 頁重跑，產出和 `aa-v5.0.1-extract-2026-11-10.json` 位元組相同（`18d62899…cf11e9`），代表合併方法和 11-10 那次一致。

和 Carbon 那次「沿用 11-10 抽取檔」不同（**偏離前例**）：新頁面較晚抓，合併規則取最新非空值，所以其他模型有 21 個欄位被刷新：

- 11 筆 `name`（AA 頁面拿掉了強度括號）。
- 8 筆 `intelligenceIndexTimePerTask`。
- Claude Sonnet 5.5 的 `briefcaseBreakdown`（Elo 小幅變動）。
- Carbon high 的 `mlcrOverall`（null → 0.194，與 11-12 重抓一致）。

這些欄位都不是分數：沒有任何 `intelligenceIndex` 變動，既有 71 列逐列比對，除了 `as_of`、`evidence_report` 之外完全相同。`value` 與 `lanes` 用 11-10 抽取檔和用新抽取檔跑，輸出完全相同。

## 依據

- **Google**：GA，2026-11-12，型號 `gemini-4-hydrogen`。
  - 文件：https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-hydrogen
  - 部落格：https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-hydrogen/
- **選擇子**：`gemini-4-hydrogen-{xhigh,high,medium,low}`。
  - 主控 2026-11-13 在 MacStudio 用 agy 1.5.7 實測：`~/.local/bin/agy models` 列出這四個（顯示名 Gemini 4 Hydrogen (Low/Medium/High/XHigh)），`agy --model gemini-4-hydrogen-low -p 'Reply with exactly: ok'` 回 `ok`。
  - 工人沒有重跑這項實測。
- **對應**：AA 的無尾碼頁 `gemini-4-hydrogen` 是 xhigh。抽取檔的 `effort_label` 是 `xhigh`；新聞報導 Hydrogen 思考層級分 low／medium／high／xhigh，xhigh 為 54 分。
- `xhigh` 在現有程式碼是合法的編碼強度，沒有改任何強度列舉：
  - `caller_identity.ENCODED_EFFORTS` 含 `xhigh`。
  - `aa_policy` 的編碼選擇子檢查只要求 `runtime_model` 以 `-<effort>` 結尾。
  - `build_overlay.ALL_EFFORTS` 含 `xhigh`。
- agy 另列出的 `gemini-pro-orchestrator-g1-pro`（Orchestrator (Argon)）在 AA 沒有分數，依指示未加入。

## 新增列

| config | agy 型號 | aa_slug | raw | score |
|---|---|---|---|---|
| gemini/gemini-4-hydrogen | gemini-4-hydrogen-xhigh | gemini-4-hydrogen | 53.92 | 54 |
| gemini/gemini-4-hydrogen-high | gemini-4-hydrogen-high | gemini-4-hydrogen-high | 50.79 | 51 |
| gemini/gemini-4-hydrogen-medium | gemini-4-hydrogen-medium | gemini-4-hydrogen-medium | 41.23 | 41 |
| gemini/gemini-4-hydrogen-low | gemini-4-hydrogen-low | gemini-4-hydrogen-low | 26.89 | 27 |

- 數量變化：計分列 71 → 75；Gemini 14 → 18；別名 59 → 63；unknown 維持 82；reference 維持 1。
- 列的形狀：
  - id 照 Carbon：最高強度不加尾碼。
  - medium、low 複製 3.8 Flash 同強度列的形狀；xhigh 與 high 複製 Flash high（agy 沒有 Flash xhigh）。
  - 別名 `gemini-4-hydrogen-xhigh` 以 `gemini-3.8-flash-high` 為範本。
- 傳輸對應一律是 `status=unknown`、`runtime_verified=false`。
- 四列加入 `scripts/lib/aa_policy.py` 的 `HUMAN_TRANSPORT_PROOF_REQUIRED`。效果：真人豁免（`--operator-asserted-human`、`OMNILANE_AA_OPERATOR_ASSERTED_HUMAN=1`）下也要有傳輸證明，明確指定會被拒（`runtime-mapping-unverified`）。
- 這份清單目前是寫死的 config id。可以改成通用規則（例如「傳輸對應尚未 runtime_verified 的 Gemini 編碼強度列」），但這次沒有改，避免擴大範圍，由主控決定。

## 車道

`aa_rebaseline.py value` 在任何車道都沒有選出 Hydrogen。三種組合的 `value` 輸出逐字相同，所以 Hydrogen 本身和刷新的欄位都不改變任何選擇：

- A：11-12 基底政策檔 ＋ 11-10 抽取檔。
- B：新政策檔 ＋ 11-10 抽取檔加 Hydrogen 四筆記錄（只在暫存區）。
- C：新政策檔 ＋ 新抽取檔。

value 規則沒選中的原因：

- hardest-coding：在 Hydrogen 的每個分數上限，都有 Terminal-Bench 4.0 高出近似帶寬 0.02 以上的可及列（上限 54 時是 Carbon low 0.626 對 Hydrogen xhigh 0.535，上限 51 時是 Argon 0.571 對 Hydrogen high 0.540）。
- bulk-mechanical：上限 54 時，Hydrogen xhigh 差 0.091，超出帶寬 0.06；成本 4086 也不到 Carbon low 5725 的一半，所以不算接近。上限 51 時，Hydrogen high 差 0.031，在帶寬內；但 GPT-6.1 Sol 各列也算接近而且更便宜（Sol medium 成本 389 對 Hydrogen high 1768），規則選最便宜的。
- hard-judgment／ui-draft／long-context：AA 沒公布 Hydrogen 的 HLE、MMMU-Pro、mlcrOverall，規則無從放。
- taste-final：Briefcase overall Elo 差距超出帶寬。
- fast-agentic：v5.0.1 沒有首字延遲，這條本來就算不出任何列。

**最終位置：Hydrogen high 一律放在 Argon high 正後方。** 範圍是每一條含 `gemini gemini-4-argon-high -` 的車道，共 11 條（除了 arbitrate 全部都有）。其他列一律不動。

| 車道 | Hydrogen high 位置 | 改後片段 | 首選（不變） |
|---|---|---|---|
| hardest-coding | 32 | … argon-high → hydrogen-high → 3.8-flash-medium … | gemini-4-carbon-high |
| bulk-mechanical | 13 | … argon-high → hydrogen-high → 3.8-flash-medium … | gpt-6.1-sol medium |
| triage | 3 | gpt-6-luna high → argon-high → hydrogen-high → 3.8-flash-low … | gpt-6-luna high |
| hard-judgment | 7 | … argon-high → hydrogen-high → opus-5-5 medium … | claude-fable-5-2 xhigh |
| taste-final | 20 | … argon-high → hydrogen-high → 3.8-flash-medium | claude-sonnet-5-5 xhigh |
| consult | 5 | … grok-4.7 high → argon-high → hydrogen-high → 3.8-flash-high | gpt-6-astra xhigh |
| ui-draft | 11 | … argon-high → hydrogen-high → 3.8-flash-high … | gemini-4-carbon-medium |
| long-context | 13 | … argon-high → hydrogen-high → 3.8-flash-medium … | gpt-6-astra medium |
| fast-agentic | 8 | … argon-high → hydrogen-high → 3.8-flash-medium … | gpt-6.1-sol medium |
| live-search | 3 | grok-4.7 high → argon-high → hydrogen-high → 3.8-flash-high … | grok-4.7 high |
| coding-overflow | 3 | grok-4.7 high → argon-high → hydrogen-high → 3.8-flash-high … | grok-4.7 high |

- 用腳本核對過三件事：
  - 每條車道拿掉 Hydrogen high 後，和改前逐項相同。
  - 11 條的首選都沒變。
  - 每條車道第一個 Gemini 列都沒變：Carbon 領頭的兩條仍是 Carbon，其餘仍是 Argon。
- **這個位置不是 value 規則選出的。** Hydrogen xhigh、medium、low 不在任何車道。

### 為什麼放 Argon 後面（取代第二則更正的逐條排法）

第二則更正的排法，是照各車道 10-14 的依據決定前後：九條車道 Hydrogen 會排在 Argon 前面，只有 hardest-coding、hard-judgment 在後。完整測試抓到它造成行為退步：

- **鎖定廠商不往後找。** `scripts/dispatch.sh` 第 570–580 行：`resolve_chain` 收到 `requested_vendor` 時，只看該廠商第一個列。
  - 第 573–576 行：政策拒絕就 `return 6`。
  - 第 579–580 行：CLI 不在就 `return 4`。
  - 不會往同廠商下一列找。
  - 第 1028–1041 行：`--vendor` 路徑收到 6 就印拒絕訊息，`exit 3`（第 1039–1041 行）。
- **Hydrogen 在真人豁免下也要傳輸證明。** `scripts/lib/aa_policy.py` 的 `HUMAN_TRANSPORT_PROOF_REQUIRED`（第 38–42 行）加上 `decide()` 裡真人豁免分支（第 630–636 行，以 `_requires_human_transport_proof` 判斷）：豁免只放寬分數上限，傳輸檢查照做。
- **Argon 不在那份清單**，真人豁免下不必證明。
- **後果（Hydrogen 排前時）：**
  - 還沒重簽、或帳號沒有 Hydrogen 的主機，`--vendor gemini <車道>` 在那九條車道會從「選到 Argon」變成被拒（rc 3，`runtime-mapping-unverified`）。
  - 一般模型呼叫端也有同樣問題：上限 50 的呼叫端（Sonnet 5.5 xhigh、GPT-6.1 Sol high）會先撞到 51 分的 Hydrogen 被拒，而不是拿到 50 分的 Argon。
  - 測試實際抓到的是 `test_mode_unification` 的三個 Gemini 子測試（`--vendor gemini long-context`）與 `test_aa_routing_update` 原本的 `consult --vendor gemini`。
- **routing.yaml 註解「a host without Hydrogen proof skips it and selects as before」：** 只對沒有鎖定廠商的車道成立。鎖定廠商時不成立，因為不會跳過。

### 兩種修法比較

1. **改派工程式**：鎖定廠商時跳過沒證明的同家列。
   - 這會改掉 10-14 以來明定的「`--vendor` 不往下找」，影響所有廠商與 Carbon。
   - 而且只跳過沒證明的列還不夠：上限 50 的呼叫端撞到 Hydrogen 51 是分數拒絕，不是傳輸拒絕，要連分數拒絕都跳過，等於全面改成往下找。
2. **改排序**：Hydrogen high 放 Argon 後面。只動 Hydrogen 這一列，不改程式。

採用第 2 種。

- 鎖定廠商的行為和改前完全相同。
- 沒鎖定廠商時：
  - Ultra 主機照舊先選 Argon。
  - 不能用 Argon 的主機（Argon 限 Google AI Ultra，Hydrogen 是 GA）會跳過 Argon、選到已證明的 Hydrogen。
- 代價：在 Ultra 主機上，就算 Hydrogen 比較便宜，也不會先選它。主控若要 Hydrogen 優先，需要先改 `resolve_chain` 的鎖定語意（第 1 種），另案處理。

**全車道依新 AA 重排移到 0.63.0，這次不套用**（Vincent 原話「AA重排讓他去0.6.3」）。主控改範圍時，我還沒算出全車道重排的結果，所以沒有草稿檔可存。

既有的「規則和現行鏈不同」提示（bulk-mechanical、ui-draft、long-context 的幾個上限）與 11-12 相同，不是這次造成的，照 Carbon 那次的做法不動。

## 探測

`build_overlay.py` 的 `PROVEN` 加了四筆：`agy-gemini-4-hydrogen-{xhigh,high,medium,low}`。重簽時會用 `agy --model <型號>` 實測，選擇子類型是 `model_id_encoded_effort`。

## 測試檔調整

- `tests/test_aa_build_shape.py`、`tests/test_aa_retired_rows.py`、`tests/test_aa_unmapped_model.py`、`tests/test_aa_withdrawn_rows.py` 原本拿現行政策檔配 11-10 抽取檔跑 build。現行政策檔有了 11-10 抽取檔沒有的 Hydrogen 四列，build 會照設計中止，所以這四個測試改指向 `aa-v5.0.1-extract-2026-11-13.json`。測試內容沒改。
- `tests/test_aa_policy.py`、`tests/test_resign_per_selector.py` 只把計分列數與 Gemini 列數各加 4。
- `tests/test_aa_routing_update.py`：
  - `test_argon_keeps_the_five_value_and_purpose_defaults` 改成 Argon → Hydrogen high → Flash。
  - agy 單一主機的 `--list` 與 `consult --vendor gemini` 預期都不變，仍是 Argon。
  - `EXPECTED_DEFAULTS` 的首選都沒變，只加了一行註解。
- `tests/test_grok_unconfined.py`：`test_shipped_coding_overflow_skips_grok_before_argon_without_opt_in` 改成有、無 Hydrogen 證明兩種都跑，都要選到 argon、candidate=2/8。
  - 有證明的情況用測試自己的 overlay 夾具，不依賴本機 `~/.omnilane`。
  - 同一個子測試也確認夾具真的生效：明確指定 Hydrogen 時，有證明 rc 0、沒證明 rc 3。
- 新增 `tests/test_native_hydrogen_regression.py`，其中：
  - `test_hydrogen_high_sits_right_after_argon`：檢查 11 條車道 Hydrogen high 都在 Argon 正後方，其他 Hydrogen 列不在任何車道。
  - `test_pinned_gemini_still_selects_argon_without_hydrogen_proof`：檢查 9 條車道在沒有 Hydrogen 證明時，`--vendor gemini` 仍選到 Argon；hardest-coding、ui-draft 由 Carbon 領頭，排除在外。拿掉 `HUMAN_TRANSPORT_PROOF_REQUIRED` 裡的 Hydrogen 四列後跑，40 個子測試失敗；還原後全過。

## 未驗證

- 四個選擇子的主機傳輸證明：主控實測過 `agy models` 列表與 low 的單次回應，但還沒有經 `omnilane resign` 產生探測證據，overlay 裡沒有 Hydrogen 對應。主控重簽後才會放行。
- xhigh／high／medium 三個選擇子沒有實際送過請求（只有 low 回過 `ok`）。
- AA 無尾碼頁對應 xhigh：依抽取檔的 `effort_label` 與新聞報導，Google 官方文件沒有逐字寫出 AA 頁與思考層級的對應。
