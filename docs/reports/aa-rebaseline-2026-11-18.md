# AA v5.0.1 快照（2026-11-18）：新增 OpenAI GPT-6.2 Astra 與 GPT-6.2 Sol 實測計分及車道更新

<!-- source: agy-s／MacStudio 工人（對話 65c2d183-9bf7-44e5-97aa-23dde6e72d57）；依總主控任務書 TASK-OMNILANE-ASTRA62-SOL62-1118.md 建立於隔離工作樹 feat/aa-20261118-astra62-sol62 -->

- 快照：`aa-v5.0.1-2026-11-18-v1`（AA Intelligence Index `5.0.1`，`as_of` `2026-11-18`，`approval.status = "approved"`）。
- 政策檔 `config/aa-model-policy.json` SHA-256：`42ac3db913098e902762f27cb76d6b83ca4f0223e77553cc9c18ed4dd14a9673`（`build --as-of 2026-11-18 --approval approved` 連跑兩次位元組完全一致）。
- 抽取檔：`docs/reports/aa-v5.0.1-extract-2026-11-18.json`，SHA-256：`6b48abb9ba75e1f6d16be031a81b240f25aef685d50d28eba8de9d2d50a7f8e2`。
  - 由 2026-11-18 實抓之 82 個模型自有頁面合併（81 筆四家供應商計分紀錄），並以 `aa_rebaseline.py fill` 自同屬 v5.0.1 之 `docs/reports/aa-v5.0.1-extract-2026-11-17.json` 回填 AA 網頁隱藏之既有模型 `briefcaseBreakdown.{analyticalQuality,presentation,rubricPassRate}` 共 210 個子欄位（完整明細列於抽取檔 `filled_from_earlier_capture`）。
- 傳輸相容性封存檔：`config/transport-compatibility.json` 新增 `aa-v5.0.1-2026-11-18-v1` 條目，SHA-256：`16dc1df69e1634f8ec787b21d4e3df425cf7a311e5d383365e7fca5e1edb6521`；`scripts/lib/transport_compat.py` SHA-256：`8322f76371042676e1252569fc43278fb2add24f293b717c276dfcd1a406d7b2`。

## 與 `aa-v5.0.1-2026-11-17-v1` 的差異

1. **新增 OpenAI `gpt-6.2-astra` 與 `gpt-6.2-sol` 共 10 筆實測推理列**（2026-11-17 正式發佈；API 與 Codex 均拒絕 `reasoning.effort = "none"`，AA 亦無 `-non-reasoning` 列）：
   - **GPT-6.2 Astra (`gpt-6.2-astra`)**（API 標準單價 `$8/$40`、快取讀取 `$0.40`/1M tokens）：
     - `codex/gpt-6-2-astra` (`max`)：`score_raw = 62.14` → `score = 62`（指數整套成本 `$4,530`）
     - `codex/gpt-6-2-astra-xhigh` (`xhigh`)：`score_raw = 61.56` → `score = 62`（指數整套成本 `$2,484`）
     - `codex/gpt-6-2-astra-high` (`high`)：`score_raw = 60.09` → `score = 60`（指數整套成本 `$2,007`）
     - `codex/gpt-6-2-astra-medium` (`medium`)：`score_raw = 57.00` → `score = 57`（指數整套成本 `$1,457`）
     - `codex/gpt-6-2-astra-low` (`low`)：`score_raw = 45.65` → `score = 46`（指數整套成本 `$794`）
   - **GPT-6.2 Sol (`gpt-6.2-sol`)**（API 標準單價 `$2/$10`、快取讀取 `$0.10`/1M tokens）：
     - `codex/gpt-6-2-sol` (`max`)：`score_raw = 57.26` → `score = 57`（指數整套成本 `$1,108`）
     - `codex/gpt-6-2-sol-xhigh` (`xhigh`)：`score_raw = 55.74` → `score = 56`（指數整套成本 `$698`）
     - `codex/gpt-6-2-sol-high` (`high`)：`score_raw = 54.89` → `score = 55`（指數整套成本 `$553`）
     - `codex/gpt-6-2-sol-medium` (`medium`)：`score_raw = 52.68` → `score = 53`（指數整套成本 `$405`）
     - `codex/gpt-6-2-sol-low` (`low`)：`score_raw = 40.55` → `score = 41`（指數整套成本 `$224`）
2. **既有 76 筆實測列與 22 筆衍生上限列零變動**：`scored_configs` 由 **98 列增為 108 列**（實測 **86 列**［+10］＋代理 **1 列**＋保守上限 **21 列**；`codex` 由 39 增為 49）、`unknown_configs` **60 列**、`reference_configs` **1 列**、別名由 **64 個增為 66 個**（+2：`gpt-6.2-astra`、`gpt-6.2-sol`）。舊型號 `gpt-6-astra`、`gpt-6.1-sol`、`gpt-6.1-terra`、`gpt-6-luna` 全數保留。
3. **探測清單更新**：`scripts/lib/build_overlay.py` 將 `gpt-6.2-astra` 與 `gpt-6.2-sol` 五檔位納入 `PROVEN`，各主機合併後須執行 `omnilane resign` 完成新選擇器傳輸驗證。

## 車道變動

1. **`hardest-coding`**（36 → 44 列）：`aa_rebaseline.py value` 於天花板 62+ 選出 **`codex gpt-6.2-astra xhigh`**（分數 `62`，Terminal-Bench 4.0 `0.641` 與 `gemini-4-carbon-high` `0.652` 差距 `0.010` ≤ `0.02` 近同分帶，而整套指數成本 `$2,484` 對 `$5,333` 便宜 53%）升任第 1 順位，並於天花板 46 以 `codex gpt-6.2-astra low`（TB4.0 `0.510`）取代 `codex gpt-6.1-sol medium`（`0.480`）；`gemini-4-carbon-high`（60）與 `gemini-4-carbon-low`（54）維持第 2、3 順位服務天花板 54–61 及 `--vendor gemini`，`gemini-4-argon-medium` 維持在 Gemini 尾段避免天花板 50–53 降級。
2. **`bulk-mechanical`**（17 → 22 列）：`aa_rebaseline.py value` 將第 1 順位由 `codex gpt-6.1-sol xhigh`（52 分，TB4.0 `0.540`、`9.9` 分鐘、`$718`）升級為 **`codex gpt-6.2-sol medium`**（53 分，TB4.0 `0.561`、`3.1` 分鐘、`$405`），後續價值鏈為 `gpt-6.1-sol medium` → `gpt-6.1-terra high` → `gpt-6.2-astra low` → `gpt-6.2-sol low` → `gpt-6.1-terra medium`，其後接 `gpt-6.2-sol xhigh/high` 與原備援鏈。
3. **`long-context`**（16 → 24 列）：`aa_rebaseline.py value` 將第 1 順位由 `codex gpt-6-astra medium`（52 分，`mlcrOverall = 0.383`、`$2,638`）升級為 **`codex gpt-6.2-astra xhigh`**（62 分，`mlcrOverall = 0.378` 差距 `0.005` ≤ `0.02` 近同分帶且成本 `$2,484` 更低），第 2 順位保留 `gpt-6-astra medium`（服務天花板 52–61），第 3 順位改為 **`codex gpt-6.2-sol low`**（41 分，`mlcrOverall = 0.322`、`$224`，服務天花板 41–51）。
4. **`consult`**（6 → 8 列）：用途排序車道將 Codex 諮詢代表升級為 **`codex gpt-6.2-astra xhigh`**（62 分）與 `codex gpt-6.2-astra high`（60 分），後接 `codex gpt-6-astra xhigh`（55 分）供較低天花板或未重簽主機降階。
5. **`hard-judgment`、`taste-final`、`ui-draft`、`fast-agentic`**：第 1 順位與價值前段維持不變（AA v5.0.1 尚未公佈 GPT-6.2 之 `hle`、`briefcaseBreakdown.{analyticalQuality,presentation}`、Sol 之 `mmmuPro` 與各檔位獨立首字延遲 `timeToFirstAnswerToken`），於備援段加入對應已計分之 GPT-6.2 非 `max` 列。
6. **`triage`、`live-search`、`coding-overflow`、`arbitrate`**：完全不變。
