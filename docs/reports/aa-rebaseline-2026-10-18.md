# AA v4.3.2 核准快照（2026-10-18）：GPT-5.4 mini 放回評分表

來源：claude-code-s／MacStudio；工作樹 `feat/gpt-5-4-mini-score-row`，基底 `e1a86aa`。

狀態：**approved，Vincent 已於 2026-10-19 01:1x 核准快照與合併（原話選項「核准並合併 (Recommended)」，由 MacStudio 主控端轉達）；主機還沒有重簽或部署**。

快照：`aa-v4.3.2-2026-10-18-v1`。
SHA-256：`aeef33407e43a1bba6720cf431acdce45add2cb8b042985af69c4f57aa27a464`（提議版是 `6ac4158930fac4f4b435e837e374fa1434ce9c29e8a122e9be57ff68e1a3e1b5`，差別只有 `approval.status` 與 `estimated_scores` 兩欄）。

使用 `aa-v4.3.2-extract-2026-10-14.json`，沒有重新抓取。抽取檔 SHA-256：`41588b6710e724f0393b1745bdfc1b468590f93d126eaf6fd81b7022485e4817`。

- 評分列從 121 增加到 124：`codex/gpt-5-4-mini` 24、`codex/gpt-5-4-mini-medium` 20（估計值）、`codex/gpt-5-4-mini-non-reasoning` 11（估計值）。Codex 從 54 變 57。
- 未知列從 12 減到 9，因為這三列離開了 `unknown_configs`。
- 其他所有分數和排序都和 `aa-v4.3.2-2026-10-14-v1` 相同。
- `routing.yaml` 沒變，`lanes` 輸出裡沒有這三列。
- 設定選單別名和探測候選都還原了；每台主機的傳輸都要另外證明。
- 快照代號改變，所以升級後每台主機都要執行 `omnilane resign`。

細節：`docs/reports/aa-gpt-5-4-mini-evidence-2026-10-18.md`；各廠牌證據：`docs/reports/aa-{claude,codex,gemini,grok}-evidence-2026-10-18.md`。
