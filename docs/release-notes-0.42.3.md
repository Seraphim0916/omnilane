# Omnilane 0.42.3

This patch is documentation only. It closes a gap in the dispatch skill that let a
model caller reach a refusal without knowing how to satisfy the gate. No routing
table, AA score, gate decision, runner, or CLI behaviour changes.

## Why

The skill described the caller-context schema in the frozen exact-AA downward gate
section, but the dispatch quick reference — the line most callers copy — omitted
`--caller-context` entirely. A caller following the quick reference was refused
with `missing-caller-context` and had no worked example to recover from. The two
refusal codes were also easy to confuse, and the wrong reading of the second one
invites editing the frozen registry, which fails the whole gate closed.

## Changes

- The dispatch command signature in the quick reference now carries
  `--caller-context FILE`, with a note that a model caller without it is refused
  before a job exists.
- The gate section gains a complete caller-context JSON example and states that
  `caller` must reproduce one `scored_configs` row exactly and `snapshot_id` must
  equal the registry's own `snapshot.id`.
- New guidance for the common case where a harness reports a model but no effort:
  read the exact flags from the launching process by walking your own ancestor
  chain (`ps -o ppid=,comm= -p <pid>` upward, then `ps -o args= -p <ancestor>`),
  and match the ancestor chain rather than the first same-named process on the
  host, because a second session of the same CLI is common and its flags are not
  yours.
- Declaring the lowest-scoring row of your model is documented as the fallback
  when that lookup genuinely yields nothing, not as the first move. An
  unnecessarily low ceiling silently closes lanes and pushes the question back
  onto the operator.
- `missing-caller-context` and `runtime-mapping-unverified` are distinguished with
  the fix for each. The second is resolved by a `--transport-overlay` entry backed
  by real evidence, never by editing the frozen registry whose SHA-256 is pinned in
  `scripts/lib/aa_policy.py`.

## Verification boundary

Reading `--model` and `--effort` from an ancestor process is request-selector
evidence of the same class the transport overlay carries. It does not certify the
upstream provider's identity, and this release makes no such claim.

The frozen AA registry and its approved SHA are unchanged. Score coverage is
unchanged at 78 scored targets, one scored reference-only entry, and 10 unknown
configurations. The prohibition on a model caller asserting
`--operator-asserted-human` for itself is restated, not relaxed.

## Upgrade

After npm publication, run `npm i -g omnilane@0.42.3`. An existing repo-symlink
installation can update its checkout and verify `omnilane --version` without
rerunning installation. GitHub release and npm publication remain separate
verification surfaces from Linux CI.
