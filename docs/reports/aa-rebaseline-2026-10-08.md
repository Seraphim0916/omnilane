# AA v4.3.2 重建提案（2026-10-08）

來源：codex-s／MacStudio；工作樹 `feat/aa-haiku-5-5`，基底 `cc1aecc`。

狀態：**proposed，尚未核准或部署**。快照 `aa-v4.3.2-2026-10-08-v1`，
政策檔 SHA256：`5c00408010aaaa79478709cb2ece1db0df0f5cf6725ae34ee3d3b56cd9f867f1`。

39 頁合併涵蓋 231 筆廠商資料；政策由 114 列增為 120 列：新增 Haiku 5.5 五個強度，
並恢復 Sonnet 5.5 low。沒有撤回原有計分列；GPT-5.4 mini 三列維持操作者指定停用。

- [完整來源、304 個欄位差異及車道證據](aa-haiku-5-5-evidence-2026-10-08.md)
- [Claude](aa-claude-evidence-2026-10-08.md)、[Codex](aa-codex-evidence-2026-10-08.md)、
  [Grok](aa-grok-evidence-2026-10-08.md)、[Gemini](aa-gemini-evidence-2026-10-08.md)
- [合併抽取檔](aa-v4.3.2-extract-2026-10-08.json)

主控已於 2026-10-08 由官方文件確認 Anthropic 模型代號 `claude-haiku-5-5` 與五種強度
`low`、`medium`、`high`、`xhigh`、`max`（Claude Code 自 v2.1.293 起支援）。
來源：[Model IDs](https://platform.claude.com/docs/en/models/haiku-5-5/overview)、
[Effort](https://platform.claude.com/docs/en/build-with-claude/effort)、
[Model configuration](https://code.claude.com/docs/en/model-config)。
五個 Haiku 5.5 傳輸映射仍是「not probed on host」；各強度實際請求形狀及真實派送能力
尚未驗證，仍待操作者核准及主機探測驗收。
本工人未執行 `omnilane resign`，未寫入使用者設定或主工作目錄。
