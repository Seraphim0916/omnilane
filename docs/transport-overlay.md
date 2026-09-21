# The transport overlay: what it pins, why it goes stale, how it is re-signed

Operator reference. A model driving omnilane needs only the refusal table in
`skills/omnilane/SKILL.md`; this is the background for the person who maintains
the host. The short version: `omnilane resign` does all of this for you.

The *transport overlay* (`~/.omnilane/transport-contracts.local.json`) is this
host's proof that each vendor CLI really selects the model a lane names. It pins
the sha256 of every vendor executable and runner script it was proven against. A
model caller is refused with `runtime-mapping-unverified` for any target the
overlay does not currently verify. A refusal of `invalid-policy-input` with
"transport contract evidence changed" means the overlay itself will not load; run
`omnilane doctor`, whose `transport-overlay` check names the file and the vendor.

## Why it goes stale

Upgrading a vendor CLI is the usual cause. The overlay pins the sha256 of each
vendor's executable and runner script, so a new release invalidates that vendor's
selector evidence. Evidence entries carry a `vendor` tag: a tagged entry that
drifts marks only its own vendor stale, and the other three keep dispatching.
Untagged evidence — `probe-manifest.json`, and any overlay built before the tags
existed — still fails the whole gate closed, which is what an unpatched host
looks like. Codex and Claude resolve through version directories
(`releases/0.153.4-…`, `versions/2.1.266`), so their upgrades remove the anchored
file rather than change its digest; both are treated as staleness, not corruption.

Do not expect these upgrades to be operator actions. agy and grok update
themselves in the background when invoked — agy's own `cli.log` records
`auto_updater.go: Spawned background update process`, and both binaries changed
under a probing session on 2026-09-10, minutes after their first call. Overlay
drift is therefore a routine consequence of using a vendor, not an occasional
maintenance event, which is why per-vendor degradation matters more than it
looks. It also means any test asserting a fixed number of verified live
mappings will go red on its own schedule.

Because of that, `build_overlay.py` anchors the executable `shutil.which` resolves
rather than a version written into the script. A pinned path drifts out of use
silently: before 0.42.6 the overlay hashed claude `2.1.263` while every dispatch
ran `2.1.266`, so eleven mappings were "verified" against a binary that had not
run for a day.

The overlay also names the score registry snapshot it was built for, and dispatch
refuses an overlay built for any other (`transport overlay snapshot mismatch`).
A release that re-scores the registry therefore stales every vendor at once, on
every host, whether or not a CLI moved. `omnilane resign` treats that as drift
too. No executable changed, so it reuses the probe evidence it already has,
probes only the rows that have none (the models the new registry added) and
rebuilds the overlay for the new snapshot.

## Re-signing with `omnilane resign`

`omnilane resign` does the re-signing described below in one command, and is what
an operator (or a daily job in the operator's GUI session) runs when doctor
reports a stale or moved vendor. `omnilane resign --check` only reports. It
re-probes a changed executable unattended only when it still carries the signer
the live overlay recorded and still sits in the same install location; then it
builds into a staging root, loads the staged overlay, replaces the live one
atomically, runs one real dispatch per re-probed vendor, and restores the backup
if that dispatch fails. Two separate things are proven there, and neither
impersonates a model: loading the staged overlay the way dispatch does proves the
mapping gate now verifies the vendor, and the dispatch — sent under the human
assertion, which skips the score ceiling and nothing else — proves the runner and
the CLI still answer. An adhoc or unsigned executable (a locally patched
`claude`, for instance), a new signer, a new install directory, or an overlay that
never recorded a signer all stop at exit 20 with the exact
`omnilane resign --vendor V --approve V` line for the operator to run after
looking. `--record-signers` is the operator adopting the signers of the
executables an older overlay already pins. An operator who re-signs a vendor's
executable adhoc on purpose (a local post-update patch step, for instance) runs
`omnilane resign --trust-adhoc VENDOR` once; from then on an adhoc update of that
vendor **in the same install directory** is re-probed unattended like a
same-signer one, while an unsigned executable, an adhoc one in another
directory, or a different vendor still stops at exit 20. The trust is recorded
on the vendor's overlay entry (`operator_trust`), survives later re-signs, and
is per vendor. A model never passes `--approve`, `--record-signers` or
`--trust-adhoc`. Keychain-backed CLIs (claude, grok, agy) cannot authenticate
from an ssh login, so a sweep there reports `unprobeable` instead of writing "not
logged in" into the evidence; run it from the GUI session. Doctor now also
reports a CLI that was updated *beside* its old executable (codex and grok
install per-version files), which the hash check alone never saw.

## Re-signing by hand

Re-signing by hand is a probe, a rebuild, and an install, in that order. Back up
`~/.omnilane/transport-contracts.local.json` first; restoring it is the rollback.
`scripts/lib/probe_sweep.py --root ROOT [--vendor V]` derives every probe command
from `build_overlay.py`'s PROVEN table (`--plan` prints them without running).
`scripts/lib/probe.py --expect TOKEN [--vendor V] NAME COMMAND…` invokes the CLI
directly through `subprocess`, so it works while the gate is refusing everything —
this is what breaks the deadlock. `scripts/provider-probe.sh` goes through
`dispatch.sh` and therefore through the gate, so it is useless in this state.
Then `scripts/lib/build_overlay.py` rebuilds, and you copy the result over the
live overlay. Verify with a real dispatch on a lane belonging to the vendor you
re-probed; loading the registry in Python is not the runtime surface.

Keep the sweep where its default `--root` puts it,
`~/.omnilane/transport-evidence/<sweep-id>/`. The rebuilt overlay anchors
`probe-manifest.json` by absolute path as untagged evidence, so a sweep parked
inside a repository is one `git clean -fdx` away from taking every vendor down
at once — the same global refusal a re-signing session is usually trying to end.

## Evidence tiers

Every mapping carries an `evidence_tier` saying how strongly its probe pinned the
responder. `billed-model` means the provider named the model it charged for —
Claude's `modelUsage`, grok's under `--output-format json`. `client-echo` means
the CLI wrote down the model it asked for — codex's session rollout, agy's
`cli.log` resolver line. `selector-only` means the CLI accepted the selector and
said nothing more. Put plainly: `client-echo` is the CLI's copy of your order,
`billed-model` is the provider's receipt. Neither certifies upstream identity,
but only one of them was written by the party that answered.

The tier is reported, never enforced. Dispatch still turns on `runtime_verified`
alone, so a mapping that drops to `selector-only` keeps working and simply shows
up in doctor as worth re-probing. Do not add a tier check to the gate: that would
rebuild the failure 0.42.5 removed, where evidence quality could refuse a lane
that runs. The tier is read off the evidence a run produced rather than assigned
per vendor, so a sweep predating 0.42.6 re-judges as `selector-only` and a CLI
that starts reporting a billed model is promoted with no code change.

Two probe details follow from this. Codex needs `exec --json` (the thread id that
locates the rollout) and must *not* use `--ephemeral`, which suppresses the very
rollout the tier reads. agy needs its own app data directory, prepared exactly
the way `run-gemini.sh` does it — `prepare-agy-mode.py --mode advise` returns a
path relative to `~/.gemini` that is passed as `--app_data_dir=`; the environment
variables that look like they would do this are ignored.

## Reading a probe before signing it

Never sign a probe you did not read. `probe.py` records a `verdict` because exit
status alone is not evidence: the Claude CLI answers a quota refusal with a JSON
body carrying `is_error`, and it accepts an unknown `--effort` by silently using
the default, returning exit 0, the right `modelUsage`, and the expected token
with only a stderr warning to show for it. Effort is half of a scored identity,
so that path would certify a mapping at the wrong tier. Configurations whose
probes failed are recorded in the overlay's `unproven[]` and surfaced by doctor
instead of vanishing — six Fable rows sat unusable for two days in September
2026 because a 429 quota refusal left no trace anywhere. A refused probe is not
always transient: re-probing those six two days later returned the same 429, so
an `unproven[]` entry can mean the account, not the moment. Read the reason
before assuming a retry will clear it.

## What an overlay does and does not claim

A `--transport-overlay /absolute/overlay.json` may prove a small set of host-local
request selectors using exact identities and hashed local contract evidence. It does
not change frozen AA scores or certify upstream provider identity. The explicit
`--operator-asserted-human` exemption is cooperative operator metadata, not automatic
model detection or OS authentication; model callers must not assert it for themselves.
