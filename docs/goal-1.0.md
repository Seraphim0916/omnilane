# Omnilane 1.0 Goal

This file is the durable amendment to the active Codex Goal. Later user
instructions override the original immutable Goal text. The acceptance details
live in `docs/release-1.0-acceptance.md`.

## 推薦執行版（中文，可直接複製）

```text
/goal 將 /Users/vincentw/dev/omnilane 迭代成有完整證據、可供 Vincent 最終挑選的 1.0 實驗組合；先確認操作者、ComputerName、LocalHostName、pwd、git 狀態、現有版本、專案規則、測試基線與真實執行面，再依權威專案證據建立、驗證與比較創意分支。
驗證：每個點子先建立失敗測試或可重現的執行期 oracle，再做最小實作；執行目標測試、相關回歸、語法／ShellCheck、真實 CLI/API/應用 smoke 與對抗式驗證，涵蓋無效輸入、邊界、失敗路徑、相容性、競態／重入、回滾與使用者誤用；記錄分支 tip、執行指令、回傳碼、已修缺陷、已知風險與淘汰理由。測試通過但真實執行面未驗證只能標 PARTIAL。
約束：同一時間最多 8 個實驗分支，最多 3 輪由新執行證據、對抗失敗模式、使用證據或比較結果驅動的創意發散；改名或重試相同點子不算新證據。三輪限制不等於單支分支的除錯上限。任何實驗分支、整合分支或發布候選都不得合併到 main/master 或其他預設分支；不得建立或推送正式標籤、推送分支、發布套件或改變遠端／生產狀態。所有最終取捨、哪些分支可整合，以及是否進入 main，全部保留給 Vincent 判斷。
邊界：每個點子使用獨立 codex/ 前綴分支，只修改該點子直接相關的程式、測試與文件；不得覆寫、回退或夾帶使用者與其他分支的變更。使用 codebase-memory 先探索程式碼，大輸出使用 context-mode 或 RTK；憑證、權杖、Cookie、工作階段、提供者金鑰、快取與日誌內容不得送入索引、壓縮或代理。
迭代策略：每輪先根據當前證據提出最多 8 個可獨立證偽的假設，逐支完成紅綠測試、真實執行面與對抗驗證；退役或完成舊實驗後才能補入新分支，確保同時不超過 8 支。只有上一輪產生足以支持不同假設的新證據時才能進下一輪，最多 3 輪。同一阻塞連續 3 次基於新證據的修復仍無法前進時暫停，不降低驗收門檻。
完成條件：完成所有有證據支持的創意發散，或完成第 3 輪；每支嘗試過的分支都有可重跑證據、真實執行面結果、對抗驗證、缺陷與風險紀錄；交付逐支比較資料後停止，等待 Vincent 選擇。此 Goal 不以任何分支合併到 main 作為完成條件，也不授權該動作。
暫停條件：準備合併任何分支、準備進入 main/master、建立或推送標籤、推送遠端、發布套件、呼叫付費提供者／模型、需要憑證或帳號所有權、生產資料、服務重啟、symlink 切換、跨主機狀態搬移、破壞性操作、重大架構／產品取捨，或主機身分不清時，立即暫停並向 Vincent 提交證據與選項。
```

預設選擇理由：先保留所有實驗分支與完整證據，讓 Vincent 在沒有 `main` 污染或遠端副作用的情況下做最終判斷。

## Goal Draft (English-compatible)

```text
/goal Iterate /Users/vincentw/dev/omnilane into an evidence-complete set of 1.0 experiments for Vincent's final selection. First verify the operator, ComputerName, LocalHostName, pwd, git state, current version, project rules, test baseline, and real runtime surfaces; then create, validate, and compare creative branches from authoritative project evidence.
Verification: for every idea, first create a failing test or reproducible runtime oracle, then implement the smallest change; run target tests, relevant regression checks, syntax and ShellCheck, real CLI/API/application smoke tests, and adversarial validation covering invalid input, boundaries, failure paths, compatibility, races/re-entry, rollback, and misuse. Record the branch tip, commands, exit codes, fixed defects, known risks, and rejection rationale. Tests without real runtime evidence are only PARTIAL.
Constraints: keep at most 8 experiment branches active at once and run at most 3 creative-divergence rounds driven by new runtime evidence, adversarial failure modes, user evidence, or comparison results; renaming or retrying the same idea is not new evidence. The three-round limit is not a per-branch debugging limit. Never merge any experiment branch, integration branch, or release candidate into main/master or another default branch. Never create or push a release tag, push branches, publish packages, or change remote or production state. Vincent retains every final decision, including which branches may be integrated and whether anything enters main.
Boundaries: use one independent codex/-prefixed branch per idea and edit only directly related code, tests, and documentation. Do not overwrite, revert, or carry unrelated user or branch changes. Use codebase-memory before broad code exploration and context-mode or RTK for large output. Never route credentials, tokens, cookies, sessions, provider keys, caches, or logs into indexing, compression, or agents.
Iteration policy: in each round, derive up to 8 independently falsifiable hypotheses from current evidence, then complete red/green tests, real runtime smoke, and adversarial validation per branch. Retire or finish old experiments before adding new branches so no more than 8 remain active. Start another round only when the prior round produced new evidence supporting materially different hypotheses, with at most 3 rounds total. Pause after the same blocker survives 3 evidence-driven repair attempts; never weaken acceptance.
Stop when: all evidence-justified divergence is exhausted or Round 3 is complete, and every attempted branch has reproducible evidence, real runtime results, adversarial validation, and documented defects and risks. Deliver the branch comparison dossier and stop for Vincent's selection. Merging anything into main is neither a completion condition nor an authorized action.
Pause if: any merge is being considered, any action would enter main/master, a tag or remote push or package publication is proposed, a paid provider/model call is required, credentials or account ownership are needed, production data or service restart or symlink flip or cross-host state movement or destructive action is required, a major architecture/product choice appears, or host identity is unclear. Present evidence and options to Vincent.
```
