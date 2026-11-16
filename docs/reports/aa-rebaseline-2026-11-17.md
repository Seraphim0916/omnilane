# AA v5.0.1 快照（2026-11-17）：Gemini 4 Argon Medium 實測計分與 Carbon／Argon 定價更新

<!-- source: agy-s／MacStudio 工人（對話 a373837d-cd0f-4103-815e-d13bed837922）；依總主控補充信 CHIEF-ADDENDUM-AA-ROSTER-APPROVED-1117-0334.md（Vincent 核准換快照）建立於工作樹 feat/aa-rebaseline-20261117 -->

- 快照：`aa-v5.0.1-2026-11-17-v1`（AA Intelligence Index `5.0.1`，`as_of` `2026-11-17`，`approval.status = "approved"`）。
- 政策檔 SHA-256：`843f50e3da589fa6887df4c1ef5169b3d8be9c082691b4ce3f7c16921d4803d7`（`build --as-of 2026-11-17 --approval approved` 連跑兩次位元組完全一致）。
- 抽取檔：`docs/reports/aa-v5.0.1-extract-2026-11-17.json`，SHA-256：`70e854a2aec39dac1f4160f98b594ab922fd7bdbd13e7003a974dd5b4c367139`。
  - 由 2026-11-17 實抓之 72 個模型自有頁面合併（71 筆四家供應商計分紀錄），並以 `aa_rebaseline.py fill` 自同屬 v5.0.1 之 `docs/reports/aa-v5.0.1-extract-2026-11-13.json` 回填 AA 網頁於兩次抓取間隱藏之 `briefcaseBreakdown.{analyticalQuality,presentation,rubricPassRate}` 共 210 個子欄位（完整明細列於抽取檔 `filled_from_earlier_capture`）。
- 傳輸相容性封存檔：`config/transport-compatibility.json` 新增 `aa-v5.0.1-2026-11-17-v1` 條目，SHA-256：`1586044733e606a5a84e2cbecea5e05512efee71aa7570ba7d6ec912fd81e569`；`scripts/lib/transport_compat.py` SHA-256：`284e3bd17913b3291229fa8ce1c6158da5628a800aecada51f0f64a385bdd8ea`。

## 與 `aa-v5.0.1-2026-11-13-v2` 的差異

1. **新增實測計分列 `gemini/gemini-4-argon-medium`**（AA 代號 `gemini-4-argon-medium`，Antigravity 選擇子 `gemini-4-argon-medium`）：
   - `intelligenceIndex = 47.6040598289046` → `score_raw = 47.6`、四捨五入整數 `score = 48`、`estimated = false`、`authority_eligible = true`。
   - 取代 2026-11-13 v2 之保守上限列 `gemini/gemini-4-argon/medium/unspecified`（原借用 High 之 `50`）。
   - 同家族低強度頁 `/models/gemini-4-argon-low` 仍為 HTTP `404`，其保守上限列 `gemini/gemini-4-argon/low/unspecified` 隨 `derive_scores` 規則自動改取 `gemini/gemini-4-argon-medium`（上限由 `50` 收緊至 **`48`**）。
   - 總列數維持 **159 列**：`scored_configs` **98 列**（實測 **76 列**［+1］＋代理分數 **1 列**＋保守上限 **21 列**［-1］，`estimated` 由 `23` 降為 `22`）、`unknown_configs` **60 列**、`reference_configs` **1 列**、別名 **64 個**（+1 `gemini-4-argon-medium`）。既有全部 75 列實測設定的 `score_raw` 與 `score` **0 變動**。
2. **Gemini 4 Carbon（High／Medium／Low）與 Gemini 4 Argon（High）API 單價下調 50%**：輸入／輸出單價由 `$4/$20` 降為 `$2/$10`（混合單價 `$2.94` → `$1.47`），指數測試總成本同步下降。
3. **`gemini-4-hydrogen`（XHigh）新增 `mlcrOverall = 0.211`**（低於 `long-context` 現有首選 `gpt-6-astra medium` `0.383` 與 `gpt-6.1-sol medium` `0.328`，不改變該車道）。
4. **傳輸閘門**：`gemini/gemini-4-argon-medium` 納入 `HUMAN_TRANSPORT_PROOF_REQUIRED` 與 `build_overlay.PROVEN`（沿用 `agy-gemini-4-argon-medium` 探測證據），無傳輸證明時車道自動跳過、明確指定時拒絕（`runtime-mapping-unverified`）。

## 車道變動

全部 12 條車道的**第 1 順位首選均不變**；僅 `aa_rebaseline.py value` 選中 `gemini-4-argon-medium` 的兩條車道插入該列，其餘 10 條車道完全不變：

- **`hardest-coding`**（35 → 36 列）：`aa_rebaseline.py value` 在天花板 48–49 選出 `gemini-4-argon-medium`（Terminal-Bench 4.0 `0.551`、分數 `48`），但若直接置於第 5 順位（早於第 31 順位之 `gemini-4-argon-high` 分數 `50`、TB4.0 `0.571`），會使天花板 50–53 的 `--vendor gemini` 呼叫降級選到 Medium 而非 High（由 `test_ceiling_52_passes_proven_carbon_and_selects_argon` 回歸測試驗證）；因此照 `2026-11-13` Hydrogen 先例，置於 Gemini 尾段 `gemini-4-hydrogen-high` 正後方（第 33 順位、`gemini-3.8-flash-medium` 之前），使天花板 50–53 維持選 Argon High（`50`）、天花板 48–49 `--vendor gemini` 升級選 Argon Medium（`48`）。
- **`ui-draft`**（19 → 20 列）：第 2 順位加入 `gemini gemini-4-argon-medium -`（MMMU-Pro `0.883`、分數 `48`，緊接於 `gemini-4-carbon-medium` 之後、`claude-opus-5-5 high` 之前；無 Ultra 傳輸證明的主機跳過前兩列後仍落回 `claude-opus-5-5 high`）。
