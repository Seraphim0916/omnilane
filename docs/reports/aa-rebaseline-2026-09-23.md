# AA 政策檔補列 Claude Opus 5.5，並把它排進車道（2026-09-23）

來源：claude-code-s／MacStudio，2026-09-23。分支 `feat/aa-opus-5-5`，工作樹 `~/dev/omnilane-wt/aa-opus55`。
狀態：`snapshot.approval.status = approved`。Vincent 於 2026-09-23 在主控工作階段明示「核准政策檔，改成已核准」；以 `--approval approved` 重建，除 `approval.status` 與 `approval.estimated_scores` 兩欄外與 proposed 版（`ca1cdfe0…5aaf`）逐列相同。
本檔是政策檔 `snapshot.approval.source` 指向的摘要。逐列證據、抽取來源與雜湊重現紀錄在
[`aa-opus-5-5-evidence-2026-09-23.md`](aa-opus-5-5-evidence-2026-09-23.md)。

## 為什麼這次只補列、不換量尺

AA 仍是 Intelligence Index v4.3.2，與前一快照 `aa-v4.3.2-2026-09-22-v1` 同版。以 2026-09-23 的抽取檔
（`docs/reports/aa-v4.3.2-extract-2026-09-23.json`，頁面 661 筆、留四家 202 筆）重新計分，既有 83 列的
`score`、`score_raw`、`estimated` 全部不變，所以不需要整份重拍，只多出 AA 新增或先前未收的列。

## 政策檔變更

- 快照 `aa-v4.3.2-2026-09-22-v1` → `aa-v4.3.2-2026-09-23-v1`；`as_of` 2026-09-23。
- 83 列 → 95 列；估計值 23 → 26；各家 codex 34→40、claude 31→36、gemini 7→8、grok 11 不變。
- 新增 Claude Opus 5.5 五列：max 58、xhigh 56、high 54、medium 51、low 42（皆為實測，非估計）。
- 另外 7 列（gpt-5.3-codex、gpt-5.5-instant、gemini-3.5-flash-lite、gpt-oss-120b 高／低、gpt-oss-20b 高／低）
  只為讓以它們執行的主控有上限可查；不進任何車道鏈，也不進 `omnilane configure` 選單。
- 新 12 列的 `transport_mapping` 全部未驗證，不宣稱任何 CLI 叫得到。
- 雜湊：舊 `a1109913…5fdd` → proposed 版 `ca1cdfe0…5aaf` → 核准版 `b891f503…79cc`；`scripts/lib/aa_policy.py` 的釘選已同步到核准版。

## 車道

依各車道自己的第一量測排入 Opus 5.5；各候選的數字表在 `docs/model-capabilities-2026-09.md`，
由 `scripts/aa_rebaseline.py lanes --extract docs/reports/aa-v4.3.2-extract-2026-09-23.json` 產生。

| 車道 | 變更 | 依據 |
|---|---|---|
| hardest-coding | Opus 5.5 xhigh 第 1、high 第 2；medium 插在 Astra high 與 Fable high 之間 | Terminal-Bench 4.0：xhigh 0.596 與 Astra xhigh 同分、SciCode 0.650 領先；max 同為 0.596，不放；medium 0.525 |
| hard-judgment | Opus 5.5 max 第 1、xhigh、high；medium 插在 Opus 5 max 之後 | HLE：max 0.614 明顯高於 xhigh 0.575；Briefcase 分析 Elo 2207 領先；medium 0.547 |
| taste-final | Opus 5.5 max、xhigh、high 排在 Opus 5 max 之前；medium 不放 | Briefcase 整體 Elo 1822／1780／1704，高於其他所有列 |
| ui-draft | Opus 5.5 xhigh 第 1；high 在 Astra high 之後 | MMMU-Pro 0.866 領先，Terminal-Bench 4.0 0.596 |
| consult | Claude 格由 Fable 5.1 xhigh 換成 Opus 5.5 xhigh | 指數 56.0 對 53.2 |
| live-search | Claude 備援由 Opus 5 medium 換成 Opus 5.5 medium | omniscience 40.3 對 31.0；幻覺率未公布 |

`bulk-mechanical`、`fast-agentic`、`long-context`、`triage`、`coding-overflow`、`arbitrate` 不動。

注意：2026-09-23 抽取檔沒有任何模型的幻覺率欄位，Opus 5.5 的幻覺率在兩次抓取都沒出現。
車道說明裡涉及幻覺率的地方，數字來自 2026-09-22 lane-metrics 抽取檔的 `supplement`。

## 執行面：本機重簽前的行為

`scripts/lib/build_overlay.py` 的 `PROVEN` 加入 Opus 5.5 五列（證據檔名 `cl-claude-opus-5-5-{effort}`），
`omnilane resign` 才會探測它們。重簽之前，本機 overlay 沒有 Opus 5.5 的驗證，車道會跳過這些列。
用既有探測證據在沙箱重建新快照 overlay（58 列已驗證、8 列未證：Opus 5.5 五列「本機尚未探測」＋既有 gpt-5.4-mini 三列），
`dispatch.sh --dry-run --caller-context` 實測：

| 主控（上限） | hardest-coding | hard-judgment | taste-final |
|---|---|---|---|
| Opus 5.5 xhigh（56） | Astra xhigh 3／14 | Fable 5.1 xhigh 4／13 | Opus 5 max 4／11 |
| Opus 5.5 medium（51） | Astra high 5／14 | Opus 5 max 6／13 | Opus 5 max 4／11 |

依鏈序推算（未實測，要重簽後才能驗）：重簽並探測通過後，xhigh 主控在三條車道都會改拿 Opus 5.5（依上限：hardest-coding 的 xhigh、hard-judgment 與
taste-final 的 xhigh，因 max 58 高於上限 56）；medium 主控的結果不變（51 分以上的 Opus 5.5 列本來就搆不到）。

## 合併前後必須由 Vincent 做的事

1. ~~核准政策檔~~：已於 2026-09-23 核准。
2. 合併並安裝後，**重簽完成前每一次派工都會被拒**（`transport overlay snapshot mismatch`）；請緊接著跑 `omnilane resign`。
3. MacMini 同樣要 pull＋重簽。
