---
description: Inspect omnilane background jobs, statistics, and evidence-gated routing recommendations
---

Check omnilane background jobs.

Input: `$ARGUMENTS` — empty for a listing; a job id optionally prefixed with
`status`/`result`; or `stats`/`recommend` with their documented filters.

Run from the plugin root:
- listing: `scripts/jobs.sh list`
- status:  `scripts/jobs.sh status <id>`
- result:  `scripts/jobs.sh result <id>` (relay output; include stderr if the
  exit code is non-zero)
- statistics: `scripts/jobs.sh stats [--last N] [--lane L] [--vendor V]`
- recommendation: `scripts/jobs.sh recommend [--last N] [--lane L] [--min-samples N]`

Recommendations are read-only historical evidence. Report the sample threshold
and do not edit routing unless the user separately requests that change.
