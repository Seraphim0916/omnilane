# Round 1 experiment: read-only installer inspection

Branch: `codex/idea-install-check`

Commits: `57467ee`, `82de9f6`

Status: PARTIAL. Functional and runtime acceptance pass. Local ShellCheck is
unverified because `shellcheck` is not installed on the current MacStudio.

## Hypothesis

The installer can report checkout-owned links, selected routing reminders, and
drift without writing, and can preview both install and uninstall actions while
preserving every byte in the target HOME.

## Red/green evidence

- Red: the focused oracle reported `0 passed, 1 failed`; `--dry-run` was rejected
  as unknown usage.
- Green: all 51 shell tests passed in isolated process-group-safe batches
  (`27 + 4 + 1 + 19`). Nine installer install/uninstall/check cases also passed
  together after the final parent-boundary fix.
- The 36-test Python suite initially had one local UI server-start failure in
  `test_unresponsive_recorded_server_retains_state`. The exact case then passed
  four consecutive runs and the complete 36-test suite passed on rerun. No UI
  code changed on this branch.
- Bash syntax, Perl syntax, Python compilation, release/CI policy tests, and
  `git diff --check` passed.

## Runtime evidence

Temporary HOME fixtures exercised fresh install preview, installed-state
uninstall preview, healthy check, partial-install check, foreign final links,
external parent links, an intentional HOME-internal parent link, a read-only
instruction file, and all five supported locales. Pre/post filesystem snapshots
included type, mode, link target, and file hash and remained byte-identical for
every read-only mode.

## Adversarial evidence

- `--check` returns 0 only for exact checkout-owned wrapper/skill links and the
  selected current routing reminder; missing or foreign paths return 1 with
  `MISSING` or `DRIFT` findings.
- Initial review found `--dry-run` still called `dispatch.sh --list`, which could
  source executable `~/.omnilane/local.sh`. A hostile overlay regression now
  proves preview mode neither sources the overlay nor creates its marker.
- A second review found that checking only the final symlink allowed
  `~/.codex/skills` or `~/.local/bin` itself to redirect writes outside HOME.
  Install, check, and preview now require the nearest existing parent to resolve
  inside the canonical HOME. Parent links that resolve elsewhere fail before a
  write; intentional links that remain inside HOME are accepted.
- Foreign final links, missing final newlines, instruction-file symlinks,
  malformed markers, existing wrapper files, read-only files, and uninstall
  after vendor removal retain their existing safe behavior.

## Review

Dry-run reuses the real install decision path and replaces only mkdir, link,
hook-write, remove, routing-load, and interactive-configurator boundaries with
stable `would ...` output. Check mode uses exact link targets and exact marked
hook content; it does not invoke a provider, plugin manager, routing overlay, or
configuration script.

## Known limits

- Check mode covers the global wrapper, detected Claude/Codex skill links, and
  routing reminders selected by `OMNILANE_HOOKS`. External Grok and Antigravity
  plugin-manager ownership remains advisory because those CLIs own their state.
- Dry-run intentionally does not render the effective routing table because
  doing so would execute machine-local overlay code; it says that inspection
  would occur after installation instead.
- Plan verbs and PASS/MISSING/DRIFT tokens are stable English even when the
  surrounding installer messages use another supported locale.
- ShellCheck must run before this branch can receive an overall PASS.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates both commits, revert `82de9f6` and then `57467ee`
to remove this feature. `main` remains unchanged pending Vincent's final
judgment.
