# Round 1 experiment: dispatch JSON inspection

Branch: `codex/idea-dispatch-json`

Commit: `632a936`

Status: PARTIAL. Functional and runtime acceptance pass. Local ShellCheck is
unverified because `shellcheck` is not installed on the current MacStudio.

## Hypothesis

A single versioned JSON envelope around the canonical human inspection output
can make `--list`, `--explain`, and `--validate` automation-safe without
duplicating route resolution or invoking a provider.

## Red/green evidence

- Red: `bash tests/run.sh` reported `50 passed, 1 failed`; both JSON list forms
  returned exit 2 before implementation.
- Green: `bash tests/run.sh` reported `51 passed, 0 failed` after implementation.
- Python unit discovery passed; seven real-browser UI cases were skipped by the
  existing local dependency gate. This branch does not change UI behavior.
- Bash syntax, Perl syntax, Python compilation, `git diff --check`, and the
  existing routing list smoke passed.

## Runtime evidence

An isolated `OMNILANE_HOME` exercised both JSON option positions for all three
inspection commands. The paired outputs were byte-equal, parsed with
`python3 -m json.tool`, reported `schema_version=1`, preserved existing exit
codes, and created no `jobs` directory.

## Adversarial evidence

- Tabs in routing data are JSON-escaped instead of emitted as raw control bytes.
- Invalid, missing, duplicate, and mixed JSON options fail before job state.
- A first adversarial matrix found that `--background --json --list` returned
  exit 2 with a generic `unknown flag` message. A regression test now proves it
  returns the unified inspection usage contract with exit 2 and no job state.
- Error paths from unavailable, unknown, and invalid routes remain valid JSON
  while preserving exit 4 or exit 2.

## Review

The JSON layer calls each canonical inspection function once and captures its
real output and exit code; it does not maintain a second route model. Duplicate
inspection execution in the general dispatch parser was removed during strict
review. `scripts/dispatch.sh` remains below 1,000 lines at 627 lines.

## Known limits

- The `lines` array intentionally preserves the versioned envelope while leaving
  each command's human line grammar unchanged. Consumers must not parse those
  strings as a second undocumented schema.
- ShellCheck must run before this branch can receive an overall PASS.
- No provider or paid model call was used; this inspection feature is explicitly
  offline and read-only.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates the commit, revert `632a936` to remove this feature.
`main` remains unchanged pending Vincent's final judgment.
