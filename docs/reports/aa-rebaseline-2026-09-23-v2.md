# AA 政策檔第二次快照：GPT-6 Sol／Luna 入表，車道改以性價比重排（2026-09-23）

來源：claude-code-s／MacStudio，2026-09-23。分支 `feat/aa-gpt6-sol-luna`，工作樹 `~/dev/omnilane-wt/aa-gpt6`。
本檔是政策檔 `snapshot.approval.source` 指向的摘要。

狀態：`snapshot.approval.status = approved`。Vincent 在主控工作階段明示：「全權自己做，輪詢到完整資料之後更新 AA 政策檔以及路由重新判斷，以性價比最高為原則，把路由表再重寫一次，最後自己重簽然後發版」。

## 資料來源與缺口

- 抽取檔：`docs/reports/aa-v4.3.2-extract-2026-09-23-0903.json`
  - 2026-09-23 09:03 抓取，AA v4.3.2。
  - 頁面共 673 筆，留下四家廠商 214 筆。
- 輪詢經過：從 03:03 起每 30 分鐘抓一次，跑了 6 小時。
  - 到 09:03 時，GPT-6 Sol 和 Luna 的每題耗時、首字延遲、長上下文（`mlcrOverall`）都已經補上。
  - 仍然缺的欄位：
    - 所有新列的 GPQA。
    - Opus 5.5 全部的 `mlcrOverall`。
    - Opus 5.5 最高等級的耗時和延遲。
  - 為什麼停止輪詢：09-17 以後上榜的模型（Opus 5.5、Grok 4.7、GPT-6 Sol、GPT-6 Luna）全部都沒有 GPQA。推論是 AA 已經不替新模型跑這一項，所以不再等。
- 幻覺率：兩次抓取都沒有任何模型帶這個欄位。

## 政策檔變更

- 快照：`aa-v4.3.2-2026-09-23-v1` → `aa-v4.3.2-2026-09-23-v2`。`as_of` 維持 2026-09-23，`build --revision 2` 讓編號不會撞到上一版。
- 列數：95 → 107。
  - 新增 GPT-6 Sol、GPT-6 Luna 各 6 列：最高、極高、高、中、低、非推理。
  - 既有 95 列的分數全部沒變。
- 各家列數：codex 40→52、claude 36、gemini 8、grok 11。
- 新 12 列的 `transport_mapping` 都是未驗證。
  - 已加進 `scripts/lib/build_overlay.py` 的 `PROVEN`，`resign` 會去探測。
  - Codex 的 Pro 額度到 2026-09-26 20:00 才重置，在那之前探測不到。
  - 模型代號 `gpt-6-sol`／`gpt-6-luna` 是依 `gpt-6-astra` 的命名推的，尚未驗證。
- 雜湊：`b891f503…79cc` → `575dc3cf…6798`。`build` 連跑兩次，雜湊相同。

## 性價比規則

1. **比較基準**：每條車道只看自己那個量測。
2. **對每一個主控上限**：在派得到的列裡找出品質最好的一列，品質跟它接近的列裡挑最便宜的。
3. **「接近」的定義**：
   - 品質攸關車道：幾乎打平才算。
   - 吞吐量車道：兩倍範圍內、而且成本不到一半，也算接近。
   - 所有車道：便宜的那列在第二量測上不能差太多。
   - `fast-agentic`：首字延遲超過 10 秒的列，直接不列入候選。
4. **最高等級**：不列入候選。
5. **補充列**：依規則選出的列之後，鏈可以再加兩種列：
   - GPT-6 新列還沒驗證時，頂替它們的舊列。
   - 其他廠商的尾巴，讓只訂一家的主機也有完整的鏈。

工具：`scripts/aa_rebaseline.py value --extract docs/reports/aa-v4.3.2-extract-2026-09-23-0903.json`。
數字與各車道理由：`docs/model-capabilities-2026-09.md` 的「Current routing decision — 2026-09-23, second snapshot」一節。

## 各車道首選

| 車道 | 之前 | 現在 |
|---|---|---|
| hardest-coding | Opus 5.5 xhigh | Astra xhigh（終端機測試同分、較便宜）|
| bulk-mechanical | Astra low | Opus 5.5 medium |
| triage | GPT-5.6 Luna high | GPT-6 Luna high |
| hard-judgment | Opus 5.5 max | Opus 5.5 xhigh |
| taste-final | Opus 5.5 max | Opus 5.5 xhigh |
| ui-draft | Opus 5.5 xhigh | Opus 5.5 high |
| fast-agentic | Astra low | GPT-6 Sol high |
| long-context | Opus 5 high（Codex 列 Terra max）| Opus 5 high（Codex 列 Terra xhigh）|
| live-search | Grok 4.7（Claude 備援 Opus 5.5 medium）| Grok 4.7（Claude 備援 Opus 5.5 low）|
| consult、coding-overflow | 不變 | 不變 |

## 合併後要做的事

1. 每台主機跑一次 `omnilane resign`。
   - 重簽之前，每一次派工都會被拒。
   - Codex 額度用完時，冒煙測試會失敗；探測都通過的話，可以改用 `--no-smoke`。
2. 2026-09-26 20:00 Codex 額度重置後，再重簽一次，才能驗證 GPT-6 Sol 和 Luna。
