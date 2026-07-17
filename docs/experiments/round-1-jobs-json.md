# Round 1 experiment: private-by-default jobs JSON

Branch: `codex/idea-jobs-json`

Commit: `19b6a44`

Status: PARTIAL. Functional and runtime acceptance pass. Local ShellCheck is
unverified because `shellcheck` is not installed on the current MacStudio.

## Hypothesis

Omnilane can expose versioned JSON for local job list, status, result metadata,
and aggregate statistics while preserving existing exit codes and keeping task,
output, and stderr bodies private unless the user chooses the existing text
`result` command.

## Red/green evidence

- Red: the shell suite reported `50 passed, 1 failed`; every new JSON form was
  rejected as unknown usage before implementation.
- Green: all 51 shell tests passed in two isolated batches (`33 + 18`). The
  split avoids a unified PTY interaction between existing process-group timeout
  tests; every registered shell test ran once across the two batches.
- All 36 Python tests passed, including the real browser behavior cases.
- Bash syntax, Perl syntax, Python compilation, `git diff --check`, and the
  existing routing-list smoke passed.

## Runtime evidence

The public `bin/omnilane jobs` entrypoint ran against an isolated
`OMNILANE_HOME`. `list`, `status`, `result`, and `stats` emitted parseable schema
version 1 documents. A completed job with exit 7 made JSON result inspection
return exit 7, reported only body availability, and did not emit the task,
output, or stderr canaries. Empty list and empty stats stores emitted valid empty
arrays with exit 0.

## Adversarial evidence

- Prefix, suffix, and interleaved `--json` positions normalize to the same
  command contract; invalid IDs return a valid JSON error while preserving exit
  2.
- Job IDs remain allow-listed, job and metadata symlinks are never followed,
  recorded exit and PID bounds remain enforced, and malformed metadata is not
  echoed.
- Invalid UTF-8 metadata is represented as unavailable instead of corrupting the
  JSON document.
- Result JSON reports `output_available` and `stderr_available` booleans but
  never reads either body. The existing non-JSON result command is unchanged.
- Initial adversarial review found that Bash 3.2 expands an empty array as an
  unbound variable under `set -u`, causing empty `stats --json` to print invalid
  JSON and still exit 0. A regression test now covers the empty store, and the
  renderer explicitly emits `[]` for both count arrays.

## Review

The implementation reuses the existing bounded readers for exit, PID, and
public metadata. It adds a presentation layer after those safety checks rather
than reading job artifacts through a second path. Public metadata is carried as
an escaped string because the legacy reader intentionally accepts any safe
single-line value; consumers are not asked to trust it as nested JSON.

## Known limits

- JSON list output remains bounded to the same newest 20 jobs as text list.
- Metadata is exposed in JSON only when `iconv` can validate UTF-8. Without that
  local utility the field is marked invalid so the document remains safe.
- ShellCheck must run before this branch can receive an overall PASS.
- No provider or paid model call was used; all fixtures and runtime checks were
  local.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates the commit, revert `19b6a44` to remove this
feature. `main` remains unchanged pending Vincent's final judgment.
