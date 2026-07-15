# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
semantic version tags.

## [Unreleased]

No released changes yet.

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

[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Seraphim0916/omnilane/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Seraphim0916/omnilane/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Seraphim0916/omnilane/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Seraphim0916/omnilane/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Seraphim0916/omnilane/releases/tag/v0.1.0
