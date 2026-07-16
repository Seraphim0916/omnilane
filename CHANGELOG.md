# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
semantic version tags.

## [Unreleased]

### Added

- `dispatch.sh --explain LANE` reports every fallback candidate and the selected
  decision without invoking a provider or creating job state.

### Changed

- Redesigned all five READMEs: a 60-second quickstart section, the feature
  grid folded into How it works, and the Live UI docs promoted to a Live
  Board section with desktop and mobile screenshots
  (`docs/live-board.png`, `docs/live-board-mobile.png`).

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

[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/Seraphim0916/omnilane/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Seraphim0916/omnilane/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Seraphim0916/omnilane/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Seraphim0916/omnilane/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Seraphim0916/omnilane/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Seraphim0916/omnilane/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Seraphim0916/omnilane/releases/tag/v0.1.0
