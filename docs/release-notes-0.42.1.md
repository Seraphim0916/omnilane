# Omnilane 0.42.1

Omnilane 0.42.1 is a test-fixture and packaging patch for the already-published 0.42.0 exact-AA release. It does not rewrite the `v0.42.0` tag or weaken production routing policy.

## Fixes

- Legacy routing and Grok-readiness tests now explicitly declare a synthetic-human caller. The fixtures no longer depend on ambient caller metadata, while real model calls without exact identity continue to fail closed.
- The cross-vendor encoded-effort lineage spy uses portable `#!/usr/bin/env python3` and explicit `--background --single-shot` followed by bounded job completion waiting. This isolates the one-shot provider fixture from Gemini's default live/FIFO lifecycle while retaining the exact `--model gemini-3.8-flash-high` selector, model caller/child ceiling, and no-human-exemption assertions.
- The npm package points at these 0.42.1 notes and retains all five README translations, the AA policy, and the native/completion-wakeup protocol documents.

## Policy boundary

The approved exact-AA registry SHA pin, missing-identity denial, downward score ceiling, child caller context, retry-lineage intersection, and model-retry human-exemption rules are unchanged. Registry accounting remains:

- 78 scored eligible configurations;
- 1 scored reference-only comparison entry;
- 10 unknown configurations.

## Upgrade

After npm publication:

```sh
npm i -g omnilane@0.42.1
omnilane --version
```

For an existing repo-symlink installation, update the checkout and run `omnilane --version`. Do not rerun `./install.sh` unless intentionally reviewing and changing integration wiring. A GitHub release does not by itself prove npm publication.

## Verification target

The patch release gate is the complete CI Python discovery command, the full shell suite, package/release policy checks, and a smoke test of the CLI extracted from the built npm tarball. Local preparation records are not packaged release evidence. Published-platform verification must come from the release's GitHub Actions run; local checks alone do not establish a Linux CI pass.
