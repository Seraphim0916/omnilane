# AA 政策檔快照更新：2026-10-02（重新計分、Grok 4.7 low、Sonnet 5.5 low 撤下）

來源：分支 `feat/aa-2026-10-02`，從 main `7bc8f38` 開出。
本檔是政策檔 `snapshot.approval.source` 指向的摘要。

狀態：`snapshot.approval.status = approved`。
- 依主控轉述的操作者核准（2026-10-02：「新快照要做」「Sonnet 5.5 low 照你建議」「fast-agentic 要不要對調 照你建議」），`--approval` 照先例用 `approved`。
- 本分支沒有發版，也沒有重簽；合併與重簽的時間由操作者另排。

## 資料

- 抽取檔：`docs/reports/aa-v4.3.2-extract-2026-10-02.json`。
  - 2026-10-02T14:23:37+08:00 抓取，AA v4.3.2（指數版本沒變）。
  - 頁面共 689 筆，留下四家廠商 225 筆（09-30 是 684／224）。
  - 頁面 sha256：`cca072f272ee17d199f8b5867aa7b9605d42b64b6a766e7bb89d9a8243149d87`。
- 沒有跑 `fill`。
- AA 新增：`grok-4-7-low`（42.22）、`gemini-4-argon`（52.56）。
- AA 撤下：`claude-sonnet-5-5-low`。同一天七次抓取（13:45–14:23，五個不同的模型頁）都沒有這一筆。
- AA 把 GPT-6 Sol 六列標成 `deprecated: true`。OpenAI 的停用公告頁沒有列它，Codex 模型頁仍寫它可用，所以列保留。

## 政策檔

- 快照：`aa-v4.3.2-2026-09-30-v1` → `aa-v4.3.2-2026-10-02-v1`。
- 有分數的列 117 → 117（加 1、減 1）；未知列 7 → 8。各家：codex 57、claude 41 → 40、gemini 8、grok 11 → 12。
- 重新計分 12 列，都是 GPT-6 Sol 與 GPT-6 Luna；整數分數改變的 7 列：

| 列 | 09-30 | 10-02 |
|---|---|---|
| codex/gpt-6-sol-high | 42.82（43） | 42.40（42） |
| codex/gpt-6-sol-non-reasoning | 28.09（28） | 28.51（29） |
| codex/gpt-6-luna | 37.26（37） | 38.12（38） |
| codex/gpt-6-luna-xhigh | 33.88（34） | 34.56（35） |
| codex/gpt-6-luna-high | 32.15（32） | 32.93（33） |
| codex/gpt-6-luna-medium | 29.46（29） | 29.93（30） |
| codex/gpt-6-luna-low | 20.92（21） | 21.53（22） |

  其餘 105 列的分數沒變。
- 新增 `grok/grok-4-7-low`（42）。只進政策檔，不在任何車道裡；這台的 low 強度要到重簽時才探測。
- `claude/claude-sonnet-5-5-low` 移到未知列（`claude/claude-sonnet-5-5/low/adaptive`），舊分數不沿用；別名 `claude-sonnet-5-5` 的候選也拿掉它。
- `gemini-4-argon` 不納入：`agy models` 沒有列出它，而且照 `value` 的規則它不是任何車道的首選。
- `build` 連跑兩次雜湊相同。

## 腳本

- `scripts/aa_rebaseline.py build`：`NEW_ROWS` 裡的列被 AA 撤下時，原本會以「is not in the extract」結束。現在這種列留在未知列，別名也不再把它加回候選。不修這個就產不出這次的快照。
- `NEW_ROWS` 加 `grok/grok-4-7-low`。
- 沒有修的已知問題：`value` 的候選池把 Gemini 的列全部排除（模型名稱比對）。修了會改變 hard-judgment 與 ui-draft 的首選，留給主控裁定。

## 車道

`routing.yaml` 改三處，其餘車道的順序不變：

| 車道 | 改動 |
|---|---|
| triage | 拿掉 Sonnet 5.5 low（第 4 位），Sonnet 5 low 遞補 |
| bulk-mechanical | 拿掉 Sonnet 5.5 low（第 12 位） |
| fast-agentic | 第 3、4 位對調：GPT-6.1 Sol low 在前、GPT-6 Sol low 在後 |

fast-agentic 對調的原因：原本的順序讓 GPT-6.1 Sol low（42）排在 GPT-6 Sol low（34）後面，任何能到 42 的主控都先拿到 34 那一列，第 4 位永遠派不到。`value` 在 09-30 的快照就標出這個落差（上限 42–45）。

`matrix` 新舊比對（13 個主控）：triage 的 GPT-6 Luna high 分數 32 → 33；fast-agentic 在 Opus 5.5 low（42）與 Sonnet 5.5 high（47）從 GPT-6 Sol low 換成 GPT-6.1 Sol low。其餘格子相同。

`value` 新舊比對：
- hardest-coding 多一個選擇（上限 44：GPT-6 Sol xhigh）。車道沒有加這一列。
- bulk-mechanical 低段的選擇從「Sonnet 5.5 low、GPT-6 Sol low」變成「Opus 5 low、GPT-6 Luna low」。車道沒有跟著改，留給主控裁定。
- fast-agentic 上限 42–45 的四行落差消失。
- taste-final 上限 42–43 的選擇從 Grok 4.6 medium 變成 Grok 4.7 low。車道沒有放這兩列。

## 合併之後

政策檔快照換版，合併的那一刻起每台主機的派工都會被拒（`transport overlay snapshot mismatch`），直到那台跑過 `omnilane resign`。
