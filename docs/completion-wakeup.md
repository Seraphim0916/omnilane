# 完成續驗橋接（Codex heartbeat）

協定版本：2026-09-07。

`scripts/completion-wakeup.py` 是獨立命令列工具：
追蹤明確登錄的 CLI 工作、發出一次有效領取權、記錄主控送達與驗收。
它不呼叫模型、不建立排程、不讀工作輸出，也不保證 app 已喚醒。
真正喚醒由主控透過 `mcp__codex_app__automation_update` 註冊的 heartbeat 負責。
這是排程輪詢，不是即時推播；本機離線、排程延遲與 app 狀態都會影響送達。

## 登錄與身分

每個 host/thread 只有一份 registry，同 run 可追加工作並重用 automation ID。
主控傳入的 thread ID 必須來自 app 的實際任務，不能把 omnilane 自身 thread 名稱代入。
只有一個 active run；不同 run 會拒絕。既有 run 已 closed 時，新 run 的 prepare 會先把完整舊紀錄
存入私有 history，再建立 pending 的新紀錄；舊 automation ID、租約、事件與 adoption 不沿用。
舊 run 的 poll／ack 會拒絕，不會領取新 run 的事件。原本 active／pending 的 run 從不默默被取代。
同一 host/thread 歷史曾使用的 run_id 永不重用，以免舊 heartbeat 命令在後續同名輪次復活。
歷史的 accepted receipt 留在原紀錄，不沿用為新 run 的驗收證據；請為每輪指定全新的 run_id。
這是工作流程契約，不是防止同使用者程序偽造參數的安全隔離。

```bash
PY=python3
SCRIPT=/absolute/repo/scripts/completion-wakeup.py
BIND=(--thread-id CONTROLLER_THREAD --host-id local --run-id RUN_ID)
$PY "$SCRIPT" prepare "${BIND[@]}" \
  --workdir /absolute/repo --job JOB_ID --ttl-seconds 3600
```

工作必須已存在於 `~/.omnilane/jobs/JOB_ID`，且 `meta.json` 身分與工作目錄相符。
若 metadata 的 foreman_session 非空，表示另有原主控身分，單靠 allowlist 不足以收錄。
該欄位來自 Claude 程序綁定，並非 Codex thread ID；程式不硬比兩者。
必須取得操作者明示採用後，把確認整理為 scoped adoption receipt，prepare 加 `--adoption-evidence`。
未提供會回 `adoption_required:JOB_ID:IDENTITY_SHA256`，只透露工作指紋，不輸出原 session 值。
空 foreman_session 仍可由主控的明確 allowlist 直接登錄。

```json
{
  "source": "operator_adoption",
  "operator_confirmed": true,
  "controller_thread_id": "CONTROLLER_THREAD",
  "controller_host_id": "local",
  "run_id": "RUN_ID",
  "job_identity_sha256": {"JOB_ID": "IDENTITY_SHA256"}
}
```

此 receipt 必須反映已取得的明示確認，不是讓工作者自己填 true 自我授權。
程式驗其 run/thread/host/job/fingerprint 並保存 receipt 雜湊；這是可稽核聲明，不是 OS 身分認證。
新 run 不沿用舊 run 的 adoption；metadata 身分改動後舊 adoption 也失效。
可用 `--home /absolute/private/path` 指定測試儲存；所有命令都要使用同一 home/binding。
prepare 回傳工具名稱、tool_args（heartbeat/create 或 update、prompt、targetThreadId、status），
以及 schedule_request。主控將工具接受的排程格式補入 rrule，保留既有通知偏好後呼叫工具。
沒有直接寫 automation 設定檔，也沒有把 pending 當 active。

工具成功後，由主控將實際結果整理成最小 JSON receipt（不要包含憑證或私人訊息）：

```json
{
  "source": "automation_update",
  "automation_id": "AUTOMATION_ID",
  "controller_thread_id": "CONTROLLER_THREAD",
  "controller_host_id": "local",
  "status": "ACTIVE",
  "effective_interval_seconds": 60
}
```

```bash
$PY "$SCRIPT" record-registration "${BIND[@]}" \
  --automation-id AUTOMATION_ID --evidence /absolute/registration-receipt.json
```

支持綁入已存在的 automation。receipt 是主控對工具結果的具名聲明，腳本驗格式／綁定／雜湊，
不自行查 app；輸出明示 caller-attested，不宣稱獨立確認排程。註冊不等於 delivered。

## 排程回合接續驗收

```bash
$PY "$SCRIPT" poll "${BIND[@]}"
```

僅 registered、未過期的工作會得到 events。pending／closed 不領取，expired 要求停用。
每事件含 event_key、job_id、exit_code、claim_token、lease_until。
空 events 不表示已完成：可能仍執行中或已有領取租約；以 needs_disable 與 errors 判斷。
poll 預設租約 900 秒，可用 `--lease-seconds` 調整至最多一天；有效租約期間第二次 poll 不重複領取。
租約過期可以重播且換 token，舊 token 失效。工作內容從未作為可執行通知提示。

```bash
$PY "$SCRIPT" ack-delivered "${BIND[@]}" --event-key EVENT_KEY --claim-token CLAIM_TOKEN
# 主控現在讀相關產物、執行原任務驗證，再留下精簡證據檔。
$PY "$SCRIPT" ack-accepted "${BIND[@]}" --event-key EVENT_KEY --claim-token CLAIM_TOKEN \
  --result PASS --evidence /absolute/verification-evidence.md
```

result 支援 PASS／FAIL／PARTIAL／BLOCKED；非零工作退出碼也可送達並驗收為 FAIL。
accepted 表示主控已作驗收判定，**不是固定等同 PASS**。沒有 delivered 就拒絕 accepted。
同一租約內相同 acknowledgement 可重試；驗收結果或證據衝突被拒絕。
binding 或終態退出碼在 poll 後改動，ack 也會拒絕。

## 停用

所有登錄工作均已作驗收判定時，poll 回 needs_disable=true。
主控先以 automation_update 停用現有 heartbeat，工具確認後寫同樣綁定、status=PAUSED 的 receipt：

```bash
$PY "$SCRIPT" closed "${BIND[@]}" --automation-id AUTOMATION_ID \
  --evidence /absolute/paused-receipt.json
```

未驗收完的使用者停止用 `--close-reason stopped`；真正到期用 `--close-reason expired`。
預設 completed 會檢查所有工作已判定。closed 命令本身不會停用 app 排程。
使用者先前的停止／重開機要求與新一輪授權仍由主控處理，不自動延長期限。

## 資料與限制

- 僅讀 CLI 的 `meta.json` 以及 `exit`；不讀 inbox tail、out.txt、events、task、native.json、Codex 私人對話。
- 凍結 metadata 的 lane/vendor/model/mode/workdir/foreman_session/started；忽略其他可變欄位，不因 finished 更新誤拒。
- native 工作明確拒絕，使用原生完成通道；後續可接獨立公共終態 receipt，不應掃描 private native state。
- 儲存於 `~/.omnilane/wakeups`，目錄 0700、檔案 0600，拒絕 symlink、非檔案、不同擁有者及超大紀錄。
- 核心鎖使用 flock，程序結束自動釋放；registry 以同目錄暫存檔及原子替換發布。
- 至少一次交付與冪等驗收，不宣稱 exactly-once；租約期間不得啟動第二份驗收。
- 一個 controller registry 最多 100 個工作；metadata 綁定錯誤會出 errors，不默默接受替代工作。
- 待真實排程回合在同主控任務送達、產物驗收、停止確認後，才可聲稱端到端 PASS。

驗證命令（從 repo 根目錄執行）：`python3 -m unittest discover -s tests -p test_completion_wakeup.py`。
