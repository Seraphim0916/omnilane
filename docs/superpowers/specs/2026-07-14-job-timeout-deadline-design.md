# Whole-job timeout design

Date: 2026-07-14
Status: proposed

## Context

Omnilane already has a per-CLI-call watchdog:

```text
--timeout > OMNILANE_TIMEOUT_<LANE> > OMNILANE_TIMEOUT > 600
```

That watchdog intentionally resets for every CLI invocation. A Grok retry and
each voter in each vote round therefore receive a fresh per-call allowance.
This protects individual vendor processes from hanging, but it does not bound
the wall-clock duration of the complete dispatch.

This feature adds a separate, optional whole-job budget. It does not redefine
the existing watchdog.

## Goals

- Add an opt-in wall-clock limit covering one complete dispatch.
- Share one deadline across retries, vote members, vote rounds, background
  execution, and Codex lock waiting.
- Keep the existing per-call watchdog and its defaults unchanged.
- Use the smaller of the per-call allowance and the whole-job time remaining
  for every CLI invocation.
- Record the configured job budget in job metadata.
- Return exit 124 when the whole-job deadline is exhausted.
- Preserve current behavior byte-for-byte at the interface level when no
  whole-job budget is configured.

## Non-goals

- No default whole-job timeout.
- No change to retry counts, vote quorum, or vote round semantics.
- No change to `OMNILANE_LOCK_TIMEOUT` when no job deadline is active.
- No replacement of the existing portable `timeout` / `gtimeout` / Perl
  watchdog implementation.
- No sub-second precision.

## User interface

### Command flag

```bash
scripts/dispatch.sh --job-timeout 1800 hard-judgment "task"
```

`--job-timeout` accepts a positive integer number of seconds. Missing,
zero, negative, or non-numeric values are usage errors and exit 2.

### Environment configuration

```bash
export OMNILANE_JOB_TIMEOUT=1800
export OMNILANE_JOB_TIMEOUT_HARD_JUDGMENT=2400
```

Resolution order:

```text
--job-timeout SECONDS
  > OMNILANE_JOB_TIMEOUT_<LANE>
  > OMNILANE_JOB_TIMEOUT
  > disabled
```

The lane suffix follows the existing convention: uppercase the lane and
replace `-` with `_`.

An unset or empty value means no whole-job budget. Initial scope deliberately
does not add an `off` sentinel; users who need exceptions can configure only
the lanes that require a budget.

### Metadata

`meta.json` keeps the existing `timeout` field as the per-call watchdog and
adds:

```json
{"job_timeout": 1800}
```

When disabled, `job_timeout` is `null`. The absolute deadline is runtime
state and is not persisted.

## Runtime model

After argument parsing and lane validation, before route and lock resolution,
`dispatch.sh` resolves the job budget once. When enabled it computes:

```text
OMNILANE_JOB_DEADLINE = current_epoch_seconds + job_timeout
```

The value is exported to the selected runner and all descendants. Background
jobs inherit the same fixed deadline, so detaching does not reset the budget.

Before every vendor CLI invocation:

```text
effective_call_timeout =
  min(per_call_watchdog, job_deadline - current_epoch_seconds)
```

If no job deadline exists, `effective_call_timeout` equals the current
per-call watchdog. If no positive time remains, no new CLI invocation starts
and the caller exits 124.

Second-level wall-clock time is used because it is portable across the Bash
3.2 environment shipped by macOS. Enforcement can exceed the configured value
by scheduler and process-termination overhead, but retries and vote fan-out
cannot reset the budget.

## Shared helpers

`scripts/lib/common.sh` owns canonical deadline behavior:

- validate the exported deadline before arithmetic expansion;
- return the effective timeout for a per-call cap;
- report whether the deadline is exhausted;
- return remaining whole-job seconds for lock waiting;
- normalize an exit to 124 when the whole-job deadline has expired.

Deadline text is treated as untrusted input even though `dispatch.sh`
normally creates it. Direct runner invocation with malformed deadline state
must fail closed with exit 2 rather than enter unsafe shell arithmetic.

## Runner integration

### Codex, Claude, exec

Resolve the effective timeout immediately before the CLI call. Keep existing
per-call watchdog handling. After the call, normalize the result to 124 if the
whole-job deadline has expired.

### Gemini

Resolve the effective timeout immediately before invocation and pass it to
`--print-timeout`. Normalize an expired whole-job deadline to exit 124 after
the command returns.

### Grok

Keep `OMNILANE_TIMEOUT` as the immutable per-call cap. At the start of every
retry, recompute the effective timeout from the shared deadline. Stop retrying
and exit 124 as soon as no budget remains.

### Vote

All voter runners inherit the same absolute deadline. Check the deadline
before and after each voter. A voter exit caused by deadline exhaustion aborts
the panel immediately with exit 124 rather than being converted into quorum
exit 5 or round-two exit 6.

### Codex lock

`acquire_cwd_lock` continues enforcing `OMNILANE_LOCK_TIMEOUT`, but an active
whole-job deadline is an additional upper bound. If the job deadline expires
first, lock acquisition exits 124. The wait loop must not sleep longer than the
remaining whole-job seconds.

## Error and exit behavior

- Invalid `--job-timeout` or environment value: exit 2.
- Job deadline exhausted before or during work: exit 124.
- Existing per-call watchdog without a job deadline: preserve current backend
  exit behavior, including 124 from GNU timeout and 142 from the Perl alarm.
- Existing domain exits 3, 4, 5, 6, 86, and 87 remain unchanged unless the job
  deadline is the condition that ended the operation.
- Background jobs persist exit 124 in their existing `exit` file.

## Test strategy

All production changes follow red-green-refactor.

### Resolution tests

- disabled by default;
- global environment value;
- per-lane value beats global;
- command flag beats both;
- missing, zero, negative, and non-numeric values exit 2;
- metadata records numeric `job_timeout` or `null`.

### Deadline tests

- a sleeping exec gate is stopped by the job deadline;
- a shorter per-call watchdog still wins over a longer job budget;
- Grok retries share one budget and do not each receive a fresh job timeout;
- vote members and rounds share one budget and deadline exhaustion returns 124;
- Codex lock waiting cannot outlive the job deadline;
- background execution inherits the original deadline;
- direct runner invocation rejects malformed deadline state safely.

Wall-clock assertions use generous upper bounds and also assert attempt or
voter marker counts, reducing timing-test flakiness.

### Regression checks

- full `bash tests/run.sh`;
- Bash syntax checks;
- `shellcheck -S warning`;
- routing table smoke test;
- `git diff --check`.

## Documentation

Update all five READMEs, `local.sh.example`, `commands/route.md`, and the
`omnilane` skill. Documentation must keep these terms distinct:

- `--timeout`: per CLI invocation hang guard;
- `--job-timeout`: optional end-to-end dispatch budget.

## Compatibility

The feature is opt-in. Existing invocations, environment files, metadata
readers, runner arguments, and timeout behavior continue to work. Adding
`job_timeout` to metadata is additive.

## Acceptance criteria

1. With no job timeout configured, the existing suite and behavior pass
   unchanged.
2. `--job-timeout 1` prevents Grok retry or vote fan-out from extending the
   dispatch into multiple fresh one-second budgets.
3. Every CLI invocation uses the lesser of the per-call cap and job time
   remaining.
4. Deadline exhaustion is visible as exit 124 in foreground and background
   jobs.
5. CI runs all new tests and existing checks successfully.
