# feat/jobs-cancel — delete one stored job (`jobs rm ID`)

Branch: `feat/jobs-cancel` (off `main` @ `c45955e`, v0.8.3) — second commit
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

The job store gained a full lifecycle — start, inspect, `wait`, `cancel` — but
the only way to delete state was `jobs prune`, which bulk-deletes *completed*
jobs by count/age. There was no way to remove one specific job (e.g. a failed
run, or a job whose stored task text you no longer want on disk). This adds
`jobs rm ID`.

## Behavior

- Removes a job's entire stored directory (`task.txt`, `out.txt`, `exit`, `pid`,
  metadata) → `removed ID`, exit 0.
- Refuses a job whose worker is still alive (no `exit` file **and** a valid `pid`
  that `kill -0` confirms) → `job is running (pid N); cancel it first`, exit 1.
  Finished (exit recorded) or dead (pid gone) jobs delete freely.
- Unknown job → exit 1 (`no such job`); malformed id → exit 2 (`invalid job id`).
- Human-only (not in the `--json` allowlist), matching `cancel`/`wait`/`tail`.

## Why refuse a running job instead of cancel-then-delete

Deleting the directory out from under a live worker would strand it writing into
a removed path (its `out.txt`/`exit` writes would fail or recreate a partial
dir). Single responsibility: `rm` only removes *settled* state; stopping a live
worker is `cancel`'s job. The user composes them (`cancel` then `rm`) when they
mean to kill-and-purge, which keeps each command's contract obvious.

## Safe deletion

`select_job` first proves the target is a **real child directory of the store,
never a symlink**, and the id matches the fixed `JOB_ID_PATTERN`, so the path
cannot traverse out. Deletion uses `/bin/rm -rf "$JOB_DIR"` — the same absolute
binary `prune` uses — bypassing shell `rm` guards on a path already validated as
in-store.

## Red/green oracle

New test `test_jobs_rm` in `tests/run.sh` uses **real** background jobs (two
`exec`-vendor gates: a `quick` gate that exits immediately and a `sleeper` gate):

1. Finished job → `rm` returns `removed <id>`, exit 0, directory gone.
2. Running job (waited for a live pid) → `rm` exits 1 with `running`, directory
   **and** worker left intact; the test then kills the worker group.
3. Adversarial: unknown id → exit 1; malformed id → exit 2.

- RED before implementation: `rm` hit the `*) usage` default → `rm of finished
  job failed: rc=2` (`71`→`70 passed, 1 failed`).
- GREEN after implementation: `71 passed, 0 failed`.

## Real runtime evidence

- `bash -n scripts/jobs.sh` → OK; `shellcheck -S warning scripts/jobs.sh` → exit 0
- `bash tests/run.sh` (Bash 5) → `71 passed, 0 failed`
- `/bin/bash tests/run.sh` (macOS Bash 3.2) → `71 passed, 0 failed`
- `python3 -m unittest tests.test_release_version tests.test_ci_policy` → `OK` (6)

Out-of-harness smoke on real processes:

```
-- rm finished (20260719-030020-20762-20837) --
removed 20260719-030020-20762-20837
rc=0
dir-gone-ok
-- rm running (20260719-030020-20914-25072 pid=20969) --
job is running (pid 20969); cancel it first
rc=1
dir-kept-ok
-- --json rm rejected --
json-rc=2
```

Result: **VERIFIED** — finished job removed, running job refused with its
directory and worker untouched, `--json rm` rejected, adversarial ids handled.

## Known risks / notes

- PID-reuse window: the running-guard trusts the owner-only `pid` file and
  `kill -0`, the same liveness basis as `status`/`wait`/`cancel`; not newly
  introduced here. A reused pid in the microsecond window is the shared,
  theoretical risk. The conservative failure mode is *refusing* a deletion (the
  guard errs toward "looks alive"), never deleting a job that is actually live.
- `rm` deletes irrecoverably (no trash). This mirrors `prune --apply`; both use
  `/bin/rm -rf` on an in-store, symlink-checked path.

## Docs

Added `jobs.sh rm ID` to all five READMEs' command reference (column-aligned with
`cancel`) and an `## [Unreleased]` CHANGELOG entry. `bin/omnilane`'s summary help
is a non-exhaustive teaser, left unchanged. VERSION untouched — no release action.

## Recommendation

Select. Completes the job CRUD surface (delete-one) alongside `cancel` and
`prune`, cooperates with the running-worker guard, offline-verified on real
processes.
