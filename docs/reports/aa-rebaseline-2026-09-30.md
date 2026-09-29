# AA 政策檔快照更新：2026-09-30（Claude Sonnet 5.5、GPT-6.1 Sol）

來源：分支 `feat/aa-sonnet-5-5`，工作樹 `~/dev/omnilane-wt/aa-sonnet55`，從 main `1230085` 開出。
本檔是政策檔 `snapshot.approval.source` 指向的摘要。

狀態：`snapshot.approval.status = approved`。
- 依主控的派工指示，`--approval` 照先例（`aa-v4.3.2-2026-09-27-v1` 是 `approved`）。
- 本分支沒有發版，也沒有重簽；合併與發版前由操作者確認。

## 資料

- 抽取檔：`docs/reports/aa-v4.3.2-extract-2026-09-30.json`。
  - 由主控在 2026-09-30 03:26 +08:00 抓取，AA v4.3.2。
  - 頁面共 684 筆，留下四家廠商 224 筆。
- 新模型：
  - Claude Sonnet 5.5：max、xhigh、high、medium、low 五列，全部是 adaptive reasoning。
  - GPT-6.1 Sol：max、xhigh、high、medium、low 五列。
  - 兩者 AA 都沒有 non-reasoning 列。
- 沒有跑 `fill`。AA 又沒列 GPT-6 Sol、Luna 中的每題耗時與首字延遲，這次沒有補回（車道的影響見下）。
- 仍然缺的欄位：
  - GPQA：09-17 以後上榜的新模型全部沒有。
  - 幻覺率：所有模型都沒有。
  - Sonnet 5.5 沒有 MMMU-Pro。
  - Sonnet 5.5 與 GPT-6.1 Sol 都沒有 `mlcrOverall`。

## 政策檔

- 快照：`aa-v4.3.2-2026-09-27-v1` → `aa-v4.3.2-2026-09-30-v1`。
- 列數 107 → 117。各家列數：codex 52 → 57，claude 36 → 41；gemini 8、grok 11 不變。
- 舊的 107 列分數與身分欄位全部沒變（逐列比對 score、score_raw、estimated 與身分欄位，0 處不同）。
- 新列分數（score_raw 四捨五入，進位規則照 registry 既有的 half-up）：

| config | effort | raw | score |
|---|---|---|---|
| claude/claude-sonnet-5-5 | max | 55.98 | 56 |
| claude/claude-sonnet-5-5-xhigh | xhigh | 51.85 | 52 |
| claude/claude-sonnet-5-5-high | high | 46.74 | 47 |
| claude/claude-sonnet-5-5-medium | medium | 40.74 | 41 |
| claude/claude-sonnet-5-5-low | low | 35.84 | 36 |
| codex/gpt-6-1-sol | max | 51.83 | 52 |
| codex/gpt-6-1-sol-xhigh | xhigh | 51.04 | 51 |
| codex/gpt-6-1-sol-high | high | 50.24 | 50 |
| codex/gpt-6-1-sol-medium | medium | 47.78 | 48 |
| codex/gpt-6-1-sol-low | low | 42.08 | 42 |

- 模型代號：
  - Sonnet 5.5 是 `claude-sonnet-5-5`。
  - GPT-6.1 Sol 是 `gpt-6.1-sol`，比照 `gpt-5.6-sol` 的寫法，config id 是 `codex/gpt-6-1-sol*`。
  - 兩者都還沒在任何主機探測過。
- 雜湊：`6d372b25…dff7` → `428657f3…1214`。`build` 連跑兩次（一次以舊快照為底、一次以新快照為底），雜湊相同。
- 釘選：`APPROVED_AS_OF` 改成 `2026-09-30`，`APPROVED_REGISTRY_SHA256` 同步更新。
- `build_overlay.py` 新增要探測的列：
  - `claude-sonnet-5-5` 五列，比照 `claude-opus-5-5`。
  - `gpt-6.1-sol` 五列，比照 `gpt-6-sol`。
- 沒有新增 catalog 別名列：`scripts/configure.sh` 的選單沒有動，`claude-opus-5-5` 也沒有別名列。

## 車道

`scripts/aa_rebaseline.py value` 用 09-30 抽取檔算出的選擇，有六條車道的鏈變了，`triage` 則是手動改。
新模型取代的舊列放在新列後面當替補，直到主機用 `omnilane resign` 驗證新模型為止。

- `hardest-coding`：GPT-6.1 Sol xhigh、high、medium 成為 Astra xhigh 以下的各級首選；Sonnet 5.5 high、medium 補進 47、41 兩級。
- `bulk-mechanical`：GPT-6.1 Sol medium 領頭；GPT-6.1 Sol low 取代原本的低價快速級；Sonnet 5.5 medium、low 是快速的 Claude 列。
- `triage`：Sonnet 5.5 low 分數比 Sonnet 5 low 高、價格更低，取代它當 Claude 列。
- `hard-judgment`：value 的選擇沒變；GPT-6.1 Sol xhigh 在 HLE 與 Astra high 近乎平手、價格約四分之一，改當 Codex 列。
- `taste-final`：Sonnet 5.5 xhigh 評分高於 Opus 5.5 high；Sonnet 5.5 high 與 Opus 5.5 medium 近乎平手而且較便宜。
- `ui-draft`：GPT-6.1 Sol high、medium、low 是 Opus 5.5 high 以下的首選。Sonnet 5.5 沒有 MMMU-Pro，所以不放。
- `fast-agentic`：GPT-6.1 Sol medium 領頭，Astra medium 當替補；Sonnet 5.5 medium 取代 Opus 5.5 low 當 Claude 列。
  - 下面兩點跟新模型無關，是這次抽取檔本身造成的：
    - GPT-6 Sol high 的首字延遲變成 15 秒，超過 10 秒上限。
    - GPT-6 Sol medium 沒有速度數字。
  - 所以這兩列移出這條車道。
- 不變：`consult`、`long-context`、`live-search`、`coding-overflow`。

若用 09-27 抽取檔 `fill` 補回 GPT-6 Sol medium 的速度，`value` 只會在 `bulk-mechanical` 尾端與 `fast-agentic` 多列出 GPT-6 Sol medium，而且排在 GPT-6 Sol low 之後，對已驗證主機的實際選擇沒有影響。

## 合併後要做的事

- 每台主機跑一次 `omnilane resign`（快照換版，並探測 Sonnet 5.5 與 GPT-6.1 Sol 共 10 列）。
- 確認 `gpt-6.1-sol` 是 Codex 實際接受的模型代號；如果探測失敗，要修正 registry 的 `model` 欄與 `build_overlay.py`。
