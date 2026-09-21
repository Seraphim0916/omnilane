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

## 車道表重寫

第一版只是在舊骨架上插候選、把 Grok 4.6 換成 4.7，Vincent 指出那是「更新」不是「重寫」。這一節是第二版：
每條車道先定「這類工作看哪些量測」，候選依那些量測由強到弱排；同時沿著分數往下排，讓任何上限的主控第一個派得到的就是它派得到的最好的。
各車道逐候選的數字表在 `docs/model-capabilities-2026-09.md`，由 `scripts/aa_rebaseline.py lanes` 產生，不是手抄。

第一版漏看的事實：

- AA 在 v4.3 把編碼評測換成 Terminal-Bench 4.0。2.1 已經飽和（頂尖模型都在 0.87–0.91），4.0 才拉得開（0.2–0.6）。
  第一版拿 2.1 當依據，還因此寫了「Grok 4.7 沒有編碼成績」——錯的，4.0 有：4.7 high 0.247、4.6 high 0.212。
- Fable 5.1 max 在 4.0 上輸給自己的 xhigh（0.520 對 0.551），每次評測花費多約 45%。Astra max 對 xhigh 也是持平。兩個 max 都不進車道鏈。
- Astra low 全面壓過 Sol high：兩項編碼都較高、幻覺率 0.469 對 0.912、每題 1.3 分鐘對 3.2 分鐘，花費相當。
- 長上下文：AA-LCR 飽和；`mlcrOverall` 上 Opus 5 各等級 0.54–0.59，Gemini Flash high 0.217、Terra max 0.317。
  （`mlcrOverall` 在頁面資料裡沒有說明文字，解讀為較難的長上下文評測，**未驗證**。）
- Haiku 在 AutomationBench 只有 0.032，卻掛在 `fast-agentic` 備援；Flash low 0.365，遠低於 Flash medium 0.609。
- AA Briefcase（專家評文件產出）：Opus 5 max 的呈現分數最高、整體與 Fable 持平；Grok 4.7 high 整體 1644，高於所有 Astra 列（xhigh 1544）。

| 車道 | 0.44.0 首選 | 現在首選 | 依據 |
|---|---|---|---|
| hardest-coding | Fable 5.1 max | **Astra xhigh** | Terminal-Bench 4.0 最高、頂層中幻覺率最低；Fable xhigh 居次 |
| bulk-mechanical | Sol high | **Astra low** | 同價位下編碼、速度、幻覺率全勝 |
| triage | Luna high | Luna high | 每次執行成本最低；Claude 備援改 Sonnet 5 low 在前、Haiku 在後 |
| hard-judgment | Fable 5.1 xhigh | Fable 5.1 xhigh | HLE、CritPt 領先；Astra xhigh 為獨立第二意見；中段改用 Opus（分析品質不隨努力等級崩落） |
| taste-final | Fable 5.1 xhigh | **Opus 5 max** | Briefcase 呈現分數最高；Grok 4.7 排到 Astra 前面 |
| consult | Astra xhigh | Astra xhigh | 各家最強的一般可達設定；Grok 換 4.7，4.6 墊後 |
| ui-draft | Sol high | **Astra high** | MMMU-Pro（看圖）其次 Terminal-Bench 4.0；Fable 無看圖數據，不列 |
| long-context | Flash medium | **Opus 5 high → medium → low** | `mlcrOverall` 大幅領先，低努力等級仍保持 |
| fast-agentic | Flash low | **Astra low** | AutomationBench 接近頂端且最快；Flash 改用 medium；移除 Haiku |
| live-search | Grok 4.6 | **Grok 4.7** | 唯一原生 X 來源；備援用 Opus 5 medium（Sonnet 的 omniscience 為負） |
| coding-overflow | Grok 4.6 | **Grok 4.7** | Terminal-Bench 4.0 與 SciCode 皆略高 |

實測（乾淨沙箱家目錄、真實 `dispatch.sh --dry-run`、各主控用 `--caller-context` 模擬；`--validate` 0 FAIL）：

| 主控（上限） | hardest-coding | hard-judgment | taste-final | bulk-mechanical | long-context | fast-agentic |
|---|---|---|---|---|---|---|
| Fable 5.1 xhigh（53） | Astra xhigh 1／11 | Fable xhigh 1／9 | Opus 5 max 1／8 | Astra low | Opus 5 high | Astra low |
| Fable 5.1 high（51） | Astra high 3／11 | Opus 5 max 3／9 | Opus 5 max 1／8 | Astra low | Opus 5 high | Astra low |
| Fable 5.1 medium（49，本工作階段） | Opus 5 high 6／11 | Opus 5 high 6／9 | Opus 5 high 6／8 | Astra low | Opus 5 high | Astra low |
| Opus 5 medium（45） | Sol xhigh 8／11 | Grok 4.6 8／9 | Grok 4.6 7／8 | Sol high 2／4 | Opus 5 medium 2／5 | Flash medium 2／4 |

`live-search` 與 `coding-overflow` 在四種主控下都落在第 2 個候選 Grok 4.6：4.7 尚未在本機驗證，被跳過；重簽探測通過後自動換成 4.7。
49 分主控在 `taste-final` 重簽後會改拿 Grok 4.7 high（第 4 個候選），它的 Briefcase 整體分數高於 Opus 5 high。

**重簽前的已知缺口**：帶 `--vendor grok` 時任何車道都會被拒（`runtime-mapping-unverified`）：指定廠商時只取該家第一段、被拒不往下找，而第一段現在是 4.7。
在那之前加 `--model grok-4.6`。

**0.44.0 裡一個派不出去的備援**：`claude claude-sonnet-5 high` 在 v4.2 政策檔下只對得到「非推理」列，本機 overlay 只驗證了 Sonnet 5 的 max 列，
所以 0.44.0 的 `bulk-mechanical` 與 `live-search` 第三順位一直派不出去。新車道表不再用它。

**本機覆寫檔**：`~/.omnilane/routing.local.yaml` 釘了 live-search／hardest-coding／hard-judgment／taste-final 四條（2026-09-03「Codex 排第一以省 Claude 額度」）。
Vincent 2026-09-22 裁示：SKILL.md 已規定目標屬於自家就優先用原生子代理，這個前提不再需要。新的 `hardest-coding` 依數據就是 Astra 領先；其餘三條回到依數據排序。

## 驗證

| 項目 | 結果 |
|---|---|
| `python3 -m unittest discover`（3.14） | 400 個，1 失敗（見下） |
| `/usr/bin/python3`（3.9.6）discover | 400 個，1 失敗（同一個），16 略過 |
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
3. `~/.omnilane/routing.local.yaml` 釘了四條車道，本機以它為準；不移除的話新車道表在這四條上不會生效（見上一節「本機覆寫檔」）。
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
