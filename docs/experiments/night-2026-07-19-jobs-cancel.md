# feat/jobs-cancel — stop a running background job

Branch: `feat/jobs-cancel` (off `main` @ `c45955e`, v0.8.3)
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

`jobs.sh` could start (`--background`), inspect (`status`), and `wait` on
background jobs, but there was no way to stop one — a mis-dispatched long job had
to run to its timeout. This branch adds `jobs cancel ID`.

## Behavior

- Already terminal (`exit` recorded) → `already finished (exit N)`, exit 0.
- No live worker (missing/invalid pid, or `kill -0` fails) → `not running (no
  live worker to cancel)`, exit 0.
- Live worker → signal its **process group** with `SIGTERM`. Dispatch launches
  background workers under `set -m`, so the recorded pid is the group leader and
  its subshell traps `TERM → finish_job 143` (records a best-effort exit). The
  group signal also kills the vendor CLI child. After a 5s grace, if still alive,
  escalate to `SIGKILL` on the group; SIGKILL is untrappable, so `cancel` then
  records the terminal exit itself (`137`).
- Idempotent; unknown job → exit 1 (`no such job`), malformed id → exit 2.
- Human-only (not in the `--json` allowlist), matching `wait`/`tail`.

## Why process-group, not just the pid

Killing only the worker pid would orphan the vendor CLI child. The group signal
(derived from `ps -o pgid=`, falling back to the pid, which equals the pgid under
`set -m`) takes the whole tree down and lets the leader's existing TERM trap
record the exit — cooperating with the background lifecycle rather than fighting
it.

## Red/green oracle

New test `test_jobs_cancel` in `tests/run.sh` dispatches a **real** background
job (an `exec`-vendor `sleep` gate), waits for a live pid, cancels it, and
asserts the worker is dead and the job reached a terminal (non-running) state;
then checks idempotency and the unknown/invalid-id exits.

- RED before implementation: `cancel` hit the `*) usage` default → `cancel
  exited 2` (`70`→`69 passed, 1 failed`).
- GREEN after implementation: `70 passed, 0 failed`.

## Real runtime evidence

- `bash -n scripts/jobs.sh` → OK; `shellcheck -S warning scripts/jobs.sh` → exit 0
- `bash tests/run.sh` (Bash 5) → `70 passed, 0 failed`
- `/bin/bash tests/run.sh` (macOS Bash 3.2) → `70 passed, 0 failed`

Out-of-harness smoke exercising **both** paths on real processes:

```
A) cooperative (real background job, TERM trap):
   status-before: running
   cancel -> cancelled (exit 143)
   alive-after=no
   status-after: done exit=143
   second cancel -> already finished (exit 143)

B) SIGKILL escalation (worker ignores TERM):
   cancel -> cancelled (exit 137)   (took 6s: 5s grace + kill)
   alive-after=no
   status-after: done exit=137

C) adversarial:
   unknown id -> no such job; exit=1
   invalid id -> invalid job id; exit=2
```

Result: **VERIFIED** — real workers terminated (group taken down, no orphaned
`sleep`), clean terminal state recorded on both the graceful and force paths,
idempotent, adversarial handled.

## Known risks / notes

- PID-reuse window: like the existing `status`/`wait`, `cancel` trusts the
  owner-only `pid` file and `kill -0`. The added blast-radius guard is that it
  signals the process **group** derived from that pid, and only acts when the pid
  is currently alive. A reused pid in the microsecond window remains a
  theoretical risk shared with the existing liveness checks; not newly
  introduced here.
- The synthetic `137` is only written after the worker is confirmed dead (post
  `SIGKILL`), so it cannot race a live supervisor's own exit write.

## Docs

Added `jobs.sh cancel ID` to all five READMEs' command reference and an
`## [Unreleased]` CHANGELOG entry. `bin/omnilane`'s summary help (already a
non-exhaustive teaser) is left unchanged. VERSION untouched — no release action.

## Recommendation

Select. Closes a real lifecycle gap, cooperates with the existing group/trap
model, offline-verified on real processes across both termination paths.
