# Omnilane 0.42.4

This patch fixes the user-facing quickstart. It changes no routing, scoring, gate,
runner, or CLI behaviour.

## Why

0.42.3 documented `--caller-context` inside the dispatch skill, which is what a
model driving omnilane reads. It left the READMEs' 60-second start untouched —
and that is the path a new install actually takes. A user who ran

```bash
npm i -g omnilane
omnilane route hardest-coding "fix the flaky auth token refresh"
```

was refused with `missing-caller-context`, and no README section explained the
flag that resolves it. The gate was working as designed; the documentation simply
never told a first-time user how to satisfy it.

## Changes

- The 60-second start in all five READMEs asserts the human operator once with
  `OMNILANE_AA_OPERATOR_ASSERTED_HUMAN=1` before the first `omnilane route`.
- A note after the quickstart explains why a dispatch must say who is asking:
  a human at a terminal asserts it with that variable or `--operator-asserted-human`
  per call; a model driving omnilane cannot assert it for itself and passes
  `--caller-context FILE` with its exact vendor, model, and effort instead; with
  neither, the dispatch is refused before any job is created.
- The `dispatch.sh` synopsis in the command reference now shows
  `[--caller-context FILE | --operator-asserted-human]`.

## Verification boundary

`--operator-asserted-human` is cooperative operator metadata. It is not automatic
model detection and not OS authentication, and this release does not change that.
A model caller still must not assert it on its own behalf.

The frozen AA registry and its approved SHA are unchanged. Coverage remains 78
scored targets, one scored reference-only entry, and 10 unknown configurations.

## Upgrade

After npm publication, run `npm i -g omnilane@0.42.4`. An existing repo-symlink
installation can update its checkout and verify `omnilane --version` without
rerunning installation. GitHub release and npm publication remain separate
verification surfaces from Linux CI.
