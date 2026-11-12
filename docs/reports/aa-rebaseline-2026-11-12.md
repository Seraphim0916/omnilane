# AA v5.0.1 提案快照（2026-11-12）：Gemini 4 Carbon 進評分表與車道

來源：claude-code-s／MacStudio 被控端3；工作樹 `feat/aa-gemini-carbon`，基底 `28f0e24`；任務 OMNILANE-CARBON-LANE-1112（MacStudio 主控端派）。

狀態：**approved**（2026-11-12）。Vincent 11-12 00:1x 指示「列進車道給別人選吧」；00:4x 答「驗收過就核准並合併」。第二方審查（`~/.openclaw/staging/2026-11-12-carbon-review/REVIEW-CARBON.md`）對分數與車道沒有意見，修正 FIX1 後由工具以 `--approval approved` 重產。主機尚未重簽。

- 快照：`aa-v5.0.1-2026-11-12-v1`。
- SHA-256：`753709bb79e32ed565df250640fd5ce1c078a261f3ee8fe0dad59824416f5a25`。`build --base <2026-11-10 核准政策檔> --approval approved` 連跑兩次，位元組相同。和先前 proposed 版（`1220a9bb…5c8801`）只差 `approval.status` 與 `approval.estimated_scores` 兩個欄位。
- 抽取檔：沿用 `docs/reports/aa-v5.0.1-extract-2026-11-10.json`，前例是 `aa-v4.3.2-2026-10-28-v1`。
  - 2026-11-12 00:16 已重抓 Carbon 三頁，存成 `docs/reports/aa-v5.0.1-carbon-refetch-2026-11-12.json`。
  - 分數、價格、Terminal-Bench 4.0、AutomationBench、MMMU-Pro 都和擷取檔相同。
  - 只有「每題分鐘數」小幅變動；high 新增了 mlcrOverall 0.194。這個值低於 long-context 現有各列，不影響該車道。
  - 因此不重拍整份。

## 依據

- **Google**：2026-11-10 公告 Carbon 開放給 Google AI Ultra 訂閱者，可在 Antigravity 使用。
  - https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-carbon-now-available/
  - https://antigravity.google/blog/gemini-4-carbon-in-google-antigravity/
- **agy 1.5.5 的 `agy changelog`**：「Added Gemini 4 Carbon (Low, Medium, and High) to `/model` and `--model` when you sign in with Application Default Credentials (`AGY_ADC_AUTH`)」。
- **選擇子**：`gemini-4-carbon-{high,medium,low}`。這是依 agy 把強度寫進型號字串的慣例推得的，和 Argon、3.8 Flash 同一種形狀。Google 沒有逐字公布，要等主機探測確認。
- **MacStudio 本機現況**：`agy models` 列不出 Carbon。這台的帳號沒有 Ultra／ADC 權限，所以本機無法探測。

## 新增列（從 reference_configs 移到 scored_configs）

| config | agy 型號 | aa_slug | raw | score |
|---|---|---|---|---|
| gemini/gemini-4-carbon | gemini-4-carbon-high | gemini-4-carbon | 60.03 | 60 |
| gemini/gemini-4-carbon-medium | gemini-4-carbon-medium | gemini-4-carbon-medium | 58.26 | 58 |
| gemini/gemini-4-carbon-low | gemini-4-carbon-low | gemini-4-carbon-low | 54.22 | 54 |

- 計分列 68 → 71；Gemini 11 → 14；reference 4 → 1（只剩 Gemini 3.1 Pro）；別名 56 → 59；unknown 維持 82。
- 既有 68 列逐列比對：除了 `as_of`、`evidence_report`，其餘欄位完全相同。
- 傳輸對應一律是 `status=unknown`、`runtime_verified=false`。探測沒通過以前，派工會被傳輸閘擋下。
- 真人豁免（`--operator-asserted-human`、快速入門的 `OMNILANE_AA_OPERATOR_ASSERTED_HUMAN=1`）原本會跳過傳輸檢查。FIX1 起 Carbon 三列在真人路徑也要有傳輸證明：車道會跳過，明確指定會被拒（`runtime-mapping-unverified`）。其他模型的真人豁免不變。程式在 `scripts/lib/aa_policy.py` 的 `HUMAN_TRANSPORT_PROOF_REQUIRED`。

## 車道

依 `aa_rebaseline.py value` 的結果放進車道，不另外手調：

- **hardest-coding**：Carbon high 排第一，Carbon low 第二（呼叫端上限 54–59 時選它），後面整串不變。
- **ui-draft**：Carbon medium 排第一，後面整串不變。
- 其他車道沒有變動，包括 Gemini 家族位置與依用途排序的車道。
- 加入 Carbon 後，`value` 在 bulk-mechanical 多了一個非 Carbon 的選擇：GPT-6.1 Sol xhigh。11-10 核准時這條車道定為「首選不變」，所以這次不改，由主控決定。

## 探測

`build_overlay.py` 的 `PROVEN` 加了三筆：`agy-gemini-4-carbon-{high,medium,low}`。重簽時會用 `agy --model <型號>` 實測；沒有 Ultra／ADC 權限的主機會探測失敗，這是預期結果。
