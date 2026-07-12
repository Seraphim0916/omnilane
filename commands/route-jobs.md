---
description: Check omnilane background dispatch jobs (list, status, result)
---

Check omnilane background jobs.

Input: `$ARGUMENTS` — empty for a listing, or a job id (optionally prefixed
with `status`/`result`).

Run from the plugin root:
- listing: `scripts/jobs.sh list`
- status:  `scripts/jobs.sh status <id>`
- result:  `scripts/jobs.sh result <id>` (relay output; include stderr if the
  exit code is non-zero)
