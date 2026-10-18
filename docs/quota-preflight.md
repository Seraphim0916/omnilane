# Quota preflight (unreleased)

Before a new dispatch, Omnilane can exclude Codex when a fresh **weekly** quota
snapshot reports at least 90% used. It reuses the effective lane's candidate
chain, in its existing order, and the existing exact-AA, inherited-ceiling,
transport, executable, mode and native capability checks. It never adds a model
or vendor to a lane. A host-local chain that omits a temporarily unavailable
candidate stays authoritative.

This is a pre-dispatch decision only. A provider error after dispatch still
stops with the existing evidence; this feature adds no cross-provider retry.
Workers, live turns, completion and resume paths do not re-read quota snapshots.

## Snapshot input

The first version reads only `$OMNILANE_HOME/quota/codex.json`
(`~/.omnilane/quota/codex.json` by default). For example:

```json
{
  "vendor": "codex",
  "window": "weekly",
  "used_percent": 92.5,
  "captured_at": "2026-10-18T10:00:00+08:00",
  "resets_at": "2026-10-21T10:00:00+08:00",
  "source": "operator-weekly-snapshot"
}
```

`used_percent` must be a finite JSON number between 0 and 100, not a boolean or
numeric string. Both timestamps must be ISO 8601 with a timezone. `source` is a
short, printable, non-secret label (maximum 128 characters), not a credential or
raw provider response. Unknown fields are not copied to job records. Files over
65,536 text characters, symlinks, unreadable files and invalid or missing fields
are unknown.

A snapshot older than 1,800 seconds is **unknown**, with `freshness: "stale"` in
the evidence. A future capture time is unknown; an elapsed reset time is stale.
Missing/invalid data never excludes a vendor and never means quota is sufficient.
The five-hour window has no threshold. Other vendors have no quota policy in
this version. Snapshot collection/refresh is **future work**: this feature does
not inspect provider configuration, credentials, cookies or session directories.

## Configuration

These environment variables follow the existing `local.sh` configuration style;
they can also be set per command. This change does not write any local settings.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNILANE_QUOTA_ENABLED` | `1` | Set exactly `0` to disable preflight and its output/decision record. |
| `OMNILANE_QUOTA_DIR` | `$OMNILANE_HOME/quota` | Snapshot directory; filenames are `<vendor>.json`. |
| `OMNILANE_QUOTA_THRESHOLD_PERCENT` | `90` | Codex weekly usage threshold, inclusive, from 0 to 100. |
| `OMNILANE_QUOTA_MAX_AGE_SECONDS` | `1800` | Maximum snapshot age, inclusive, nonnegative seconds. |

Invalid threshold or age configuration makes the quota status unknown; it does
not disable existing dispatch safety checks. Disabling preflight restores the
previous routing decision output byte for byte, including stderr.

## Decisions and explicit targets

The original candidate is the target the ordinary resolver would select before
quota filtering. If it is near its limit, all candidates from that vendor are
excluded during a second pass through the **same effective chain**. Other gates
still run, including native capability matching. A vote candidate containing
the excluded vendor is not a compliant alternative. If the original candidate
is a vote containing Codex, its Codex participant's snapshot triggers the same
filter. `off` is not an alternative.

If no compliant alternative remains:

- `hardest-coding` and `hard-judgment` exit nonzero, before job creation, with one
  machine-readable JSON line on stderr. Its code is
  `quota_near_limit_no_alternative`; the operator must decide what to do next.
- Other lanes keep the original candidate and record `quota_near_limit` in stderr
  and the job's quota decision record.

Explicit `--vendor`, `--model` or `--effort` preserves the requested target and
only reports quota status on stderr. Existing `--target-config` pins receive the
same treatment. All ordinary safety gates still apply. Native `--inherit` does
not resolve a lane target and is unchanged.

## Inspection and audit

Use the usual `--dry-run`, caller identity and mode flags; no provider is invoked
and no job is created. Shell plans add one `quota_preflight=<JSON>` line; native
plans include the same object. It contains:

- decision time, lane, job ID (`null` before job creation);
- `original` and `final` vendor/model/effort triples;
- snapshot source, capture/reset times, age, freshness, threshold and usage;
- effective `status` (`fresh` or `unknown`), action and exclusion reason.

Actions are `unchanged`, `explicit`, `rerouted`, `quota_near_limit` or `refused`.
Refusal uses `final: null` and includes the object in its JSON error. Both CLI and
native jobs persist the decision in `jobs/<job-id>/quota-preflight.json`, with
the allocated job ID and file mode 600. Native handoff plans also retain the
same `quota_preflight` object. The existing `meta.json` schema and job audit
remain unchanged. The snapshot is read once for that decision; existing jobs
are not reassigned when it changes.

Offline regression coverage is in `tests/test_quota_preflight.py`, discovered by
the standard Python unittest gate. Provider tools are replaced with fixture
executables, and HOME, temporary files and routing overlays are isolated.
