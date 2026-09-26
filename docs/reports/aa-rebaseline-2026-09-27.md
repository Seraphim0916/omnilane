# AA 政策檔快照更新：2026-09-27（0.50.0）

來源：claude-code-s／MacStudio，2026-09-27。分支 `feat/aa-0927`，工作樹 `~/dev/omnilane-wt/aa-0927`。
本檔是政策檔 `snapshot.approval.source` 指向的摘要。

狀態：`snapshot.approval.status = approved`。
- 依據一：Vincent 2026-09-23 的全權授權：「輪詢到完整資料之後更新 AA 政策檔以及路由重新判斷……自己重簽然後發版」。
- 依據二：Vincent 2026-09-26 的裁示：「用舊的做」、版號用 0.50.0。

## 資料

- 抽取檔：`docs/reports/aa-v4.3.2-extract-2026-09-27.json`。2026-09-27 抓取，AA v4.3.2，頁面共 673 筆，留下四家廠商 214 筆。
- 補回的欄位：GPT-6 Sol 中、Luna 中的每題耗時與首字延遲，共 4 項。
  - AA 從 2026-09-26 05:13 起撤掉了這幾個數字。
  - 用 `scripts/aa_rebaseline.py fill` 從 `aa-v4.3.2-extract-2026-09-23-0903.json` 補回。
  - 補了哪些欄位，列在抽取檔的 `filled_from_earlier_capture` 區塊。
- 相對 2026-09-23 09:03 的變化：
  - 107 列的智力指數全部沒變。
  - 速度類數字（每題耗時、首字延遲）AA 重新量測過。
  - Opus 5.5 最高等級補上了 `mlcrOverall`（0.667）。
- 仍然缺的欄位：
  - GPQA：09-17 以後上榜的新模型全部沒有。
  - Opus 5.5 最高等級的每題耗時與首字延遲。

## 政策檔

- 快照：`aa-v4.3.2-2026-09-23-v2` → `aa-v4.3.2-2026-09-27-v1`。
- 列數仍是 107；各家列數不變：codex 52、claude 36、gemini 8、grok 11。
- 雜湊：`575dc3cf…6798` → `6d372b25…dff7`。`build` 連跑兩次，雜湊相同。
- 釘選：`APPROVED_AS_OF` 改成 `2026-09-27`，`APPROVED_REGISTRY_SHA256` 同步更新。

## 車道

`scripts/aa_rebaseline.py value` 用 09-27 抽取檔算出來的選擇，跟用 09-23 09:03 抽取檔算的逐行相同，所以 `routing.yaml` 不動。

## 執行面（MacStudio，發版前）

- 2026-09-27 01:47 重簽，結果裝在 `resign-20260927-014704`。
  - GPT-6 Sol／Luna 10 列全部探測通過，代號 `gpt-6-sol`／`gpt-6-luna` 確認正確。
  - codex 33 列已驗證，冒煙測試通過。
- Claude 沒有重簽成功：Fable 額度用完，6 列 Fable 探測失敗，整家維持舊的釘選。已排定 2026-09-27 08:10 自動跑 `omnilane resign --vendor claude`（Fable 額度 08:00 恢復）。

## 合併後要做的事

- 每台主機跑一次 `omnilane resign`（快照換版）。
- MacMini 仍然需要在圖形終端機核准 claude／gemini／grok。
