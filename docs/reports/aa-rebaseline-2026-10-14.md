# AA v4.3.2 Gemini 4 Argon 核准快照（2026-10-14）

來源：codex-s／MacStudio；工作樹 `feat/aa-gemini-4-argon`，基底 `bafcedb`。

狀態：**approved，Vincent 已於 2026-10-18 09:1x 核准快照與合併（原話「都做完」）；主機尚未重簽或部署**。

快照：`aa-v4.3.2-2026-10-14-v1`。
SHA-256：`483cd8c36694384032cee05904c0a7dc5e62d89873d18de73231740368a535ac`。

使用主控提供的 `aa-v4.3.2-extract-2026-10-14.json`，未重新抓取。
抽取檔 SHA-256：`41588b6710e724f0393b1745bdfc1b468590f93d126eaf6fd81b7022485e4817`。
其中 `page_sha256` 是來源網頁位元組的雜湊，與上述 JSON 檔案雜湊不同。

- 評分由 120 筆增至 121 筆，新增 Gemini 4 Argon (High)，原始指數
  52.5605655745982，政策 `score_raw=52.56`、`score=53`、`estimated=false`。
- `claude-4-5-haiku`：16.8822352291856 → 15.410949885381；政策 16.88 / 17 → 15.41 / 15。
- `gpt-oss-20b`：9.95295342568922 → 8.9675171856126；政策 9.95 / 10 → 8.97 / 9。
- `claude-haiku-5-5-low`：29.4494981304947 → 29.4523685891139；政策仍為 29.45 / 29。
- Argon 已納入 `value` 候選池；各車道均未選中。新舊政策的 `value` 與 `lanes`
  對同一份抽取檔產生完全相同的輸出，路由、選單、README 與 SKILL 車道表不變。
- 沿用評分列的 `transport_mapping.status=unknown`，不猜測本機 `agy` 模型代號，
  候選傳輸代號為空，未加入 `build_overlay.py` 的 `PROVEN`。
  `unknown_configs` 與 `no-aa-row` 不適用，因為本列有 AA 實測評分。
- AA 別名只指向新評分列，來源標為 `scripts/aa_rebaseline.py`／
  `aa_model_not_runtime_identity`，不宣稱已登錄本機選單。

逐廠商證據由 `aa_rebaseline.py report --old <舊政策備份>` 產生：
[Gemini](aa-gemini-evidence-2026-10-14.md)、
[Claude](aa-claude-evidence-2026-10-14.md)、
[Codex](aa-codex-evidence-2026-10-14.md)、
[Grok](aa-grok-evidence-2026-10-14.md)。

測試、雜湊重現、提交狀態與限制見
[執行報告](aa-gemini-4-argon-REPORT-2026-10-14.md)。
