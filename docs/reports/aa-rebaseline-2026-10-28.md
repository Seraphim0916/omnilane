# AA v4.3.2 核准快照（2026-10-28）：登錄 agy 託管的 Claude

來源：claude-code-s／MacStudio；工作樹 `feat/agy-hosted-claude`，基底 `ce5bff1`。

狀態：**approved**。Vincent 2026-10-28 指示「omnilane走A 快速做完」（A＝把 agy 的 Claude 型號正式加進 omnilane 型號清單、測試、試派驗證、重簽），本次直接以 approved 產出，沒有先出 proposed 版。主機尚未重簽。

快照：`aa-v4.3.2-2026-10-28-v1`。
SHA-256：`312ad06ee0189b3385005ac4f223189ab3fe44382a42e20cc4267be5b07c1c3e`（`build` 連跑兩次位元組相同）。

抽取檔沿用 `docs/reports/aa-v4.3.2-extract-2026-10-27.json`，沒有重新抓 AA。

## 新增列

`agy models` 列出 `claude-opus-5-5-{low,medium,high}` 與 `claude-sonnet-5-5-{low,medium,high}`。每列沿用原生 Claude 同型號同強度的 AA 列（`aa_slug` 相同、分數相同），選擇器把強度帶在型號字串裡（`model_id_encoded_effort`），與 agy 的 Gemini 列同一形狀。

| config | agy 型號 | aa_slug | raw | score |
|---|---|---|---|---|
| gemini/claude-opus-5-5-high | claude-opus-5-5-high | claude-opus-5-5-high | 53.58 | 54 |
| gemini/claude-opus-5-5-medium | claude-opus-5-5-medium | claude-opus-5-5-medium | 51.24 | 51 |
| gemini/claude-opus-5-5-low | claude-opus-5-5-low | claude-opus-5-5-low | 42.31 | 42 |
| gemini/claude-sonnet-5-5-high | claude-sonnet-5-5-high | claude-sonnet-5-5-high | 46.75 | 47 |
| gemini/claude-sonnet-5-5-medium | claude-sonnet-5-5-medium | claude-sonnet-5-5-medium | 40.84 | 41 |
| gemini/claude-sonnet-5-5-low | claude-sonnet-5-5-low | claude-sonnet-5-5-low | 35.87 | 36 |

沒有加的：agy 沒有 xhigh／max；`gpt-oss-120b-medium` 在 AA 抽取檔沒有對應列（只有 `gpt-oss-120b` 與 `gpt-oss-120b-low`），不加。

130 → 136 scored，Gemini 9 → 15，unknown 維持 9。既有 130 列的 score、score_raw、transport_mapping 逐列比對全部相同；`routing.yaml` 沒動，新列不在任何車道，只能用 `--vendor gemini --model <agy 型號>` 指名派。

探測：`build_overlay.py` 的 `PROVEN` 加六筆（證據名 `agy-claude-{opus,sonnet}-5-5-{high,medium,low}`），重簽時會用 `agy --model <型號>` 實測；沒通過探測前一律 `transport_mapping.status=unknown`、`runtime_verified=false`，派工會被傳輸閘擋下。
