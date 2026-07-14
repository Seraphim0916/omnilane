# Whole-job timeout design

Date: 2026-07-14
Status: proposed, revision 2 after adversarial review

## Context

Omnilane already has a per-CLI-invocation watchdog:

```text
--timeout > OMNILANE_TIMEOUT_<LANE> > OMNILANE_TIMEOUT > 600
```

That watchdog intentionally resets for every vendor CLI invocation. A Grok
retry and each voter in each vote round therefore receive a fresh per-call
allowance. It protects individual calls from hanging, but it does not bound
the complete dispatch.

This feature adds a separate, optional execution budget for one dispatch. It
does not redefine the existing per-call watchdog.

## Adjudicated review findings

The first design propagated an absolute deadline to every runner and performed
shell arithmetic on it. Adversarial review identified three blocking defects,
all reproduced on the stock macOS Bash/Perl path:

1. Bash arithmetic recursively evaluates variable contents. A value such as
   `x[$(touch ...)]` executed the command substitution when used by either
   `[[ value -ge deadline ]]` or `$((deadline - now))`.
2. The current Perl `alarm + exec` fallback killed the directly exec'd shell,
   returned 142, and left its sleeping child alive.
3. A zero timeout disabled Perl's alarm; a negative timeout failed with a
   backend-specific non-timeout status.

The revised architecture therefore has no exported deadline, no job-deadline
arithmetic in Bash, and no per-runner deadline integration. One outer
supervisor owns the whole execution tree.

## Goals

- Add an opt-in wall-clock limit covering lock wait plus the selected runner.
- Bound retries, vote members, and vote rounds without modifying those loops.
- Stop the supervised process group and reap its leader when time expires.
- Return exit 124 when the whole-job budget is the terminating condition.
- Preserve the existing per-call watchdog, defaults, and no-budget behavior.
- Record the configured whole-job budget in job metadata.
- Use the same supervisor implementation in macOS development and Linux CI.

## Non-goals

- No default whole-job timeout.
- No absolute deadline environment variable.
- No remaining-time calculation or `min(per_call, remaining)` in runners.
- No change to retry counts, vote quorum, vote rounds, or lock policy.
- No claim that setup before worker launch is part of the timed interval.
  Argument parsing, routing resolution, task-file creation, and metadata
  creation happen first and should take negligible time.
- No guarantee for a descendant that deliberately escapes into a new session.
  Normal vendor subprocesses stay in the supervised process group; an explicit
  daemonization/`setsid` escape is outside this portable guarantee.
- No sub-second user interface.

## User interface

### Command flag

```bash
scripts/dispatch.sh --job-timeout 10800 hard-judgment "full repository audit"
```

`--job-timeout` is an integer number of seconds in the range
`1..999999999`. Missing, zero, negative, too-large, or non-numeric values are
usage errors and exit 2.

### Environment configuration

```bash
export OMNILANE_JOB_TIMEOUT=10800
export OMNILANE_JOB_TIMEOUT_HARD_JUDGMENT=14400
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

An unset or empty value means no whole-job budget. Initial scope does not add
an `off` sentinel.

### Metadata

`meta.json` keeps `timeout` as the per-call watchdog and adds:

```json
{"job_timeout": 10800}
```

When disabled, `job_timeout` is `null`. No absolute deadline is persisted or
exported.

## Runtime model

### Dispatch control plane

After lane and route resolution, `dispatch.sh` resolves both timeout controls:

- `OMNILANE_TIMEOUT`: existing per-call watchdog;
- `JOB_TIMEOUT`: new optional whole-job budget.

Job-timeout validation is lexical only:

```text
^[1-9][0-9]{0,8}$
```

The raw value is never placed in a Bash arithmetic context. Only the validated
digits are written to metadata or passed as one argv element to the
supervisor.

Dispatch then creates the job directory, task file, and metadata. Its
`run_job` controller records the controlling PID, invokes the worker, writes
the worker's final status to the existing `exit` file, and prints the normal
foreground result.

### Worker boundary

Lock acquisition and runner invocation move into one small executable worker
(`scripts/lib/job-worker.sh`). Arguments are passed as an argv array; no shell
source string, `eval`, or exported Bash function is used.

With no whole-job budget, dispatch calls the worker directly.

With a whole-job budget, dispatch calls:

```text
scripts/lib/job-timeout.pl SECONDS scripts/lib/job-worker.sh ARGS...
```

The timer begins immediately before the supervisor forks the worker. The
worker covers:

1. Codex same-directory lock wait, when applicable;
2. the selected runner;
3. all runner-owned retries, voters, and rounds.

This makes the earlier “each call receives the remaining time” mechanism
unnecessary. The existing inner per-call watchdog and the outer whole-job
supervisor race naturally; whichever limit expires first ends that work.

### Process supervision

`job-timeout.pl` uses stock Perl modules `POSIX` and `Time::HiRes`:

1. Revalidate `SECONDS` before numeric conversion.
2. Fork once.
3. In the child, call `setsid` and exec the worker. The worker becomes leader
   of an isolated session and process group.
4. In the parent, measure elapsed time with a monotonic clock and reap the
   worker with `waitpid`.
5. At expiry, send TERM to the worker process group, allow a fixed one-second
   cleanup grace, then send KILL to the group and reap the leader.
6. If the worker exits first, preserve its exit status. If same-group
   descendants remain after the leader exits, clean them before returning so
   the completed job does not leave quota-burning children.
7. On INT, TERM, or HUP received by the supervisor, forward termination to the
   worker group, reap it, and return the corresponding signal-style status.

The configured budget is therefore an execution limit plus at most the fixed
termination grace and scheduler overhead. Exit 124 identifies expiry even if
the final cleanup required KILL.

### Nested per-call watchdogs

GNU `timeout` normally creates another process group. Nesting that behavior
inside the whole-job process group would let the inner group evade an outer
group signal.

While `job-worker.sh` is supervised, it exports an internal
`OMNILANE_JOB_SUPERVISED=1` marker. In that mode, `run_with_timeout` uses the
existing same-process-group Perl alarm backend even when GNU `timeout` is
installed. This keeps normal runner descendants inside the outer group.

The marker is internal runtime state, not user configuration. Without a
whole-job timeout, backend selection and existing exit behavior remain
unchanged.

Gemini's native `--print-timeout` remains unchanged; its process starts
inside the outer worker group.

## Dependency and failure behavior

The whole-job supervisor requires Perl with `POSIX::setsid` and
`Time::HiRes::CLOCK_MONOTONIC`. These are present in stock macOS Perl and the
Ubuntu CI image.

When `--job-timeout` is requested, dispatch checks the dependency before
starting the worker. A missing or unusable supervisor fails closed with a
readable error and exit 2; it never silently runs without the requested
budget.

The Perl helper is syntax-checked in CI and exercised directly on every CI
run. The feature does not select GNU `timeout` for outer supervision, so CI
cannot accidentally skip the macOS-relevant implementation.

## Error and exit behavior

- Invalid job-timeout flag or environment value: exit 2.
- Missing whole-job supervisor dependency: exit 2 before worker launch.
- Whole-job budget exhausted: exit 124.
- Worker finishes before the budget: preserve its existing exit status.
- Existing domain exits 3, 4, 5, 6, 86, and 87 remain unchanged unless
  whole-job expiry actually terminates the worker first.
- Background jobs persist the same final status, including 124, in their
  existing `exit` file.
- With no job timeout configured, existing timeout backend behavior remains
  unchanged, including 124 from GNU timeout and 142 from the Perl alarm.

Near the boundary, a worker reaped before expiry wins; otherwise expiry wins
and returns 124. No post-hoc wall-clock comparison rewrites a completed
worker's status.

## Test strategy

All production changes follow red-green-refactor.

### Resolution and injection tests

- disabled by default;
- global environment value;
- per-lane value beats global;
- command flag beats both;
- missing, zero, negative, over-nine-digit, and non-numeric values exit 2;
- metadata records numeric `job_timeout` or `null`;
- malicious values containing `$()`, array syntax, and arithmetic text exit 2
  without creating their marker files;
- static check confirms no `OMNILANE_JOB_DEADLINE` or runner deadline
  arithmetic is introduced.

### Supervisor tests

- `perl -c scripts/lib/job-timeout.pl`;
- direct helper invocation times out at 124;
- a fake worker that spawns a TERM-ignoring child is followed by TERM/KILL and
  leaves neither leader nor child alive;
- zero, negative, non-numeric, and missing seconds never launch the command;
- normal exit 0 and non-zero worker statuses are preserved;
- signal forwarding terminates the supervised group;
- tests exercise the Perl helper directly on Linux CI rather than relying on
  host backend discovery.

### End-to-end deadline tests

- a sleeping exec gate is stopped by the whole-job budget with exit 124;
- a shorter existing per-call watchdog can still finish first;
- fake Grok retries cannot multiply the whole-job budget;
- fake vote members and rounds cannot multiply the whole-job budget;
- Codex lock waiting cannot outlive the whole-job budget;
- background execution records exit 124 and leaves no supervised child alive;
- no-budget invocations retain existing output and status behavior.

Wall-clock assertions use generous upper bounds and also assert attempt, voter,
PID, and marker evidence to reduce timing-test flakiness.

### Regression checks

- full `bash tests/run.sh`;
- `bash -n` for shell files;
- `perl -c` for the supervisor;
- `shellcheck -S warning`;
- routing-table smoke test;
- `git diff --check`.

## Documentation

Update all five READMEs, `local.sh.example`, `commands/route.md`, and the
`omnilane` skill. Keep these terms distinct:

- `--timeout`: per CLI invocation hang guard;
- `--job-timeout`: optional worker-wide execution fuse.

Examples must not imply that 20 minutes is enough for a full repository audit.
For a fubon-autotrade-sized deep review, document an opt-in starting range of
2–4 hours (7200–14400 seconds), with a 30-minute per-call guard as a reasonable
starting point. These are recommendations, not hard-coded defaults.

## Compatibility

The feature is opt-in. Existing invocations and timeout selection remain
unchanged when it is disabled. Adding `job_timeout` to metadata is additive.

Enabling the feature intentionally adds a worker process boundary and forces
same-group per-call supervision so the outer process-group guarantee remains
true. That internal process topology is not a compatibility contract.

## Acceptance criteria

1. With no job timeout configured, the existing suite and observable behavior
   pass unchanged.
2. `--job-timeout 1` prevents Grok retry and vote fan-out from turning one
   second into multiple fresh whole-job budgets.
3. Lock wait and runner work are both inside the same timed worker boundary.
4. Whole-job expiry is exit 124 in foreground and background execution.
5. A timed-out job has no surviving same-group worker or vendor descendant
   after the one-second termination grace.
6. Malicious timeout text cannot reach shell arithmetic or execute commands.
7. CI directly exercises the stock-Perl supervisor and all existing checks.
