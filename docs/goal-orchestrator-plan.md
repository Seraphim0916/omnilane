# Goal Orchestrator — caller-owned bounded dispatch ledger

## 摘要（繁中）

`omnilane goal` 是有預算上限的目標台帳。開啟目標的代理工作階段或使用者自己維持迴圈：決定下一步、派工、讀取結果、記錄敘事並收尾。omnilane 只守住工作數、時間與重複失敗上限，並把每份工作與結案報告存到同一個目標底下。

## Problem

探索式工作需要一個可追查的邊界：哪些工作屬於同一個目標、已花掉多少工作數與時間、相同失敗是否正在重試，以及最後由誰怎麼判定收尾。這些邊界必須在每次派工前生效，同時不能把開啟目標者已持有的脈絡複製到另一個常駐工作階段。

## What shipped

`omnilane goal` is a bounded ledger around normal dispatch:

- `omnilane goal open "TEXT" [--budget-jobs N] [--budget-seconds S] [--workdir DIR]` creates a goal and holds its job-count and wall-clock budgets.
- `omnilane goal dispatch GOAL_ID [dispatch.sh args...]` checks the job cap, wall clock, and repeat-failure fuse before it runs the requested dispatch, then records that job.
- `omnilane goal note GOAL_ID "TEXT"` records the caller's narrative for the close report.
- `omnilane goal status GOAL_ID` shows the budget state and per-job lines, including recorded outcomes and fuse state.
- `omnilane goal close GOAL_ID [--summary "TEXT"]` writes `goals/<id>/report.md` with the goal, budget usage, jobs, notes, and closing summary.

`omnilane doctor` includes a read-only goal-orchestrator check.

The caller is the loop: the agent session or human that opened the goal decides what to dispatch next, reads the result, records the useful narrative, and decides when to close. omnilane never runs a planner model.

## History

P1–P3 built a resident planner, its action protocol, parallel fan-out, failure fuse, report, documentation, and doctor check. P4 removed the resident planner by owner ruling: the brain belongs to the session that opened the goal, while a context-free extra planner duplicated that role and spent tokens. P4 retained the ledger, budgets, fuse, job records, report, and doctor surface.

## Remaining known gaps

- The caller must retain the goal context and make the next-work, result-interpretation, retry, and completion decisions; the ledger does not choose them.
- Only jobs launched with `goal dispatch` are covered by that goal's budgets, fuse, records, and close report. Use direct dispatch for independent or obvious one-off work.
