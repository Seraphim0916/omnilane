# Round 1 experiment: read-only job store audit

Branch: `codex/idea-jobs-audit`

Commit: `0f4cc28`

Status: PARTIAL. Functional, adversarial, regression, and runtime acceptance
pass. Local ShellCheck is unverified because `shellcheck` is not installed on
the current MacStudio.

## Hypothesis

A bounded `jobs.sh audit [--last N]` command can prove the local job store still
meets Omnilane's integrity and privacy invariants without printing or modifying
private task and result content.

## Red/green evidence

- Red: the complete shell suite reported `50 passed, 1 failed`; the new clean
  audit fixture received usage exit 2 because `audit` did not exist.
- Green: the complete shell suite passed `51 passed, 0 failed` after the final
  adversarial fixes.
- The complete Python suite passed `36 passed, 11 subtests passed`.
- Bash syntax, Perl syntax, Python compilation, and `git diff --check` passed.

## Runtime evidence

The public `bin/omnilane route --background` path created a real local job
through an isolated fake exec gate. After completion, the public
`bin/omnilane jobs audit --last 10` path reported that generated job as `PASS`
with `sampled=1 passed=1 failed=0 findings=0`. No provider or network call was
made.

Eight concurrent public audits of the same completed store all returned zero.
An absent store returned the stable empty summary with no filesystem write.

## Adversarial evidence

- Store and job directories must be exact mode 700; regular artifacts must be
  exact mode 600.
- Invalid job names, non-directory entries including a FIFO, job and artifact
  symlinks, nested directories, unsafe artifact types, missing task/PID files,
  malformed PID/exit records, and changed job paths fail closed.
- Initial implementation reused the stats metadata prefix parser. Review found
  that JSON-shaped corrupt data could pass a prefix-only check. A dedicated
  exact generated-schema parser and regression now reject both plain garbage
  and a valid-looking prefix with a corrupt suffix.
- Output contains only validated job IDs and stable finding codes. Fixtures
  prove task/result strings never appear, and pre/post hashes prove audit does
  not alter regular artifacts.
- `--last` values 0, above 10000, and nonnumeric fail with usage exit 2.

## Review

The command reuses the existing bounded readers for exit, PID, and public
metadata, then applies stricter release-audit invariants. It rechecks every
selected job path before inspection and never follows a symlink intentionally.
It adds no lock, cleanup path, provider call, or job artifact.

## Known limits

- The default sample is the newest 100 valid job IDs; `--last` can raise this to
  10000. Store-level invalid entry names are checked regardless of the sample.
- Filesystem access-control lists and hard-link identity are not currently
  reported; exact POSIX mode and file type are the portable enforced boundary.
- A hostile process with concurrent write access can still race portable path
  checks. The command fails closed on detected replacement but does not claim
  transactional filesystem isolation.
- ShellCheck must run before this branch can receive an overall PASS.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates `0f4cc28`, revert that commit to remove the
feature. `main` remains unchanged pending Vincent's final judgment.
