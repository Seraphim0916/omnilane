# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
semantic version tags.

## [Unreleased]

### Added

- `install.sh --check` reports missing, drifted, and checkout-owned links and
  selected routing reminders without writing. `--dry-run` previews install or
  uninstall actions without loading machine-local routing code.
- `omnilane completion bash|zsh` prints safe shell completion definitions for
  public commands, routing lanes, and local job IDs without loading executable
  machine-local routing overrides.
- `omnilane release-audit [--json]` provides an offline, read-only release gate
  for version metadata, tracked package contents, executable modes, rollback
  docs, archive reproducibility, and optional annotated-tag verification.
- `dispatch.sh --list`, `--explain`, and `--validate` accept `--json` before or
  after the inspection command and return one versioned JSON envelope without
  invoking a provider or creating job state.
- `dispatch.sh --dry-run` resolves the vendor, overrides, mode, work directory,
  per-call timeout, whole-job timeout, and expected side effects without reading
  task stdin, invoking a provider, or creating job state.
- `jobs.sh --json list|status|result|stats` emits versioned, machine-readable
  local job summaries. JSON result inspection reports only body availability;
  task, output, and stderr bodies remain private.
- `jobs.sh wait ID [--timeout N]` waits read-only for one local background job.
  It preserves the recorded job exit, returns 124 on wait timeout, and 125 for
  a dead worker without a recorded exit.
- `jobs.sh audit [--last N] [--json]` performs a bounded, read-only integrity
  and privacy check of the local job store without printing task or result
  content.

## [0.6.0] - 2026-07-16

### Added

- `dispatch.sh --explain LANE` reports every fallback candidate and the selected
  decision without invoking a provider or creating job state.
- `dispatch.sh --validate` checks the effective routing table for duplicate
  lanes, malformed candidates, unknown vendors, and machine-local reachability
  without invoking a provider.
- `jobs.sh stats [--last N]` summarizes local job outcomes and lane/vendor
  distribution from bounded public metadata without reading task or result bodies.
- `omnilane doctor --json` emits the same read-only health checks as one escaped,
  machine-readable document while preserving the existing failure exit status.
- The Live Board can pin one loaded job as a memory-only reference and compare
  its route and public result with the current selection. Comparison remains
  local, read-only, responsive, and plain-text safe.

### Changed

- Redesigned all five READMEs: a 60-second quickstart section, the feature
  grid folded into How it works, and the Live UI docs promoted to a Live
  Board section with desktop and mobile screenshots
  (`docs/live-board.png`, `docs/live-board-mobile.png`).

### Fixed

- Lock-owner reads no longer leak transient missing-file diagnostics when the
  owner file disappears between the existence check and bounded read; locking
  behavior remains fail-closed.

## [0.5.1] - 2026-07-16

### Added

- A root `VERSION` source of truth, `omnilane --version`, and CI regression
  coverage that keeps both plugin manifests, the changelog, and all five README
  headings aligned.

### Fixed

- Codex `work` remains available outside Git worktrees. When no whole-job
  timeout is configured, Omnilane now reuses the resolved per-call watchdog as
  a process-group fuse for that case (up to 999999999 seconds); expiry cleans
  the supervised process group and returns 124. Without the bundled Perl
  supervisor it warns and retains the existing per-call watchdog path instead
  of blocking work.

## [0.5.0] - 2026-07-15

### Added

- Optional `--job-timeout` and per-lane/global environment controls for one
  aggregate deadline across lock wait, retries, voters, and rounds.
- Preview-first `jobs prune`, read-only `omnilane doctor`, executable gate
  fallback, and explicit dispatch usage/environment validation.
- Bundled-Chromium behavior CI, contribution and security policies, and this
  durable changelog.

### Changed

- Job prompts, results, metadata, PID files, and logs now use owner-only storage
  with bounded control-file parsing and complete JSON escaping.
- Installer ownership rules preserve regular wrappers and symlinks belonging to
  other checkouts; uninstall remains successful after provider removal.
- Empty and invalid stale locks can be recovered without stealing a live owner.
- GitHub Actions now use read-only permissions, stale-run cancellation, and
  verified commit SHA pins.

### Fixed

- Rejected job-ID traversal, symlinked job stores and job directories,
  malformed exit/PID metadata, unbounded PID reads, ambiguous dispatch
  positionals, and invalid timeout/retry/lock controls.
- The whole-job supervisor forwards signals, terminates TERM-ignoring process
  groups, preserves normal exit status, and records timeout exit `124` for
  foreground and background jobs.
- Cross-browser mobile focus restoration tests now wait for the observable
  focus state instead of racing the next animation frame.

### Security

- Prevented terminal-control injection through rejected control values and
  diagnostic output.
- Kept prompts and model answers out of world-readable job artifacts and
  prevented local state commands from following foreign symlinks.

## [0.4.0] - 2026-07-15

### Added

- Natural-language consultation rules and an explicit multi-vendor `consult`
  lane with no silent fallback for named targets.
- `--vendor` selection for direct model consultation.
- Authenticated, loopback-only Live UI with job summaries, details, SSE updates,
  lifecycle commands, desktop/mobile behavior, and real-browser tests.
- Per-call and per-lane watchdog overrides through `--timeout` and
  `OMNILANE_TIMEOUT_<LANE>`.

### Changed

- Published the v0.4.0 guide in English, Traditional Chinese, Simplified
  Chinese, Japanese, and Korean.

## [0.3.0] - 2026-07-13

### Added

- Interactive configurator, global wrapper, opt-in vote panel, fallback model
  documentation, and five-language installer guidance.

### Security

- Replaced routing `eval` with a literal tokenizer.
- Made installer marker removal fail closed and uninstall byte-reversible.
- Added untrusted-data boundaries and cleanup guarantees to vote round two.

## [0.2.1] - 2026-07-13

### Fixed

- Corrected target-directory lock keys and ownership, fail-closed mode
  validation, Grok empty-output handling, background job status, preserved job
  exit codes, read-only Claude advice tools, Gemini workdir behavior, and the
  stock-Perl watchdog fallback.

## [0.2.0] - 2026-07-12

### Added

- Lane fallback chains, the `coding-overflow` lane, background job robustness,
  and a Traditional Chinese README.

## [0.1.0] - 2026-07-12

### Added

- Initial shared routing table, cross-vendor dispatcher, runners, installer,
  and baseline lint fixes.

[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v0.6.0...HEAD

[0.6.0]: https://github.com/Seraphim0916/omnilane/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Seraphim0916/omnilane/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Seraphim0916/omnilane/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Seraphim0916/omnilane/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Seraphim0916/omnilane/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Seraphim0916/omnilane/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Seraphim0916/omnilane/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Seraphim0916/omnilane/releases/tag/v0.1.0
