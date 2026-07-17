# Omnilane 1.0 acceptance matrix

Status: Vincent selected all eight Round 1 experiments. They are integrated one
at a time on `codex/release-1.0`; `main` remains unchanged. Clean-tree local
release-candidate verification is complete except ShellCheck, which remains
unavailable on the current MacStudio. CI/ShellCheck and Vincent's final branch
decision remain before any release action.

## Release contract

Omnilane 1.0 is ready only when every required row below has current evidence,
the complete project checks pass, and the real CLI/runtime paths succeed from a
clean checkout. A green unit suite alone is insufficient. "Zero bugs" means no
known reproducible defect inside this matrix; it is not a claim that defects are
impossible.

Each idea gets one independent branch from the current accepted baseline. At
most eight experiment branches may be active at once, and the project may run at
most three evidence-driven creative-divergence rounds. A new round is allowed
only when the preceding round produced new runtime evidence, adversarial failure
modes, user evidence, or comparison results that justify materially different
hypotheses. Renaming or retrying the same idea does not count as new evidence and
does not justify another round.

The three-round limit applies to creative divergence, not to a branch's normal
red/green debugging loop. A branch is still rejected rather than rescued by
weakening acceptance criteria when it cannot become stable. The separate Goal
stop rule for the same blocker recurring three evidence-driven attempts remains
in force.

No idea branch is merged automatically. After the final justified divergence
round, Vincent receives a comparison dossier for every attempted, retired, and
surviving branch and selects which branches, if any, may enter
`codex/release-1.0`. Selected branches are then merged one at a time into that
integration branch with regression checks after each merge. `main` remains
untouched until Vincent separately approves it.

## Baseline

| Item | Evidence | Current result |
| --- | --- | --- |
| Operator and locality | `whoami-operator.sh`, `scutil`, `pwd` | `codex-s`; `MacStudio`; `/Users/vincentw/dev/omnilane` |
| Source state | `git status --short --branch` | clean `main` at `528ecc6` before integration branch creation |
| Version | `VERSION`, both plugin manifests, latest tag | candidate metadata `1.0.0`; no `v1.0.0` tag created |
| Shell baseline | `bash tests/run.sh` | 50 passed, 0 failed |
| Full required checks | `CONTRIBUTING.md` command set | pending |
| Real runtime baseline | installed CLI and isolated install/UI flows | pending |

## Round 1: up to eight concurrent idea branches

The table order is the recommended evaluation order only. It does not authorize
merging. Later rounds may replace retired experiments with materially different
branches only when Round 1 evidence justifies doing so, while keeping no more
than eight experiment branches active at once.

| # | Branch | User-visible outcome | Red/green oracle | Adversarial focus | Runtime proof |
| --- | --- | --- | --- | --- | --- |
| 1 | `codex/idea-dispatch-json` | `dispatch.sh --list`, `--explain`, and `--validate` expose a versioned JSON contract without provider calls | tests reject missing, invalid, mixed-mode, and unstable JSON | hostile routing text, control bytes, missing vendors, duplicate lanes, option-order errors | parse each mode with `python3 -m json.tool`; confirm no job state |
| 2 | `codex/idea-dispatch-dry-run` | a dry run shows the fully resolved vendor, mode, workdir, per-call timeout, whole-job timeout, and side-effect decision | fake provider must never execute or create a job | symlink workdir, disabled lane, unavailable vendor, invalid timeout, nested depth | invoke dry run against isolated `OMNILANE_HOME`; inspect exit code and absence of state |
| 3 | `codex/idea-jobs-json` | job list, status, result metadata, and stats offer versioned JSON while keeping private bodies private unless explicitly requested | JSON schema tests over valid and malformed job stores | symlinks, oversized fields, malformed metadata, control bytes, missing files | parse CLI output from an isolated fixture store |
| 4 | `codex/idea-jobs-wait` | scripts can wait for one job to reach a terminal state with a bounded timeout | deterministic foreground fixture changes pending to terminal | disappearing job, malformed status, timeout zero, interrupted wait, PID reuse | isolated background fixture; verify terminal and timeout exit codes |
| 5 | `codex/idea-install-check` | `install.sh --check` reports drift and ownership without writing; `--dry-run` previews install/uninstall actions | filesystem snapshots must remain byte-identical | foreign symlinks, missing newline, read-only files, partial install, locale variants | run in a temporary HOME and compare pre/post hashes |
| 6 | `codex/idea-shell-completion` | Bash and Zsh completion output covers public commands, lanes, options, and safe job-ID completion | generated scripts pass shell syntax and command inventory assertions | spaces in models, hostile job names, absent job store, no command execution during completion | source completion in isolated shells and complete representative inputs |
| 7 | `codex/idea-jobs-audit` | `jobs.sh audit [--last N] [--json]` performs a bounded, read-only integrity scan and gives actionable findings | fixtures cover healthy, corrupt, unsafe, malformed, and oversized stores | link traversal, FIFO files, path replacement, permissions, hostile metadata, 1001-job store | audit isolated stores; prove no mutation, private output, bounded samples, and stable status codes |
| 8 | `codex/idea-release-audit` | `omnilane release-audit [--json]` verifies version alignment, required files, executable modes, docs links, release inventory, archive, and optional tag without publishing | mutate a temporary checkout one invariant at a time and require precise failure | dirty tree, stale changelog links, manifest mismatch, unsafe files, private-key markers, tag mismatch | run human and JSON modes on clean checkout; build byte-identical deterministic inventory twice |

## Cross-cutting 1.0 gates

| Area | Required evidence |
| --- | --- |
| Core routing | offline list/explain/validate/dry-run plus fake-gate dispatch; no paid provider required |
| Job lifecycle | create, inspect, wait, audit, prune preview, and bounded cleanup in isolated state |
| Compatibility | macOS Bash 3.2-compatible syntax, Linux CI, Python 3.9+, Bash/Zsh completion syntax |
| Failure behavior | documented exit codes; invalid input fails before provider or state mutation |
| Privacy and security | no prompt/result leakage in metadata modes; no symlink traversal; owner-only mutable state |
| Installation | fresh install, repeated install, drift check, dry-run, uninstall, byte-reversible hook restoration |
| UI | Python unit/HTTP/lifecycle tests and real-browser behavior suite; Live Board remains observation-only |
| Documentation | English and four translations keep command/version headings aligned for 1.0 public commands |
| Version and package | `VERSION`, manifests, changelog, compare links, CLI version, release inventory all agree on `1.0.0` |
| Rollback | every idea remains an independent branch; selected integration merges are individually revertible; install/uninstall round-trip leaves original bytes |

## Required verification for each idea branch

1. Run the branch-specific failing oracle and adversarial fixtures.
2. Run `bash tests/run.sh` and the relevant Python unit module.
3. Run syntax and ShellCheck over changed shell files.
4. Exercise the changed CLI path against an isolated `HOME` and
   `OMNILANE_HOME`; capture return code and bounded output.
5. Record the branch tip, result, divergence round, known risks, and rejection
   or selection recommendation. Do not merge before Vincent chooses.

## Selection and integration gate

After no further evidence-justified round remains, or after Round 3 completes,
stop and ask Vincent to choose. For each selected branch only, merge it into
`codex/release-1.0`, rerun its oracle, the complete regression set, and the real
runtime smoke, then record the merge commit and rollback command. A selection
does not authorize merging `main`, creating a tag, pushing, publishing, or
changing any external release state.

## Final release-candidate verification

Run the complete checks documented in `CONTRIBUTING.md`, including real-browser
behavior. Then perform a clean-checkout install/check/dry-run/uninstall cycle and
run all public read-only CLI modes. Provider-backed calls remain out of scope
unless Vincent separately approves cost and account use.

Do not merge the default branch, create or push `v1.0.0`, publish packages, or
change external release state without Vincent's explicit approval.
