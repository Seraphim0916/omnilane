# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
semantic version tags.

## [Unreleased]

### Added

- `omnilane doctor` now reports vendor-CLI availability: which of codex, claude,
  grok, gemini (the `agy` CLI), kimi, qwen, and opencode are reachable, plus
  openrouter (`OPENROUTER_API_KEY` + curl). Shown in human and `--json` output;
  the probe mirrors the runners' `*_BIN` overrides and `local.sh` in an isolated
  subshell. It is a `PASS`/`WARN` check and never fails the report.
- MCP server read-only introspection tools: `explain`, `validate`, `dry_run`,
  `jobs_list`, `jobs_stats`, `jobs_audit`, and `doctor` join the existing
  `route`, `list_lanes`, `jobs_status`, and `jobs_result`, so the MCP surface
  mirrors the CLI's full offline read-only surface. All refuse provider calls and
  create no job state; `dry_run` requires an explicit `workdir` only when `mode`
  is `work`. `jobs_stats`/`jobs_audit` accept an optional `last` and `json`.
- `configure set|get|unset|list|diff` non-interactive subcommands: script or
  inspect `routing.local.yaml` without a tty. `set` validates the lane, refuses
  unsafe specs, and rolls back on a structural FAIL; `diff` shows how local
  overrides change the effective table versus the defaults. The interactive menu
  is unchanged.

## [0.8.3] - 2026-07-18

### Added

- Dependency-free MCP stdio server (`omnilane mcp`) exposing routing, lane
  discovery, and background-job status/results to any MCP-capable host.

## [0.8.2] - 2026-07-18

### Added

- `openrouter` vendor: direct OpenRouter API dispatch (curl + `OPENROUTER_API_KEY`,
  no CLI dependency). Advise/consult only — work mode fails hard with guidance —
  and the model slug is mandatory. Hundreds of hosted models become reachable
  from any omnilane install without adding a coding-agent CLI.
- `opencode` vendor: headless `opencode run` dispatch through the OpenCode
  multi-provider aggregator CLI. Advise mode pins OpenCode's built-in read-only
  `plan` agent; work mode uses `--auto`. Appended as the last fallback in the
  default `coding-overflow` chain.

## [0.8.1] - 2026-07-18

### Added

- Claude Code plugin `SessionStart` hook (`hooks/hooks.json`): plugin installs
  now auto-inject the routing reminder (`hooks/routing-instruction.md`) at
  session open (`startup|resume|clear`) via `${CLAUDE_PLUGIN_ROOT}`, with no
  edit to the user's `~/.claude/CLAUDE.md`. The `install.sh` instruction-file
  reminder remains the path for the other CLIs.

## [0.8.0] - 2026-07-18

### Added

- Two new dispatch vendors: `kimi` (Moonshot Kimi Code CLI, binary `kimi`,
  override `KIMI_BIN`) and `qwen` (Alibaba Qwen Code CLI, binary `qwen`,
  override `QWEN_BIN`). Both runners follow the uniform
  `MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE` contract: advise stays
  read-only (kimi `--plan`; qwen default approvals), work auto-approves
  (kimi `-y`; qwen `--approval-mode yolo`), API-key env is stripped so the
  CLIs use their own subscription logins, and empty output is a failure.
- `coding-overflow` now falls back grok → kimi → qwen before `off`, so the
  quota relief valve works with any one of the three vendors installed.
- Configurator and bash completion know the new vendors; `--vendor kimi|qwen`
  pins them directly. The `vote` panel still accepts the original four
  vendors only.
- Both new runners are contract-tested (argv construction, exit codes,
  empty-output failure) against fake binaries; real-model end-to-end runs
  are pending community feedback — failures surface loudly, never as a
  silent rc=0.

## [0.7.1] - 2026-07-18

### Changed

- Routing defaults refreshed against 2026-07 model data (Artificial Analysis
  Coding Agent Index v1.1, vendor announcements): `hardest-coding` first
  choice moves from GPT-5.6 Sol `xhigh` to `max` — Sol (max) scores 80, the
  current state of the art, retiring the earlier xhigh-beats-max snapshot.
- Claude Opus 4.8 fallbacks on `hardest-coding` and `hard-judgment` move from
  `high` to `xhigh` effort, following Anthropic's published guidance to use
  extra effort for difficult tasks and long-running asynchronous work.

## [0.7.0] - 2026-07-17

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
- `dispatch.sh --help` / `-h` and `jobs.sh help` print full usage on stdout
  with exit 0; misuse keeps the stderr usage error with exit 2.
- `jobs.sh tail JOB_ID [--lines N]` peeks at the bounded end of one job's
  public output stream, for running and completed jobs, refusing symlinked
  output paths.
- `jobs.sh retry JOB_ID [--background]` re-dispatches a completed job with its
  recorded lane, vendor, mode, workdir, timeouts, and original task text;
  metadata parsing is fail-closed and running jobs are refused.
- `jobs.sh prune --older-than DAYS` prunes completed jobs by the timestamp
  embedded in the job id; alone it prunes purely by age, and combined with an
  explicit `--keep N` both conditions must hold.

### Fixed

- A bare `jobs.sh` invocation reports usage with exit 2 instead of crashing
  with an unbound-variable error under macOS stock Bash 3.2.
- An empty routing chain (`lane:` with no candidates) no longer aborts
  `--list` mid-table under Bash 3.2; `--validate` now reports it as
  `FAIL <lane> empty-chain` and keeps scanning later lanes.
- `jobs.sh prune` no longer crashes under Bash 3.2 when no job is eligible.
- The configurator's four-voter selection and the vote runner's temp-file
  cleanup no longer hit empty-array expansions under Bash 3.2 `set -u`.

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

[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v0.8.3...HEAD
[0.8.3]: https://github.com/Seraphim0916/omnilane/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/Seraphim0916/omnilane/compare/v0.8.1...v0.8.2

[0.8.1]: https://github.com/Seraphim0916/omnilane/compare/v0.8.0...v0.8.1

[0.8.0]: https://github.com/Seraphim0916/omnilane/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Seraphim0916/omnilane/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Seraphim0916/omnilane/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Seraphim0916/omnilane/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Seraphim0916/omnilane/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Seraphim0916/omnilane/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Seraphim0916/omnilane/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Seraphim0916/omnilane/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Seraphim0916/omnilane/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Seraphim0916/omnilane/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Seraphim0916/omnilane/releases/tag/v0.1.0
