# Omnilane 0.42.0

Omnilane 0.42.0 adds a native-first executor, a frozen exact-AA downward-delegation gate, and a supervised Codex completion-wakeup workflow. Routing still preserves an exact vendor/model/effort choice; execution may use a compatible caller-owned native agent or the established CLI path.

## Highlights

### Native-first, caller-owned execution

- `--executor auto` selects native execution only from an explicit capability context that matches model, effort, mode, workdir, tools, isolation, lifecycle, and strategy.
- A native route creates a pending handoff. The caller performs the declared `collaboration.spawn_agent` or explicit `collaboration.followup_task` action, waits for the real result, verifies it, and records a sanitized completion with `omnilane jobs --json complete-native`.
- `--executor native` fails on missing or incompatible capability. `--executor cli` forces the existing terminal path. Automatic fallback does not substitute a different vendor, model, or effort.
- Existing-agent reuse is explicit: the caller must have observed the exact agent idle, consent to preserved context, and recheck the state immediately before follow-up.

Protocol: [`native-executor.md`](native-executor.md)

### Frozen exact-AA downward delegation

- `config/aa-model-policy.json` is the checked-in approved AA v4.2 registry, anchored by an implementation-owned SHA-256 check.
- Every provider attempt is gated against the lower of the current caller score and inherited ceiling.
- Child caller context records the exact selected target. Retry intersects the current caller with the original authorizer ceiling and revalidates the unchanged target configuration.
- A model retry does not inherit an earlier human exemption; missing current caller identity fails closed.

The registry contains 78 scored configurations, one scored reference-only comparison entry, and 10 unknown configurations. The reference entry is not an eligible delegation target. This is policy coverage, not a claim that all configurations are installed, reachable, or runnable.

### Codex completion wakeup

- `scripts/completion-wakeup.py` prepares a controller-bound run and exact job allowlist, records the scheduler receipt, polls for terminal events, and records delivery and acceptance separately.
- Finished jobs are accepted only after the controller inspects public results and required verification; process exit alone is not acceptance.
- Closing a run requires the actual automation pause receipt. A later run receives a fresh run ID while historical IDs remain rejected.

Protocol: [`completion-wakeup.md`](completion-wakeup.md)

This feature uses scheduled heartbeat polling. It is not an instant push channel, and it requires a caller that can register the callback. Without that surface, the controller keeps waiting directly.

## Verification used for this release

- Accepted independent full suite: **124 passed, 0 failed**.
- Accepted native executor suite: **47/47 passed**.
- The counts overlap and are not combined.
- Accepted runtime evidence covered an actual explicit native-reuse handoff/completion, exact-AA allow/deny paths, and registered completion delivery/acceptance lifecycle.

The completion record is caller-attested and does not independently certify the upstream model identity. Native agent capacity and cold-start availability remain host/runtime properties, not release guarantees.

## Package contents

The npm package includes:

- `config/aa-model-policy.json`
- `scripts/lib/native.py`
- `scripts/lib/aa_policy.py`
- `scripts/lib/aa_retry.py`
- `scripts/completion-wakeup.py`
- `docs/native-executor.md`
- `docs/completion-wakeup.md`
- this release note

## Upgrade

After npm publication:

```sh
npm i -g omnilane@0.42.0
omnilane --version
```

For an existing repo-symlink installation, update the checkout to the released revision and verify `omnilane --version`; the command and skill links already follow that checkout. Run `./install.sh` only for an initial installation or deliberately reviewed rewiring, since it can update integration files. A GitHub release does not by itself prove that npm publication completed.
