# Omnilane 0.42.5

This release stops one vendor's CLI upgrade from refusing every vendor's dispatch,
makes that failure visible in `omnilane doctor`, and stops the probe harness from
signing evidence it never read. Routing, scores, and the frozen AA registry are
unchanged.

## Why

On 2026-09-09 `agy` was upgraded from 1.1.27 to 1.1.28. That changed its sha256,
and `apply_transport_overlay` compared every evidence hash in one loop and raised
on the first mismatch, so `load_registry` failed and **every** dispatch was
refused:

```json
{"allowed":false,"code":"invalid-policy-input","message":"transport contract evidence changed"}
```

Only the seven gemini mappings depended on that binary; the other forty-two were
collateral. `omnilane doctor` reported 19 passed, 0 failed throughout, because no
check ever loaded the overlay.

Re-probing showed the upgrade had changed nothing observable: all six agy
selectors still answered correctly. The outage was entirely the blast radius of a
config linter that had been given the authority of a security gate.

The same investigation found six `claude-fable-5-1` configurations that had been
unusable since 2026-09-07. Their probes had failed with a quota refusal, and
`build_overlay.py` simply omitted them — no record, no warning, nothing for an
operator to notice.

## Changes

### Evidence staleness is per vendor

- Overlay `evidence[]` entries take an optional `vendor` tag. A tagged entry whose
  hash drifts, or whose file no longer exists, marks that vendor stale and skips
  the `verified` upgrade for its mappings; `decide()` then returns
  `unknown-target-runtime` for that vendor alone.
- Untagged entries keep the previous global fail-closed behaviour, so overlays
  built before the tags exist are unaffected.
- Missing files are treated as staleness, not corruption: the codex and claude
  evidence paths embed version directories, so their upgrades delete the file
  rather than change its digest.
- Structural overlay checks — schema, snapshot, host, exact identity, selector
  type, effort alignment — remain hard failures.

### Doctor loads the overlay

- A new `transport-overlay` check loads the configured overlay through
  `aa_policy.load_registry` and names the offending file and vendor on failure.
  It reports per-vendor verified counts on success and warns when a vendor has
  degraded. `live-capable` is unchanged: it answers whether a CLI supports a live
  session, which stays true while the gate refuses the vendor.

### Probes record a verdict

- `probe.py` now derives a `verdict`, `verdict_reason`, `observed_model`, and
  `probed_at` from the raw evidence through a pure function, per vendor:
  - Claude responses are judged on `modelUsage` — the billed model the CLI
    reports — plus `is_error`, and are failed when stderr shows the CLI silently
    substituted the default effort for an unknown `--effort`. Effort is half of a
    scored identity, so that path would otherwise certify a mapping at the wrong
    tier while exit status, `is_error`, `modelUsage`, and the expected token all
    look correct.
  - grok and agy reject invalid input outright, so exit status and a clean stderr
    are sufficient.
  - codex prints a banner to stderr on every run, so stderr is recorded for review
    rather than treated as failure.
- `build_overlay.py` refuses to sign a non-passing probe and records it in a new
  `unproven[]` block with its reason, so a transient quota refusal is visible
  instead of silently dropping configurations. Evidence predating the verdict
  field is still signed, with a warning naming each legacy entry.
- `build_overlay.py` and `probe.py` moved from an untracked `.rollback` sweep
  directory into `scripts/lib/`, and take `--root`; overlay rebuilds no longer
  depend on a directory that a cleanup can delete.

## Verification boundary

A passing probe proves the CLI accepted the selector and, for Claude, that the
billed model matches the request. It still does not certify upstream provider
identity, and `upstream_identity_verified` remains `false`. grok, agy, and codex
expose no equivalent of `modelUsage` in the evidence captured so far.

The frozen AA registry and its approved SHA are unchanged. Coverage remains 78
scored targets, and the host overlay still verifies 49 mappings — codex 26,
claude 11, gemini 7, grok 5.

## Upgrade

Existing overlays keep working untouched. To gain per-vendor degradation, rebuild
the overlay with the new `build_overlay.py` so its evidence carries vendor tags.
Run `omnilane doctor` afterwards and confirm the `transport-overlay` check reports
the vendor counts you expect.
