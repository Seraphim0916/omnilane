# Round 1 experiment: side-effect-free dispatch dry run

Branch: `codex/idea-dispatch-dry-run`

Commit: `87c08e2`

Status: PARTIAL. Functional and runtime acceptance pass. Local ShellCheck is
unverified because `shellcheck` is not installed on the current MacStudio.

## Hypothesis

Omnilane can resolve the same route, overrides, execution mode, work directory,
per-call timeout, whole-job timeout, and expected side effects as a real dispatch
while stopping before stdin consumption, provider invocation, or job-state
creation.

## Red/green evidence

- Red: `bash tests/run.sh` reported `50 passed, 1 failed`; `--dry-run` was an
  unknown flag with exit 2.
- Green: `bash tests/run.sh` reported `51 passed, 0 failed`.
- Bash syntax, Perl syntax, Python compilation and unit discovery, routing list
  smoke, and `git diff --check` passed.
- Seven existing real-browser UI cases were skipped by the local dependency
  gate. This branch does not change UI behavior.

## Runtime evidence

`bin/omnilane route --dry-run` ran against an isolated `OMNILANE_HOME` with
work mode and explicit per-call and whole-job timeouts. It returned exit 0,
reported the resolved exec route and side-effect decision, left the routing file
hash unchanged, created no jobs directory, invoked no provider, and did not emit
the task canary.

## Adversarial evidence

- Disabled, unavailable, invalid-timeout, and nested-depth paths retain exits 3,
  4, 2, and 86 before state creation.
- A stdin task source is reported without reading stdin or exposing task bytes.
- Workdir symlinks are reported safely and receive no writes.
- Terminal-control bytes in model overrides are shell-quoted and never emitted
  raw.
- Initial adversarial review found that dry run accepted a symlinked jobs store
  even though real dispatch rejects it. The jobs-store safety check now runs
  read-only before the dry-run exit; the regression returns exit 1 and proves no
  write through the foreign symlink.

## Review

The implementation reuses the canonical dispatch path through route, override,
timeout, and dependency resolution. It adds one terminal plan renderer and one
boolean parser flag, then exits at the existing state boundary. It does not
duplicate route resolution or runner behavior. `scripts/dispatch.sh` remains
below 1,000 lines at 596 lines.

## Known limits

- The plan is human-readable shell-quoted output, not JSON. The independent
  `codex/idea-dispatch-json` branch explores a versioned machine envelope.
- ShellCheck must run before this branch can receive an overall PASS.
- No provider call was used; provider execution is exactly the side effect this
  feature promises to avoid.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates the commit, revert `87c08e2` to remove this
feature. `main` remains unchanged pending Vincent's final judgment.
