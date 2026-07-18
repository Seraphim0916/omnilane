# feat/jobs-filters — filter the job listing

Branch: `feat/jobs-filters` (off `main` @ `c45955e`, v0.8.3)
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

`jobs list` showed the 20 most recent jobs with no way to narrow them. This
branch adds three filters (human and `--json`):

- `--lane L` — only jobs whose recorded lane is `L`
- `--vendor V` — only jobs dispatched to vendor `V`
- `--status running|done` — only running (no exit recorded) or done jobs

Filters compose. The listing now scans **all** jobs and shows up to 20 matches
(previously it capped the scan at 20 before display, which would have hidden
matches beyond the 20 most recent). Unfiltered `list` output is unchanged.

## Implementation

- Lane/vendor come from each job's `meta.json`, parsed with the existing
  `parse_stats_metadata` helper (`STATS_LANE`/`STATS_VENDOR`); a job with missing
  or unparseable metadata cannot match a `--lane`/`--vendor` filter and is
  excluded. Status comes from the same `exit`-file presence check `list` already
  uses.
- Filter values are validated: `--lane` against `^[a-z][a-z0-9-]*$`, `--vendor`
  against the known vendor set, `--status` against `running|done`. Invalid values
  and a missing flag argument exit 2.

## Red/green oracle

New test `test_jobs_list_filters` builds four fixture jobs (varying
lane/vendor/status) and asserts each filter and the combined filter select
exactly the right ids in both human and `--json` output, plus that invalid
`--lane`/`--vendor`/`--status` and a valueless `--lane` all exit 2.

- RED before implementation: the `list` arm rejected extra args (`$# -eq 1` →
  usage) → `--lane triage wrong set: usage:` (`70`→`69 passed, 1 failed`).
- GREEN after: `70 passed, 0 failed`.

## Real runtime evidence

- `bash -n scripts/jobs.sh` → OK; `shellcheck -S warning scripts/jobs.sh` → exit 0
- `bash tests/run.sh` (Bash 5) → `70 passed, 0 failed`
- `/bin/bash tests/run.sh` (macOS Bash 3.2) → `70 passed, 0 failed`

Out-of-harness smoke through the real `omnilane jobs list` entrypoint (three
fixture jobs):

```
--status running -> job2, job3            (excludes the done job1)
--vendor codex   -> job1, job3            (excludes claude job2)
--lane triage --status done -> job1 only
--json --lane bulk-mechanical -> valid envelope, job3 only
--vendor mystery -> "invalid --vendor value", exit 2
```

Result: **VERIFIED** — real entrypoint, human + JSON, composed filters, adversarial
rejection.

## Note (unrelated flaky test)

During this branch's first RED run, the pre-existing `openrouter runner contract`
test failed once on "request/response temp files must be cleaned up", then passed
on an immediate re-run and on every subsequent run (bash 5 and bash 3.2). This is
a pre-existing environmental timing flake in that runner's temp-file cleanup, not
introduced by this branch (which does not touch the openrouter runner). Flagged
for awareness; not fixed here.

## Docs

Added a `jobs list [--lane L] [--vendor V] [--status running|done]` line to all
five READMEs' command reference and an `## [Unreleased]` CHANGELOG entry.

## Recommendation

Select. Useful listing ergonomics, reuses the existing metadata parser and state
model, fully offline-verified incl. real runtime.

## Follow-up commit — jobs stats filters

A second commit on this branch extends the same `--lane`/`--vendor` filters to
`jobs stats`. The stats loop now resolves each job's lane/vendor first and skips
non-matching jobs before they count toward any aggregate, so the success rate and
lane/vendor counts are recomputed for the filtered subset. `--last` still bounds
the sample; the filters apply within it. Unfiltered `stats` is byte-for-byte
unchanged (metadata still drives `invalid_metadata`; `sampled` still counts all).

New test `test_jobs_stats_filters` builds three fixture jobs and asserts the
sampled count and JSON aggregates for `--vendor`, `--lane`, and the combined
filter, plus exit-2 on invalid values. Suite: `71 passed, 0 failed` (Bash 5 and
Bash 3.2); `bash -n` + `shellcheck -S warning` clean.

Real smoke through `omnilane jobs stats`:

```
all                        -> sampled=3 succeeded=2 failed=1 success_rate=66%
--vendor codex             -> sampled=2 succeeded=1 failed=1 success_rate=50%
--lane triage              -> sampled=2 succeeded=2 failed=0 success_rate=100%
--lane triage --vendor codex -> sampled=1 success_rate=100%
--json --vendor codex      -> {"sampled":2,...,"vendors":[{"name":"codex","count":2}]}
--vendor mystery           -> invalid --vendor value, exit 2
```

Result: **VERIFIED** — the success rate and aggregates are correctly recomputed
per filter over the real entrypoint, JSON included.
