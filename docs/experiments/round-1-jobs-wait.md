# Round 1 experiment: bounded background job wait

Branch: `codex/idea-jobs-wait`

Commit: `3b478a4`

Status: PARTIAL. Functional and runtime acceptance pass. Local ShellCheck is
unverified because `shellcheck` is not installed on the current MacStudio.

## Hypothesis

A read-only `jobs.sh wait ID [--timeout N]` command can let scripts wait for a
single background job without polling private artifacts themselves, while
preserving the recorded job exit and bounding every wait.

## Red/green evidence

- Red: the focused shell oracle reported `0 passed, 1 failed`; `wait` returned
  usage exit 2 instead of following a pending job to exit 7.
- Green: all 51 shell tests passed in four isolated batches (`28 + 4 + 1 + 18`).
  Process-group timeout tests were isolated so their deliberate signals could
  not interfere with another test batch in the shared PTY.
- All 36 Python tests passed, including the real browser behavior cases.
- Bash syntax, Perl syntax, Python compilation, `git diff --check`, and routing
  list smoke passed.

## Runtime evidence

The public `bin/omnilane route --background` path launched a local fake exec
gate in an isolated `OMNILANE_HOME`. An immediate public `omnilane jobs wait`
returned 124 and `wait timeout after 0s`. A second bounded wait observed the
same real job complete, returned its recorded exit 7, printed `done exit=7`, and
matched the persisted exit artifact.

## Adversarial evidence

- Timeout 0 performs one immediate terminal check without sleeping. Negative,
  nonnumeric, and values above 86400 fail with usage exit 2.
- A malformed exit record fails closed with exit 1 and never echoes injected
  lines.
- A dead worker without an exit record returns 125. A live reused PID is not
  mistaken for completion; it remains pending until the wait bound is reached.
- Job-directory symlinks are rejected, a directory that disappears mid-wait
  returns exit 1, and the pending-job artifact hash remains unchanged.
- TERM interrupts the waiter with exit 143 and requires no state cleanup because
  the command never writes.

## Review

The wait loop uses the existing bounded exit and PID readers plus the existing
job-ID and real-directory boundary. It checks the exit record before PID
liveness on every iteration, then rechecks completion before reporting a dead
worker. It adds no provider call, lock, job artifact, or cleanup path.

## Known limits

- Polling resolution is one second. The default bound is 600 seconds and the
  accepted range is 0 through 86400 seconds.
- PID reuse cannot be proven from the portable PID artifact alone. A reused live
  PID therefore delays the answer until either an exit record appears or the
  wait times out; it never produces a false successful completion.
- A real job exit 124 or 125 shares the numeric code used for wait timeout or a
  dead worker. The stable output distinguishes `done exit=N` from wait failures.
- ShellCheck must run before this branch can receive an overall PASS.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates the commit, revert `3b478a4` to remove this
feature. `main` remains unchanged pending Vincent's final judgment.
