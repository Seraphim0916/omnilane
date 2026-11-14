<!-- source: codex-s / MacStudio; OMNILANE worker; 2026-11-13; task 063-FIX1 -->
# 0.63.0 Orchestrator FIX1 修正紀錄

工作目錄：`/Users/vincentw/dev/omnilane-wt/aa-orchestrator`。未提交、未推送、未執行正式 resign、未改正式安裝、主機證明、routing.yaml 或政策 JSON。全程離線；測試平行度不超過 2。

## 一、根因與修法

`git diff f984308 -- scripts/lib/aa_policy.py` 顯示 `_requires_human_transport_proof` 新增 `and not row.get("score_source")`（現第 574 行），衍生列因此連真人主張也要傳輸證明。既有生命週期測試使用 `claude-opus-5 high`，現在該列是上限列；測試沒有離線傳輸證明，在假執行器啟動前即被擋。此函式按型號選擇器判斷，故只把同型號強度改成 max 也不足以避開該證明需求。

修法：四組生命週期測試改用沒有衍生列的既有實測型號 `claude-opus-5-5 high`；Grok 模式／自動模式測試改用 `grok-4.7`。這些測試驗的是程序與模式，不是模型傳輸證明。沒有修改或放寬正式傳輸閘門，既有無證明拒絕測試仍保留。

直接命令列對照（`.fix1-evidence/fixture-selector-cli.txt`）：相同離線真人主張輸入，舊 Claude／Grok 選擇器皆 exit 3、`runtime-mapping-unverified`、`target-transport`；只換新選擇器，皆 exit 0。此入口只作政策判斷，不啟動供應商。

`git diff f984308 -- scripts/lib/build_overlay.py` 第 74–85 行新增 Orchestrator 代理與上限列探測候選。Gemini 多了 Argon low、Argon medium、Orchestrator 三筆，總數 17→20；六筆故障維持不變，成功數 11→14、單筆故障後成功數 16→19。`tests/test_resign_per_selector.py` 校正上述固定期待值，保留故障種類、證明內容與縮減判定。

補齊同次改版造成的另外四項期待：

- `test_worker_no_nested.py` 的假 Codex 目標改為實測 `gpt-6-luna`；`test_runner_claude_env.py` 改用 `grok-4.7`，保持首則訊息與環境隔離的原有斷言。
- `test_aa_unmapped_model.py` 明確區分 Argon high 的實測列與 low／medium 上限列，逐列驗來源、分數、無授權、無傳輸證明及不進自動選池。
- `test_aa_withdrawn_rows.py` 保留原實測 ID 消失、重建冪等與後續恢復的檢查；撤回實測後可由更高強度推得上限，但須使用衍生 ID、無派工權限、無傳輸證明、不進自動選池。

## 二、F-02：拒絕衍生列取得派工權限

選擇「拒絕」，沒有將明確的低強度派工方冒充為同型號較高強度的實測列。上限不能證明派工方至少具備該分數；代理型號也可能根本沒有自身實測列。

- `scripts/lib/aa_policy.py:467`：共用 `authority_eligible`，拒絕 `authority_eligible=False` 或有 `score_source` 的列。
- `scripts/lib/aa_policy.py:691`、`:871`：一般派工與原生繼承在計算上限前即拒絕，回 `ineligible-caller-config`；不產生子脈絡或有效上限。
- `scripts/lib/caller_identity.py:188`、`:203`、`:675`：解析入口拒絕衍生列；既有強度未確認的地板僅從可授權實測列選取；寫檔入口在建立目錄前再次拒絕。
- `tests/test_aa_orchestrator.py:99`：涵蓋全部 23 個衍生列的一般派工、繼承、解析、地板與寫檔負向回歸；包含審查的 Argon low→high 情境。

先紅後綠：新增兩個測試函式，改前 46 個子情境失敗；改後通過。證據：`.fix1-evidence/red.txt`、`green.txt`。

## 三、F-03

`tests/test_aa_orchestrator.py:150` 改為 `Path(d).resolve()`，讓建置輸出路徑與暫存根使用同一個實體路徑。

## 四、F-04

可選項本輪未改；`aa_rebaseline.py` 退回 unknown 的備註修復仍保留為未處理項。本輪未重新產生政策檔。

## 驗證與限制

AI-Native：**PARTIAL**。核心修正及離線命令列已驗證，程序生命週期尚未通過沙箱外驗收。

政策 SHA256（改前、改後相同）：
```text
fb5d05954fb69787be1c3b7f81bc44894adbfb72d67cc54636e34ff4bc7a9828  config/aa-model-policy.json
```

任務指定指令皆已執行，完整輸出在 `.fix1-evidence/`：

| 指令／輸出檔 | 實際輸出摘要 |
|---|---|
| `python3 -m unittest tests.test_aa_policy tests.test_aa_routing_update tests.test_aa_orchestrator` → `verify-policy.txt` | `Ran 58 tests in 11.779s` / `FAILED (failures=1, skipped=1)` |
| 任務指定的 11 個程序／回歸測試模組 → `verify-workers.txt` | `Ran 186 tests in 970.005s` / `FAILED (failures=114, errors=22)` / `PASS: 25 policy cases; no provider invocation; no job state` |
| `/usr/bin/python3 -m unittest tests.test_aa_policy tests.test_aa_orchestrator` → `verify-system-python.txt` | `Ran 47 tests in 6.886s` / `OK (skipped=1)` |
| 指定 Python 3.9 `ast.parse` 迴圈 | 無輸出，exit 0 |
| `git diff --check && git status --short` → `git-status.txt` | 差異檢查 exit 0；保留既有未提交內容，另有本輪 14 檔與報告／證據 |

補修後定點驗證：
```text
verify-selectors.txt: Ran 42 tests in 35.521s / OK
verify-caller.txt: Ran 13 tests in 0.051s / OK
verify-followups.txt: Ran 46 tests in 25.781s / FAILED (failures=3)
```
最後一組只剩 `LiveFirstMessageCarriesWorkerRules.test_first_message_is_rules_then_task` 的 claude／codex／gemini 三個子情境，全部明示 `ps cannot read process start times here`。

重要時間界線：指定長測試在最後四處補修前已載入全部測試模組，因此其完整輸出仍含舊 Codex／Grok 目標、撤回列與 Argon 列期待錯誤。四處補修後已重跑完整相關四模組（46 項），未再重跑耗時的 11 模組長測試。不能把兩輪合併宣稱整套通過。

沙箱失敗清單：
- `test_aa_routing_update...test_direct_api_refuses_work_before_provider_call`：直接回報 `ps cannot read process start times here`。
- `test_fifo_open_eintr`：83 個失敗／錯誤紀錄；包括啟動時 `could not read the start time ... with ps`、清理直接 `PermissionError: ... 'ps'`，及未出現啟動／持有者標記的連鎖失敗。
- `test_job_worker_close`：30 個失敗／錯誤紀錄；包含 `ps` 拒絕啟動、就緒點未到與輸出檔缺失。額外定點重現 `test_idle_cap_without_result_fails`：先收到 exit 1 和 `ps` 拒絕，再因無 out.txt 出錯，見 `secondary-failure.txt`。
- `test_mailbox_descriptor_lifecycle`：8 個失敗／錯誤紀錄；就緒逾時與 `os.killpg` 的 `PermissionError`。不可把清理權限錯誤全部等同於 `ps`；需沙箱外重驗。
- `test_inbox_reader_release`：8 個失敗紀錄，輸出明示 `ps` 拒絕啟動。
- `test_mode_unification...test_normal_eof_reaps_detached_app_server_descendant`：程序快照為空，查核其實作依賴程序查詢；沙箱外未驗證。
- `test_worker_no_nested`：補修後三供應商子情境皆明示 `ps` 拒絕。

逐項完整名稱與分類見 `.fix1-evidence/failure-index.md`。上列數字是失敗／錯誤紀錄數（含子情境與清理錯誤），不是獨立測試函式數。

已驗證：改前基線（45 項，OK／skip 1）、F-02 先紅後綠、政策命令列拒絕、新舊測試選擇器對照、上述定點回歸、語法與差異檢查。略過的 1 項是需明示啟用的主機傳輸證明測試，未讀取真實主機證明。未驗證：沙箱外完整測試、真實供應商、正式安裝；依任務未執行。

## 回滾與交付

`.fix1-evidence/before/` 保存本輪十四個修改檔的改前內容，包括主控原有未提交內容。需要回滾時，只針對本輪差異還原；不要使用基底版本覆蓋整份檔案。`.fix1-evidence/commands.md` 列出指令與輸出對應。

未另派代理：任務明示工人自己完成。已自行檢查本輪差異。共享變更紀錄目錄不在沙箱可寫範圍，本文件作為專案內變更紀錄，供主控收件。

圖譜工具已先嘗試；搜尋被拒絕：`MCP tool call requires approval, but approval policy is never`。後續使用允許的定點讀檔。首次嘗試將測試輸出寫到 `/tmp` 被拒絕，改存工作樹內 `.fix1-evidence/`，未繞過權限。
