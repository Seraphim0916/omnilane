# The transport overlay: what it pins, why it goes stale, how it is re-signed

Operator reference. A model driving omnilane needs only the refusal table in
`skills/omnilane/SKILL.md`; this is the background for the person who maintains
the host. First installation is `omnilane resign --init`; subsequent maintenance is `omnilane resign`.

## First installation

The human operator runs `omnilane resign --init` in a desktop terminal with the vendor CLIs signed in.
It probes, builds and verifies the overlay, runs a real smoke dispatch for each verified vendor,
records first-trust signers and writes the overlay assignment to `$OMNILANE_HOME/local.sh`.
Existing settings are preserved, and backups and evidence are kept in the printed initialization root.
It refuses an existing overlay, non-interactive execution, `--daily`, and approval or smoke-skipping flags.
It never approves a changed signer or trusts an adhoc executable. A held vendor stays unverified;
verified vendors may still be installed, with `installed: true` and an `attention` entry for each gap.

For manual recovery or older installations, the underlying steps remain available:

```bash
cd "$(npm root -g)/omnilane"            # or your clone
ROOT=~/.omnilane/transport-evidence/first-sweep
python3 scripts/lib/probe_sweep.py --root "$ROOT"
python3 scripts/lib/build_overlay.py --root "$ROOT"
cp "$ROOT/transport-contracts.local.json" ~/.omnilane/transport-contracts.local.json
# Back up an existing local.sh before adding this assignment.
echo 'export OMNILANE_AA_TRANSPORT_OVERLAY="$HOME/.omnilane/transport-contracts.local.json"' >> ~/.omnilane/local.sh
omnilane resign --record-signers       # human operator, unchanged pinned executables only
omnilane doctor
```

The manual steps do not waive any executable-origin, proof or signer gate.

The *transport overlay* (`~/.omnilane/transport-contracts.local.json`) is this
host's proof that each vendor CLI really selects the model a lane names. It pins
the sha256 of every vendor executable and runner script it was proven against. A
model caller is refused with `runtime-mapping-unverified` for any target the
overlay does not currently verify. A missing or hash-mismatched raw probe file
invalidates only the rows that cite that file. Other rows, including siblings
from the same vendor, remain available. Refusals, `omnilane doctor` and
overlay-health name the invalidated row count and the affected vendor's recovery
command: `omnilane resign --vendor <vendor>`.

This applies to both passing and failed probe files. A failed row stays refused;
damage to its evidence does not disable intact rows. Re-verifying an in-memory
registry first revokes the previous overlay's authority and diagnostics. New
authority is published only after validation completes, so a skipped row or a
validation error cannot retain an earlier pass.

Re-signing treats lost positive proof as unprobed. With unchanged executables, it probes
only missing selectors, preserves intact evidence and sets aside damaged staged
copies before rebuilding. Repairing one vendor cannot turn another vendor's
damaged output into new proof. The original overlay and evidence stay available
for rollback; a fresh passing probe is required to regain authority.
Lost failure evidence has no trustworthy retry timestamp. It stays deferred in
unattended runs. An operator can explicitly retry an ordinary failed row with
`--retry-failed` or use an interactive terminal. Retained per-selector pending
rows keep their existing interactive-only cooldown override; `--retry-failed`
does not bypass it. The executable-origin and signer gates still apply.
This deferral survives repairing another vendor, even after the damaged failure
files have been set aside. Diagnostics do not display damaged descriptors as
verified observations or timestamps.

A refusal of `invalid-policy-input` with
"transport contract evidence changed" means the overlay itself will not load; run
`omnilane doctor`, whose `transport-overlay` check names the file and the vendor.
The manifest is a shared integrity anchor, not an individual probe: a missing,
changed or malformed manifest, a different sweep substituted for the evidence
directory, invalid schema/source/engine bindings, or an overlay digest mismatch
still rejects the whole overlay.

The directory rule is content-based: group all manifest-listed passing and
failed descriptors by their recorded parent directory. If **every descriptor in
one group** has a mismatched digest, is still readable JSON, and has a nonempty
string-array `command`, integer `exit_code`, Boolean `timed_out`, and parseable
timezone-bearing `probed_at`, reject the entire overlay with
`transport evidence directory does not belong to this overlay`. These are a
complete set of recognizable replacement probe records with no surviving
descriptor anchor. They are inspected only to reject, never to grant authority.
This deliberately conservative rule also rejects a complete rewrite that merely
reformats every descriptor (including a directory containing one descriptor).
It does not assert that a particular directory rename or a different sweep ID
was observed: the existing format pins file contents, not directory identities.

A subset of changed descriptors, missing/truncated descriptors, or damaged
streams is row-level loss instead. Removing the entire raw directory therefore
invalidates its rows while retaining the trusted manifest; replacing it with a
complete set of recognizable unanchored records fails globally. A byte-identical
copy is still the same pinned evidence. Malformed shared metadata and unsafe file
types are never downgraded to row-level loss.

For stable files during validation, deleting or changing raw proof can only
remove available rows; it never grants a row, changes a score, or bypasses an
executable-origin or caller-ceiling check. Concurrent mutation between hashing
and reading remains an existing limitation of the evidence format/reader; this
change does not provide an immutable evidence snapshot or fix that separate race.

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

The overlay names its source score snapshot. Unchanged request-selector proofs carry
over automatically to an approved new snapshot; a score or date change alone requires
no re-signing, login or network call. Only new or changed request contracts and changed
executables need probing, using `omnilane resign --vendor VENDOR`. Doctor reports how
many rows carried over and how many are new and unprobed. Unknown source snapshots,
expired evidence and changed contracts still fail their existing checks.

## Re-signing with `omnilane resign`

Every mode loads `scripts/lib/common.sh` (including `$OMNILANE_HOME/local.sh`)
with dispatch's shell settings before reading an overlay. A shell load failure
stops before any overlay read, staging or write; a configured but unreadable or
invalid-JSON overlay also stops without writes. One line on stderr names the
configuration/overlay path and reason, including with `--json` (stdout stays
JSON or empty). A missing vendor CLI retains the existing drift/held behavior.

| Exit | Meaning |
|---|---|
| 0 | nothing changed, or re-signed and verified |
| 10 | `--check` found drift |
| 20 | drift needs an operator |
| 30 | attempted and rolled back |
| 40 (`EXIT_HOST_CONFIG`) | host configuration failed, or configured overlay could not be read as JSON |
| 50 (`EXIT_BUSY`) | another resign holds this host's resign lock; nothing read or written |
| 60 | verified selectors are installed; only retained selectors remain pending re-check (`omnilane doctor` explains; `omnilane resign` retries) |
| 2 | genuinely no overlay configured; argparse usage errors also use 2 |

The JSON summary includes `installed` and an `attention` list (`vendor`, `reason`,
`next_command`). Each held or incomplete vendor also gets a final readable line.
`installed: true` with exit 20 or 60 means installation succeeded with outstanding
rows; use that vendor's next step. A next step containing `--approve`,
`--record-signers`, `--trust-adhoc` or `--allow-shrink` is for a human operator
to review and run in a terminal, never for an unattended job or model to adopt.

Daily-job owners should decide notification policy for the new code 40. An
explicitly configured but unreadable overlay is not an unconfigured host.

A vendor that a run could not re-sign (its CLI was not found, the signer check
refused it, the canary or the probes failed) keeps the mappings it had. Each one
gains a `pending_recheck` object with `reason`, `at` and `consecutive_runs`;
`omnilane doctor` reports them as a warning, `omnilane list` ends with a
`pending re-check:` line, and the next `omnilane resign` treats the vendor as
drifted and tries again. The mark disappears when the vendor is re-signed. The
mappings are removed, and listed as unproven, only after two consecutive runs
whose reason is that the CLI could not be found. When no vendor can be re-signed
at all, the run updates these marks in place and rebuilds nothing.

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

Native Gemini 3.6/3.7 Flash high/medium/low choices are retired from configure.
The rejected `gemini-3.6-flash-high` and `gemini-3.7-flash` high/medium/low
selectors (`invalid model selection`) are also excluded from the probe
candidate table. The 3.6 medium/low choices were menu-only entries, not proven
probe candidates. Historical compatibility
snapshots retain their scored rows, but their transport selectors are null,
so old evidence cannot carry these retired selectors into a new overlay.
Use the supported Gemini 3.8 Flash selectors instead. The AA scoring policy
and routing lanes are unchanged; this retirement does not run resign or
modify any host-local overlay.

Re-signing by hand is a probe, a rebuild, and an install, in that order. Back up
`~/.omnilane/transport-contracts.local.json` first; restoring it is the rollback.
`scripts/lib/probe_sweep.py --root ROOT [--vendor V]` derives every probe command
from `build_overlay.py`'s PROVEN table (`--plan` prints them without running).
Export `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1` before a hand-run sweep: `omnilane
resign` sets it for every Claude probe, but `probe_sweep.py` and `probe.py` are
hashed into each proof, so neither sets it, and an enabled advisor tool makes a
working Claude selector fail as `model-mismatch`.
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

### Non-Aqua probe sessions

The re-sign wrapper first tries one eligible selector in a non-Aqua macOS
session (such as SSH) for Claude, Gemini/agy, or Grok. It uses the same cheapest
selector convention as the smoke check: lowest registry score, then config ID;
this is not a live price lookup. A passing preflight permits the normal sweep,
which still has to prove every target separately. Any preflight failure leaves
the vendor `unprobeable` with a safe failure category and GUI/login guidance
as one possible cause, not a diagnosis of every failure. Preflight output
is separate from the sweep evidence and cannot verify a mapping. Rows excluded
by cooldown/skip selection are not used for this preflight.

`OMNILANE_PROBE_ANY_SESSION=1` explicitly bypasses the launchd session heuristic
(and the wrapper preflight). It does not bypass authentication, model/effort
proof, signer, trust, or approval checks. Set it only for a host whose CLI can
authenticate outside Aqua; unset it to restore automatic session handling.
The wrapper's automatic override is scoped to one successful preflight/sweep
and restores the caller's environment afterward.

### Probe child environment boundary

The wrapper admits only `HOME`, `PATH`, and `TMPDIR` from its caller. `HOME`
keeps the existing home location (including on-disk login/config lookup), `PATH`
allows the selected CLI and its interpreter to run, and `TMPDIR` keeps temporary
files in the caller's designated temporary location. `LANG=C` and `LC_ALL=C`
are fixed, not inherited. Claude additionally receives
`CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1`; Grok receives
`GROK_CURSOR_MCPS_ENABLED=0`, `GROK_CURSOR_HOOKS_ENABLED=0`,
`GROK_CLAUDE_MCPS_ENABLED=0`, and `GROK_CLAUDE_HOOKS_ENABLED=0`.
Gemini/agy preparation receives only the three common keys and fixed locale.
No other environment values pass through: this includes credential variables,
endpoint/proxy overrides, alternate config roots, and Python/Node/dynamic-loader
injection variables. The caller's environment is restored after each probe.

This allowlist is verified with offline fake children, not authenticated vendor
CLIs. Environment-based API-key/token login is deliberately unsupported rather
than forwarding unverified credential variables; live keychain/on-disk login
compatibility has not been established. No `SECURITYSESSIONID` or other presumed
keychain variable is forwarded without evidence. `HOME` and `PATH` are trusted
caller inputs, not a filesystem sandbox. User configuration, hooks, MCP servers,
or credentials reachable through the home directory may still be loaded by a
CLI; the allowlist does not claim to isolate those files.

Both Gemini preparation paths use Python `-I`, `stdin=DEVNULL`, the explicit
child environment, and a 30-second deadline. On POSIX, timed-out preparation
kills its process group; descendants that deliberately create another session
can escape that group. The pinned probe engine remains unchanged: its existing
180-second probe timeout kills only the direct CLI child, not its descendants.
The environment scope serializes cooperating wrapper calls, but callers must
not spawn unrelated children concurrently in this synchronous process.
