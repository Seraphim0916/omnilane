# AA Orchestrator：B 節實作與驗收（2026-11-13）

來源：codex-s／MacStudio 工人。工作樹 `/Users/vincentw/dev/omnilane-wt/aa-orchestrator`，基底 `f984308`。僅執行 AGY-BRIEFS.md B 節；未委派、未提交、未聯網、未重簽、未更動主安裝或主機設定。分類：工程（engineering）。

## 結論與範圍

已實作只能明確指定的 Orchestrator 與低強度上限；`routing.yaml` 原封不動。整體 AI-Native 狀態 **PARTIAL**：新增行為的離線命令列預演通過，實際供應商呼叫依任務限制未執行；原有路由測試因沙箱禁止讀取程序啟動時間失敗，完整套件亦不能視為通過。

「計分但不在車道」的選擇流程原本就成立：`scripts/dispatch.sh:567` 的 `resolve_chain` 只走明列鏈，586 行用 `OVERRIDE_MODEL` 蓋過選擇子；`scripts/lib/aa_policy.py:478` 的 `_runtime_target` 查整份 `scored_configs`，不要求模型在鏈裡。因此沒有新增選擇機制。另將 `scripts/aa_rebaseline.py:767` 的 `value_pool` 排除衍生列，避免 Argon 上限被既有家族效益池收進去。車道鏈與排序均未改。

## 分數與傳輸

Orchestrator：`score_source=proxy`，`proxy_of=[gemini/gemini-4-argon, gemini/gemini-4-hydrogen]`，max(Argon high 50, Hydrogen xhigh 54) = **54**。這是代理分數，非 AA 實測；產生器從引用列計算，不硬填 54。重建前移除舊代理值，再計算，避免快照沿用過期值。

低強度：`score_source=upper-bound`、`bound_from` 指向同廠商同型號、較高強度中最低的計分列；只用實測來源，不串接另一個上限。前提是同型號強度越低能力不會越高。這是派工上限，不是品質評分。沒有來源的未知型號仍拒絕；明確退休的列不因上限規則復活。

所有代理列均要求主機傳輸證明，真人豁免也不能跳過。`build_overlay.PROVEN` 新增 `agy-gemini-pro-orchestrator-g1-pro` 與全部上限列的探測候選，清單本身不代表已探測。決策記錄新增 `target_score_source`、`target_bound_from`、`target_proxy_of`；Orchestrator 的型號仍照實記錄，不推測內部執行者。

| 上限列 | 上限 | 來源列 |
|---|---:|---|
| `gemini/gemini-4-argon/low/unspecified` | 50 | `gemini/gemini-4-argon` |
| `gemini/gemini-4-argon/medium/unspecified` | 50 | `gemini/gemini-4-argon` |
| `claude/claude-opus-5/high/adaptive` | 44 | `claude/claude-opus-5` |
| `claude/claude-opus-5/low/adaptive` | 44 | `claude/claude-opus-5` |
| `claude/claude-opus-5/medium/adaptive` | 44 | `claude/claude-opus-5` |
| `claude/claude-opus-5/xhigh/adaptive` | 44 | `claude/claude-opus-5` |
| `codex/gpt-6-sol/high/reasoning` | 44 | `codex/gpt-6-sol` |
| `codex/gpt-6-sol/low/reasoning` | 44 | `codex/gpt-6-sol` |
| `codex/gpt-6-sol/medium/reasoning` | 44 | `codex/gpt-6-sol` |
| `codex/gpt-6-sol/xhigh/reasoning` | 44 | `codex/gpt-6-sol` |
| `codex/gpt-5.6-terra/high/reasoning` | 32 | `codex/gpt-5-6-terra` |
| `codex/gpt-5.6-terra/low/reasoning` | 32 | `codex/gpt-5-6-terra` |
| `codex/gpt-5.6-terra/medium/reasoning` | 32 | `codex/gpt-5-6-terra` |
| `codex/gpt-5.6-terra/xhigh/reasoning` | 32 | `codex/gpt-5-6-terra` |
| `grok/grok-4.6/high/reasoning` | 31 | `grok/grok-4-6-xhigh` |
| `grok/grok-4.6/low/reasoning` | 31 | `grok/grok-4-6-xhigh` |
| `grok/grok-4.6/medium/reasoning` | 31 | `grok/grok-4-6-xhigh` |
| `codex/gpt-5.6-luna/high/reasoning` | 24 | `codex/gpt-5-6-luna` |
| `codex/gpt-5.6-luna/low/reasoning` | 24 | `codex/gpt-5-6-luna` |
| `codex/gpt-5.6-luna/medium/reasoning` | 24 | `codex/gpt-5-6-luna` |
| `codex/gpt-5.6-luna/xhigh/reasoning` | 24 | `codex/gpt-5-6-luna` |
| `codex/gpt-oss-120b/low/reasoning` | 4 | `codex/gpt-oss-120b` |

## 產物與回滾

政策檔只經 `scripts/aa_rebaseline.py build` 產生。保留原本 75 列實測分數（含原始小數值），新增 1 列代理、22 列上限：98 列計分、60 列未知、1 列參考。四份廠商證據報告由 `report` 產生，明列代理／上限與來源。

- 快照：`aa-v5.0.1-2026-11-13-v2`。
- 政策 SHA-256：`fb5d05954fb69787be1c3b7f81bc44894adbfb72d67cc54636e34ff4bc7a9828`。
- 抽取檔：`docs/reports/aa-v5.0.1-extract-2026-11-13.json`，SHA-256：`6544aa0e3ae5ab5b73aeeac38a34e22290a551bf09615aea654f3bdbe73fc475`，沿用現有檔，未抓網頁。
- 五處釘值：`aa_policy.APPROVED_REGISTRY_SHA256`、`compat-entry --write` 新封存項、`transport_compat.APPROVED_ARCHIVE_SHA256`、`tests/test_origin_integration.py` 兩個受保護檔案雜湊。舊封存項保留。
- 備份：`.omnilane-tmp/aa-orchestrator-backup/` 內原始政策、相容封存與技能檔。其餘追蹤檔原始版本為 `f984308`；主控可檢查本任務差異後逐檔還原，勿覆寫其他人的後續修改。
- 文件：五份 README 新功能段、`docs/model-capabilities-2026-09.md`、`skills/omnilane/SKILL.md` 別名段、CHANGELOG 未發行段。技能車道表未加入代理列；繁中與 zh-CN 檔新增段均採繁體字，遵守本輪禁用簡體字要求。

## 驗收

基準：修改前 `python3 -m unittest tests.test_aa_policy tests.test_aa_routing_update` 執行 48 項，1 失敗、1 略過。失敗原文：

```text
omnilane: ps cannot read process start times here, so no job was started. A sandbox usually causes this; rerun outside the sandbox (from a normal terminal, or by approving the unsandboxed run).
```

未越過沙箱限制。新增測試先重現停用 `arbitrate` 車道應拒絕的預期差異，再修正驗收判準；新增測試沒有保留失敗。測試 (a)～(h) 以 `tests/test_aa_orchestrator.py` 涵蓋，另驗代理來源寫入決策紀錄、重建冪等與來源分數改變時重算。傳輸證明全部是隔離測試資料，不是主機證明。

離線實際命令列預演：Orchestrator 上限 54／57 放行，53 拒絕；無證明時含真人豁免均拒絕。Argon medium 上限 52 放行、49 拒絕。所有啟用車道均不選代理列；停用的 arbitrate 仍拒絕。未知型號仍拒絕。

最終相關測試：38 項全部通過；指定驗收組（含新增模組）56 項，僅基準既存的沙箱失敗 1 項、略過 1 項。系統 Python 指定組 48 項也只有相同失敗與略過。Python 3.9 語法檢查與 `git diff --check` 皆通過；重建位元組相同。

原始驗收命令、完整輸出與退出碼見 [驗收命令](aa-orchestrator-2026-11-13-commands.md)。完整套件原始輸出留在 `.omnilane-tmp/aa-orchestrator-full-suite.txt`。單測平行度最高 2；其餘唯讀檢查可同時進行。

工程閘：基準測試、失敗重現、離線實際預演、可重用產生器／測試、變更紀錄與備份均已執行。第二方審查未執行（工人禁止再委派）；交由主控獨立驗收。未把測試通過當成供應商實測。

## 風險與未驗證

Orchestrator 內部換模型使派工紀錄無法分辨每段執行者；帳號可能對應不同變體；看不到內部分模型用量；外層與內層兩層規劃可能重複。內部分工未經官方驗證。若本機變體日後改成 Carbon high（60），須重算代理上限，不沿用 54。

未驗證：真實帳號 Orchestrator／所有新增上限選擇子的傳輸、長任務續行與成本；本任務禁止聯網及 resign。下一步由主控在允許讀取程序資訊的環境重跑驗收，另依部署核准安排主機探測。完整套件首次結果為 **94 組通過、60 組失敗（退出碼 1）**，包含非 overlay 失敗，不符合交付驗收標準，不能宣稱完成。已修正本次新增上限造成的舊 Argon 探測清單斷言；整個 `tests.test_resign` 模組補驗 **75 項通過**，未把此結果換算成整套通過。
