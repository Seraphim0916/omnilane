# feat/mcp-readonly-tools — MCP read-only introspection parity

Branch: `feat/mcp-readonly-tools` (off `main` @ `c45955e`, v0.8.3)
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

The `omnilane mcp` stdio server previously exposed only `route`, `jobs_status`,
`jobs_result`, and `list_lanes`. Any MCP host wanting the CLI's other offline,
read-only surfaces (`--explain`, `--validate`, `--dry-run`, `jobs list`,
`doctor`) had to shell out separately. This branch adds five read-only tools so
the MCP surface mirrors the CLI's read-only surface:

| Tool | Backing CLI | Args | Provider call | Job state |
| --- | --- | --- | --- | --- |
| `explain` | `dispatch.sh --explain LANE [--json]` | `lane` (req), `json` | never | none |
| `validate` | `dispatch.sh --validate [--json]` | `json` | never | none |
| `dry_run` | `dispatch.sh --dry-run … LANE TASK` | `lane`+`task` (req), `mode`/`workdir`/`vendor`/`model`/`effort`/`timeout` | never | none |
| `jobs_list` | `jobs.sh [--json] list` | `json` | never | none (metadata only) |
| `doctor` | `doctor.sh [--json]` | `json` | never | none |

All five carry `readOnlyHint: true`. `dry_run` requires `workdir` only when
`mode` is `work` (same contract as `route`), so the plan resolves realistically
without ever invoking a provider.

## Red/green oracle

New test `test_mcp_readonly_tools` in `tests/run.sh`:
- RED: added the assertions before implementing; the suite failed with the new
  tools absent from `tools/list` (`70 passed`→`69 passed, 1 failed`).
- GREEN after implementation in `bin/omnilane-mcp`: `70 passed, 0 failed`.

The test drives the real node server over stdio JSON-RPC against an isolated
`OMNILANE_HOME` whose `probe` lane uses an `exec` gate (deterministic
availability on any host) with the twelve default lanes forced `off`.

## Adversarial coverage (all return `isError`)

- `explain` with no `lane` (missing required arg)
- `explain` with `lane: "Bad_Upper"` (fails `^[a-z][a-z0-9-]*$`)
- `dry_run` with `mode: work` and no `workdir`
- `jobs_list` with an unexpected argument

## Privacy / side-effect invariants (asserted)

- The `exec` gate marker file is never written → no read-only tool executed work.
- No `jobs/` directory is created → no job state from explain/validate/dry_run.
- `dry_run` never echoes task content (inherited from `dispatch.sh --dry-run`).

## Real runtime evidence

Full CONTRIBUTING check set, MacStudio, all green:

- `node --check bin/omnilane-mcp` → OK
- `bash -n tests/run.sh` → OK
- `bash tests/run.sh` (Bash 5) → `70 passed, 0 failed`
- `/bin/bash tests/run.sh` (macOS Bash 3.2) → `70 passed, 0 failed`
- `python3 -m unittest discover -s tests` → `Ran 36 tests … OK`
- `shellcheck -S warning bin/omnilane scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh install.sh` → exit 0, no warnings

Out-of-harness smoke against the **real default table** (isolated
`OMNILANE_HOME`, real node server binary, no provider call):

```
server-exit=0
tools(9): route,jobs_status,jobs_result,list_lanes,explain,validate,dry_run,jobs_list,doctor
jobs_list -> (empty)
doctor -> PASS  dispatch     …/scripts/dispatch.sh is executable
explain triage -> lane: triage
dry_run triage -> dry_run=yes
validate -> PASS hardest-coding selected=1/2 vendor=codex
```

Result: **VERIFIED** (not PARTIAL) — real server binary exercised end to end.

## Docs

Updated the current-surface MCP tool-list sentence in all five READMEs and added
an `## [Unreleased]` CHANGELOG entry. Historical "What's new in v0.8.3" notes are
left intact (they correctly describe the 4 tools that shipped in 0.8.3). VERSION,
plugin manifests, and what's-new titles are untouched — no release action.

## Known risks / notes

- `doctor`/`validate`/`explain` exit non-zero for genuine FAIL/unavailable
  states; the MCP layer surfaces that as `isError` with the full diagnostic text
  in the message (same as the pre-existing `list_lanes`/`jobs_status` behavior).
  This is intentional and consistent, not a regression.
- `json` params default to `false` (human text) to match `list_lanes`; hosts opt
  into the versioned JSON envelope with `json: true`.

## Recommendation

Select. Pure additive read-only surface, mirrors an already-shipped CLI, full
offline verification incl. real runtime, no new dependencies, no provider calls.
