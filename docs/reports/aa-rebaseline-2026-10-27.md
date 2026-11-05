# AA v4.3.2 核准快照（2026-10-27）：加入 GPT-6.1 Terra

來源：claude-code-s／MacStudio；工作樹 `feat/aa-gpt-6-1-terra`，基底 `c4f5b6a`。

狀態：**approved**，主控於 2026-10-27 依 Vincent 同日 07:0x 指示「另外OPENAI放新模型了，去查一下最新AA把新模型收進去」核准；主機還沒有重簽或部署。

快照：`aa-v4.3.2-2026-10-27-v1`。
SHA-256：`916b1d1afa2aeaf875e451034d2b6e2ed9eb587285066223f698af49c3104037`（提議版是 `0fa2df6e95445069b1fa46474bc43460d018dac8587c503035c0734c37ad285f`，差別只有 `approval.status` 與 `estimated_scores` 兩欄）。

抽取檔：`aa-v4.3.2-extract-2026-10-27.json`（Gemini 4 Argon 頁，2026-10-27 抓取）。SHA-256：`7562dc0103b9d8d5aa6d6217af8fb4a953387c373331d704095802b20e9d3697`。

- 評分列從 124 增加到 130：`codex/gpt-6-1-terra` 48、`-xhigh` 45、`-high` 44、`-medium` 41、`-low` 36、`-non-reasoning` 33，都不是估計值。Codex 從 57 變 63。
- 未知列維持 9。其他所有分數和排序都和 `aa-v4.3.2-2026-10-18-v1` 相同。
- `routing.yaml`：Terra 依價值規則插入 hardest-coding、bulk-mechanical、ui-draft、fast-agentic；各車道首選不變。
- 設定選單加入 `gpt-6.1-terra`，探測候選涵蓋五個推理強度與 `none`；每台主機的傳輸都要另外證明。
- 快照代號改變，所以升級後每台主機都要執行 `omnilane resign`。
- 未處理：AA 重測後 long-context 的價值選擇會改變（與 Terra 無關），留待決定。

細節：`docs/reports/aa-gpt-6-1-terra-evidence-2026-10-27.md`；各廠牌證據：`docs/reports/aa-{claude,codex,gemini,grok}-evidence-2026-10-27.md`。
