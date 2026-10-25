# C-B06：信箱描述符繼承與收尾實測

來源：codex-s／MacStudio；2026-10-26。
工作目錄：`/Users/vincentw/dev/omnilane-wt/mailbox-fd`。

狀態：PASS（下列隔離情境）。只新增測試與本報告，沒有修改共用工人、設定或既有測試。

## 實際驗證

`tests/test_mailbox_descriptor_lifecycle.py` 沿用 `offline_env.isolated_environment`，複製原有程式庫與設定到暫存環境，直接啟動未修改的 `job-worker.sh`，以假 Claude 執行器產生一個真正的子程序。全數循序執行，沒有連線或呼叫模型。

| 情境 | 工人退出碼 | 描述符外洩 | 程序群組殘留 | 信箱殘留 |
| --- | --- | --- | --- | --- |
| 正常退出 | 0 | 無 | 無 | 無 |
| 成功結果後由操作端送出 SIGUSR1 | 0 | 無 | 無 | 無 |
| 假執行器實際計時兩秒逾時，沒有成功結果 | 142 | 無 | 無 | 無 |
| 成功結果後關閉，執行器忽略 SIGTERM 且不讀取 EOF | 0 | 無 | 無 | 無 |

測試以 `fstat` 記錄執行器與子程序的 0–1023 號描述符，和兩個信箱 FIFO、事件檔的裝置與 inode 比對。子程序使用 `close_fds=False`，並驗證刻意設為可繼承的對照檔案確實傳入子程序，避免測試工具自行關閉描述符而掩蓋問題。另核對父子關係與程序群組。

工人結束後，先檢查兩個已記錄程序與整個程序群組皆消失，再檢查 `inbox.ready`、`inbox.holder.pid`、`inbox.fifo`、`runner-inbox.fifo` 已移除。測試的保險清理在斷言之後才執行，不能把清理成功當成產品通過。事件檔和輸出檔是保留結果，不列為應刪除的信箱。

若描述符、退出碼或收尾斷言失敗，unittest 輸出會包含程序識別碼、父子關係、描述符與信箱 inode、殘留程序／檔案、退出碼及標準錯誤的 JSON 證據。本次沒有產品失敗需要修復。

## 驗收證據

指令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_mailbox_descriptor_lifecycle.py' -v
```

最終實際輸出尾段（退出碼 0）：

```text
test_executor_timeout_without_result (test_mailbox_descriptor_lifecycle.MailboxDescriptorLifecycleTests.test_executor_timeout_without_result) ... ok
test_normal_exit (test_mailbox_descriptor_lifecycle.MailboxDescriptorLifecycleTests.test_normal_exit) ... ok
test_operator_close_after_result (test_mailbox_descriptor_lifecycle.MailboxDescriptorLifecycleTests.test_operator_close_after_result) ... ok
test_operator_close_forces_unresponsive_runner (test_mailbox_descriptor_lifecycle.MailboxDescriptorLifecycleTests.test_operator_close_forces_unresponsive_runner) ... ok

----------------------------------------------------------------------
Ran 4 tests in 14.625s

OK
```

`git diff --check`：退出碼 0，標準輸出與標準錯誤皆空白。新增檔尚未加入索引，另以 `git diff --no-index --check /dev/null <新增檔>` 檢查兩份新增檔案。

## 未驗證範圍

- 使用假 Claude 執行器；沒有驗證真實供應商、其他廠商分支或 Codex 專用轉送暫存描述符。
- 逾時是執行器的真實計時與 142 退出碼，沒有啟動整體工作的外層監督器。
- 子程序透過專用標準輸入管線的 EOF 自行結束；不涵蓋脫離程序群組、忽略 EOF 的惡意子程序。
- 沒有檢查 1024 以上描述符、其他作業系統或完整測試套件。

未提交或加入 Git 索引。`.wt-tmp/mailbox-acceptance.txt` 是本機驗收輸出，不屬於交付追蹤檔案；回滾僅需移除本次兩份新增檔案。
