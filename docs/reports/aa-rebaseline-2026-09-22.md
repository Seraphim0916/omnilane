# AA 政策檔重拍到 Intelligence Index v4.3.2，並重新分配車道（2026-09-22）

來源：claude-code-s／MacStudio，2026-09-22。分支 `feat/aa-v4.3.2-rebaseline`，工作樹 `~/dev/omnilane-wt/aa-v432`。
狀態：Vincent 於 2026-09-22 裁示「發新版、路由重寫、加入 Grok 4.7、用新版政策表」，`snapshot.approval.status` 已設為 `approved`，
雜湊釘已對到核准後的檔案。隨 0.45.0 發版。

## 為什麼不能只加一列

Artificial Analysis 從 v4.2 換到 v4.3.2，兩個量尺不能互換，各家落差也不等：Fable 5.1 max 57→53、Astra xhigh 54→52、
Opus 5 medium 50→45、Grok 4.6 high 51→44、Sol high 48→42、Gemini 3.8 Flash high 47→41。
把 Grok 4.7（v4.3.2 的 46）插進 v4.2 的表，會排在 Grok 4.6（51）之下，方向就錯了。

## 怎麼做的

`scripts/aa_rebaseline.py`：`fetch` 抓一頁 AA 模型頁並存成抽取檔；`build` 只讀抽取檔重產政策檔（不再連網，
所以證據與政策檔一致）；`report` 產各家證據表；`matrix` 出可達性矩陣。
抽取檔 `docs/reports/aa-v4.3.2-extract-2026-09-22.json`（頁面 655 筆、留四家 197 筆，含頁面 sha256）。
取整規則寫進 `schema_notes.score_rounding`：`score_raw`（兩位小數）四捨五入（half-up）成整數；每列都帶 `score_raw`。
v4.2 那版的取整規則沒有留下文字紀錄，無從比對。

## 政策檔變更

- 78 列 → 83 列。估計值 48 筆 → 23 筆（25 筆變成實測）。各家：codex 34、claude 31、gemini 7、grok 11。
- 新增：`grok/grok-4-7`（xhigh，46）、`grok/grok-4-7-high`（46）；
  `claude/claude-sonnet-5-xhigh`／`-high`／`-medium`／`-low`（34／32／28／24，AA 現在有分數，原列在 unknown）。
- 移出：`codex/gpt-6-astra-non-reasoning`——AA 已不列，移到 `unknown_configs`，舊分數不沿用。
  `build_overlay.py` 原註解即記載 Astra 拒絕 effort none，此列本來就沒有可用的選擇器。
- 整數化後的同分群：Fable 5.1 max＝Fable 5.1 xhigh＝Astra max＝53。政策允許同分派工，
  所以 Fable 5.1 xhigh 主控現在派得到 Fable 5.1 max 與 Astra max（v4.2 下派不到）。
- 各家逐列新舊分數：`docs/reports/aa-{codex,claude,gemini,grok}-evidence-2026-09-22.md`。

## 換版對主控上限的影響（同一份舊車道表）

v4.2 下，Fable 5.1 medium／low、Opus 5 medium、Astra low、Sol high 在 `hard-judgment` 是「無可派」，
`hardest-coding` 只剩第 4 順位的 Flash。v4.3.2 下這些主控都派得到 Grok 4.6（44）。換版本身對中階主控是改善。

## 車道重新分配

分項指標取自同一份抽取檔。重點依據：Terminal-Bench 2.1——Fable 5.1 max 0.914、Astra high 0.899（不低於 xhigh 的 0.891）、
Grok 4.6 high 0.884、Opus 5 high 0.876、Flash high 0.876；Grok 4.7 **沒有** Terminal-Bench 結果。

Claude 第三順位的兩個候選（同一份抽取檔）：

| 設定 | 指數 | Terminal-Bench 2.1 | 每題輸出 token | 首個答案 token | 混合價（美元／百萬） |
|---|---|---|---|---|---|
| Sonnet 5 high | 31.7 | 無資料 | 44,341 | 11.7 秒 | 1.54 |
| Opus 5 medium | 44.8 | 0.861 | 28,977 | 4.6 秒 | 3.85 |

Opus 5 medium 分數高 13 分、比較快、用的 token 較少，單價是 2.5 倍。

| 車道 | 原本 | 現在 | 理由 |
|---|---|---|---|
| hardest-coding | Fable max → Astra xhigh → Grok 4.6 → Flash high | Fable max → Astra xhigh → **Astra high → Opus 5 high** → Grok 4.6 → Flash high | 52 與 44 之間原本是空的；中階主控一掉就到尾段。Astra high 是實力候選（0.899）；Opus 5 high 的 Terminal-Bench 與 Flash high 同為 0.876、單價約 6.7 倍，放它是為了補 48 分這一階的上限覆蓋，不是因為編碼較強——只留 Astra high 也說得通。Grok 留在有編碼實測的 4.6 |
| hard-judgment | Fable xhigh → Astra xhigh → Grok 4.6 | Fable xhigh → Astra xhigh → **Astra high → Opus 5 high → Grok 4.7 high** → Grok 4.6 | 同上；Grok 4.7 指數高 2 分，排在 4.6 前 |
| taste-final | …→ Grok 4.6 → Flash high | 同 hard-judgment，尾段保留 Flash high | 同上 |
| live-search | Grok 4.6 → Flash high → Sonnet 5 high → off | **Grok 4.7 high** → Grok 4.6 → Flash high → Sonnet 5 high → **Opus 5 medium** → off | 一般網頁搜尋用不到 45 分，便宜的 Sonnet 5 high 留著；它在尚未驗證的主機上會被跳過，由 Opus 5 medium 接手 |
| bulk-mechanical | Sol high → Flash high → Sonnet 5 high | Sol high → Flash high → **Opus 5 medium** | 依上表：分數、速度、token 都是 Opus 5 medium 佔優，代價是單價 2.5 倍。要省錢可改回 Sonnet 5 high（重簽後可用）——由 Vincent 決定 |
| consult、coding-overflow | Grok 4.6 | 不變 | `--vendor` 只取該家第一段、被拒不往下；4.7 沒有編碼實測 |
| triage、ui-draft、long-context、fast-agentic | — | 不變 | 新數據沒有改變排序 |

**發現的既有問題**：`claude claude-sonnet-5 high` 在 v4.2 政策檔裡只對得到「非推理」列，而本機 overlay 只驗證了 Sonnet 5 的 max 列，
所以 `bulk-mechanical` 與 `live-search` 的第三順位在 v4.2 下一直派不出去。本分支已把 Sonnet 5 的 adaptive high 列加進政策檔與探測清單，
重簽探測通過後它就能用；在那之前仍會被跳過。

**重簽前的已知缺口**：帶 `--vendor grok` 派 `hard-judgment`／`taste-final`／`live-search` 會失敗。`resolve_chain` 在指定廠商時只取該家第一段
（現在是尚未驗證的 `grok-4.7 high`），被拒就回傳失敗、不往下找 4.6。不帶 `--vendor` 的一般派工不受影響。

**未驗證的候選會被跳過**：`dispatch.sh` 的 `resolve_chain` 對政策拒絕的候選（超上限或傳輸未驗證）一律 `continue`。
實測（沙箱 overlay、真實 dispatch、`--dry-run`）：`live-search` 落在候選 2／6 的 `grok-4.6 high`。
所以 Grok 4.7 排在前面是安全的，等本機重簽探測通過後自動生效。

主控為 Fable 5.1 medium（49）時，11 條車道的 `--dry-run` 全數成功：
`hardest-coding`／`hard-judgment`／`taste-final` → Opus 5 high（候選 4）；`live-search` → Grok 4.6（候選 2／6）；其餘維持首選。

## 驗證

| 項目 | 結果 |
|---|---|
| `python3 -m unittest discover`（3.14） | 395 個，1 失敗（見下） |
| `/usr/bin/python3`（3.9.6）discover | 395 個，1 失敗（同一個），16 略過 |
| `tests/run.sh` | 126 通過、1 失敗（同一個） |
| 3.9 語法解析＋匯入 `aa_rebaseline.py` | 通過 |
| `scripts/release-audit.sh`（乾淨工作樹） | PASS |
| 以既有探測證據在沙箱重建 overlay | 52 列已驗證、9 列未證（新 6 列「本機尚未探測」＋既有 3 列 gpt-5.4-mini） |

唯一失敗：`test_live_overlay_loads_and_verifies_every_unstale_mapping`。它直接讀 `~/.omnilane/transport-contracts.local.json`，
要求「已驗證＋未證」筆數等於 PROVEN 筆數。本機那份還是 v4.2 快照，重簽後才會過。這是閘門正常運作，不是回歸。
上表測試都以 `OMNILANE_HOME=<沙箱>` 執行，沒有碰現行 overlay。

## 合併前後必須由 Vincent 做的事

1. `grok login`（CLI 目前顯示未登入）。沒登入的話重簽探測不到 Grok 4.7，車道會繼續用 4.6。
2. 合併並安裝到 `~/dev/omnilane` 之後，**在重簽完成前每一次派工都會被拒**（`transport overlay snapshot mismatch`）。
   請緊接著跑 `omnilane resign`；它會沿用舊證據重建 overlay，並探測新加的 Grok 4.7 與 Sonnet 5 各列。
3. `~/.omnilane/routing.local.yaml` 釘了 `live-search` 與 `hard-judgment` 用 `grok-4.6 high`，本機以它為準；
   repo 的新車道表在這兩條上不會生效，要不要改由 Vincent 決定。
4. MacMini 同樣要 pull＋重簽。

## 隨這一版一起出的同步

- 車道表：`skills/omnilane/SKILL.md`（車道表、各主控註記、模型別名；＝六面部署）、五語 README、
  `docs/model-capabilities-2026-09.md`（新增 2026-09-22 一節，含各候選的分項數字與排序理由）。
- `CHANGELOG.md` 0.45.0、五語 README 的版本說明、`VERSION`／`package.json`／`plugin.json`／`.claude-plugin/*` 升 0.45.0。
- `scripts/configure.sh` 的 Grok 選單加入 `grok-4.7`，政策檔因此有對應別名。
  `docs/aa-model-coverage-2026-09-05.json` 以既有的 `no-aa-row` 狀態登錄它：那份盤點拍攝於 v4.2、早於 4.7 上架，
  643 列裡沒有這個模型，分數另見 v4.3.2 政策檔。盤點本身沒有重做。

已查過不用改：README、`docs/native-executor.md`、`docs/completion-wakeup.md` 裡的 `2026-09-07` 都是歷史敘述或協定版本，與快照無關。
- `docs/reports/aa-*-2026-09-07.md` 與 `docs/model-governance-proposal.md` 仍未進版控（v4.2 快照的證據）。
  建議一併提交作為前一版的紀錄；由 Vincent 決定。
