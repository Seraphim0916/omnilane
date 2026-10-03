# Goal Orchestrator — caller-owned dispatch ledger

## 摘要（繁中）

`omnilane goal` 是目標台帳。開啟目標的代理工作階段或使用者自己維持迴圈：決定下一步、派工、讀取結果、記錄敘事並收尾。工作數與秒數預算預設都不設上限；只有呼叫端傳入 `--budget-jobs N` 或 `--budget-seconds S` 時，omnilane 才守住對應上限。重複失敗熔斷器不是預算，仍預設啟用。每份工作與結案報告都存到同一個目標底下。

## Problem

探索式工作需要一個可追查的邊界：哪些工作屬於同一個目標、已花掉多少工作數與時間、相同失敗是否正在重試，以及最後由誰怎麼判定收尾。這些邊界必須在每次派工前生效，同時不能把開啟目標者已持有的脈絡複製到另一個常駐工作階段。

## What shipped

`omnilane goal` is a ledger around normal dispatch. Job-count and wall-clock budgets default to unlimited; each budget flag opts in to that cap:

- `omnilane goal open "TEXT" [--budget-jobs N] [--budget-seconds S] [--workdir DIR]` creates a goal. Omitted budget flags remain unlimited; supplied flags set hard caps.
- `omnilane goal dispatch GOAL_ID [dispatch.sh args...]` checks any caller-supplied job or wall-clock cap plus the always-on repeat-failure fuse before it runs the requested dispatch, then records that job.
- `omnilane goal note GOAL_ID "TEXT"` records the caller's narrative for the close report.
- `omnilane goal status GOAL_ID` shows the budget state and per-job lines, including recorded outcomes and fuse state.
- `omnilane goal close GOAL_ID [--summary "TEXT"]` writes `goals/<id>/report.md` with the goal, budget usage, jobs, notes, and closing summary.

`omnilane doctor` includes a read-only goal-orchestrator check.

The caller is the loop: the agent session or human that opened the goal decides what to dispatch next, reads the result, records the useful narrative, and decides when to close. omnilane never runs a planner model.

`goal dispatch` starts and records a job, so its `--dry-run` option is unsupported.
It exits 2 before acquiring a goal lock, refreshing state, consuming a stdin task,
or creating a dispatch intent/reservation. Use
`omnilane dispatch --dry-run [flags] LANE "TASK"` for a routing preview, explicitly
passing `--workdir DIR` when needed; direct dispatch does not inherit a goal's
working directory. A task or option value equal to `--dry-run` remains ordinary
data under the dispatch parser's existing rules.

## Interrupted dispatch and reservations

A durable intent reserves an ordinal and job slot before dispatch. A one-time,
no-overwrite outcome contains either its exact generated job-ID claim or an
explicit no-launch receipt. The dispatcher must win the claim before spawning.
A claim alone is not proof that the worker started: reconciliation requires
valid existing PID or terminal-exit evidence and writes derived records
atomically. Reconciliation never invokes dispatch.

`spent_jobs` keeps its recorded-job meaning. `reserved_jobs` counts unresolved
intents; admission considers both without charging a claim and its record twice.
Reservations do not create failure counts. Unresolved or conflicting evidence
blocks another dispatch and closing; missing files or a generic command error
are not permission to retry. A no-launch receipt competes for the same outcome
slot, so it cannot override a winning claim or permit a late launcher.

After an error or lost response, run `goal status` before deciding what to do.
A fresh identical command is a new intent: at-most-one launch is guaranteed per
intent, not across arbitrary repeated commands. Legacy goals remain supported.
Pruning worker evidence before first reconciliation leaves the reservation
unresolved; already-recorded goal history survives job pruning. Automatic
ambiguous-intent and stale-lock recovery is intentionally absent.

## History

P1–P3 built a resident planner, its action protocol, parallel fan-out, failure fuse, report, documentation, and doctor check. P4 removed the resident planner by owner ruling: the brain belongs to the session that opened the goal, while a context-free extra planner duplicated that role and spent tokens. P4 retained the ledger, budgets, fuse, job records, report, and doctor surface.

## Remaining known gaps

- The caller must retain the goal context and make the next-work, result-interpretation, retry, and completion decisions; the ledger does not choose them.
- Only jobs launched with `goal dispatch` are covered by that goal's budgets, fuse, records, and close report. Use direct dispatch for independent or obvious one-off work.
