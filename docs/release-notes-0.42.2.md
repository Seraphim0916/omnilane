# Omnilane 0.42.2

This patch repairs the Grok single-shot reasoning-effort transport. It does not raise caller scores or remove the exact-AA downward-delegation gate.

## Changes

- Explicit Grok effort is forwarded as `--reasoning-effort VALUE`. The supported selector spellings are `low`, `medium`, `high`, and `xhigh`; invalid values fail before provider startup.
- Grok 4.6 entries in the default routing table explicitly select `high`.
- A scored Grok target requires a verified `cli_reasoning_effort` mapping and the exact `--reasoning-effort` flag. The existing host-local transport overlay validates host, snapshot, exact identity, evidence hashes, vendor, flag, and effort.
- Explicit effort on the live ACP path is rejected rather than discarded. This release does not add Grok work-mode network isolation on macOS.

## Verification boundary

The frozen AA registry and its approved SHA remain unchanged. Transport evidence belongs to the local host; the package does not ship a blanket assertion that every Grok model/effort combination is verified.

`request-selector-contract` proves how Omnilane selects the model and effort. It does not independently authenticate the upstream model's internal identity: `upstream_identity_verified` remains false. A successful reply alone is not an identity attestation.

For an existing local overlay, use `selector_type: cli_reasoning_effort`, `cli_flag: --reasoning-effort`, and matching model/effort identity only after checking the installed CLI and runner. Retain absolute evidence paths and SHA-256 hashes. Changed evidence requires re-verification; do not merely relabel an old mapping as verified. Select it with `--transport-overlay /absolute/overlay.json` or `OMNILANE_AA_TRANSPORT_OVERLAY`.

## Upgrade

After npm publication:

```sh
npm i -g omnilane@0.42.2
omnilane --version
```

Repo-symlink installations use the updated checkout. Do not rerun `install.sh` just to update the version. Local routing overrides take precedence; review any Grok entries still using `-`.

GitHub release, npm publication, local provider smoke, and Linux CI are separate verification surfaces. Release evidence must identify each result rather than equating one with the others.
