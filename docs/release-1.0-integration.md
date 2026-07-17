# Omnilane 1.0 integration evidence

Target branch: `codex/release-1.0`

Authorized selection: all eight Round 1 experiment branches. This file records
integration only. It does not authorize merging `main`, pushing, tagging, or
publishing.

## Integration baseline

- Start commit: `1cbf46a`
- `main` and `origin/main`: `528ecc6cdaa5051078e8d17eb5c7128394c0c67a`
- Worktree: clean

## #5 Read-only installer inspection

- Branch: `codex/idea-install-check`
- Merge commit: `25b7083`
- Merge result: clean, no conflict
- Shell regression: `51 passed, 0 failed`
- Python regression: `36 passed, 11 subtests passed`
- Runtime: isolated real HOME plus a non-executed fake `codex` entry; install
  dry-run returned 0 with three planned actions, check returned expected 1 for
  missing installation, and HOME remained empty.
- Adversarial runtime: missing supported CLI returned 1; nonexistent HOME was
  rejected by the parent-boundary gate. Both were fail-closed and wrote no
  installer state.
- Rollback: revert merge commit `25b7083` before later dependent merges.

## #6 Safe Bash/Zsh completion

- Branch: `codex/idea-shell-completion`
- Merge commit: `000155d`
- Merge result: one `CHANGELOG.md` add/add conflict; resolved by preserving both
  #5 installer and #6 completion entries. Tests and localized docs merged cleanly.
- Shell regression: `52 passed, 0 failed`
- Python regression: `36 passed, 11 subtests passed`
- Runtime: public Bash completion loaded with `eval`, public Zsh completion
  loaded with `source`, the `triage` lane completed in both, and public output
  byte-matched both checked-in completion files.
- Rollback: revert merge commit `000155d` after reverting later dependent
  merges; retain the conflict-resolution parent history.

## #8 Offline release audit

- Branch: `codex/idea-release-audit`
- Merge commit: `40fabac`
- Merge conflicts: changelog, four localized READMEs, public wrapper, and shell
  suite. Resolution preserved #5 and #6 while adding the release gate as a
  complete CLI, test, and documentation contract.
- Syntax and regression: Bash syntax passed; shell suite `53 passed, 0 failed`;
  Python suite `36 tests` passed.
- Clean runtime: current `0.6.0` JSON-plus-manifest passed with 70 tracked
  paths; two consecutive outputs were byte-identical. Target `1.0.0` failed
  closed with five release blockers, including `version-mismatch`.
- Isolated archive runtime: an annotated local `v0.6.0` tag resolving to the
  fixture HEAD passed `--require-tag`; no source-repository tag or remote was
  created.
- Adversarial target injection remains covered by the full shell oracle. The
  repository pre-tool safety hook blocked a second integration-level fixture
  command from reading a deliberately named `.env`; the original experiment's
  isolated secret-shaped package fixture remains recorded in its dossier.
- Limitation: ShellCheck is unavailable on this MacStudio, so this experiment
  and the aggregate release candidate remain `PARTIAL` pending CI/ShellCheck.
- Rollback: revert merge commit `40fabac` before reverting #6 or #5.

## #1 Versioned dispatch inspection JSON

- Branch: `codex/idea-dispatch-json`
- Merge commit: `78fcf74`
- Merge result: only `CHANGELOG.md` conflicted; all four existing Unreleased
  entries were preserved alongside the dispatch JSON entry.
- Syntax and regression: Bash syntax passed; shell suite `54 passed, 0 failed`;
  Python suite `36 tests` passed.
- Isolated runtime: both accepted `--json` positions for `--list`, `--explain`,
  and `--validate` produced byte-identical schema-version-1 documents.
- Adversarial runtime: mixing `--json` with provider work failed usage with exit
  2 and created no jobs directory. No provider or network call was made.
- Limitation: ShellCheck remains unavailable locally; aggregate status stays
  `PARTIAL` pending CI/ShellCheck.
- Rollback: revert merge commit `78fcf74` before reverting earlier integrations.

## #2 Side-effect-free dispatch dry run

- Branch: `codex/idea-dispatch-dry-run`
- Merge commit: `d775d86`
- Conflict resolution combined the existing JSON inspection parser with the
  dry-run flag and retained one shared canonical dispatch resolution path.
- Syntax and regression: Bash syntax passed; shell suite `55 passed, 0 failed`;
  Python suite `36 tests` passed.
- Isolated runtime: a fake executable provider, explicit route, stdin task,
  symlink workdir, work mode, background mode, and both timeout layers resolved
  into the expected plan. The provider marker and jobs directory stayed absent,
  the routing hash stayed identical, and task canary bytes were not emitted.
- Adversarial runtime: a symlinked jobs store failed closed with exit 1 before
  provider execution and did not write through to the foreign directory.
- Limitation: ShellCheck remains unavailable locally; aggregate status stays
  `PARTIAL` pending CI/ShellCheck.
- Rollback: revert merge commit `d775d86` before reverting #1 or earlier work.

## #3 Private-by-default jobs JSON

- Branch: `codex/idea-jobs-json`
- Merge commit: `0e7fabf`
- Conflict resolution retained dispatch JSON and dry-run documentation while
  adding jobs JSON syntax to all five READMEs and the public wrapper help.
- Syntax and regression: Bash syntax passed; shell suite `56 passed, 0 failed`;
  Python suite `36 tests` passed.
- Isolated runtime: prefix and suffix JSON positions for list were byte-equal;
  list, status, result, and stats all parsed as schema version 1.
- Privacy/adversarial runtime: task, result, and stderr canaries were absent from
  every JSON response; path-escape job ID failed with exit 2 while still
  returning valid JSON.
- Limitation: ShellCheck remains unavailable locally; aggregate status stays
  `PARTIAL` pending CI/ShellCheck.
- Rollback: revert merge commit `0e7fabf` before reverting #2 or earlier work.

## #4 Bounded jobs wait

- Branch: `codex/idea-jobs-wait`
- Merge commit: `28fbd7c`
- Conflict resolution retained the jobs JSON normalizer and added wait as a
  human-mode, read-only command with its own bounded timeout contract.
- Syntax and regression: Bash syntax passed; shell suite `57 passed, 0 failed`;
  Python suite `36 tests` passed.
- Isolated runtime: a transitioning job preserved recorded exit 7; an immediate
  timeout returned 124; a dead worker without an exit marker returned 125.
- Cross-feature adversarial runtime: unsupported `--json wait` failed usage with
  exit 2 but still returned a valid JSON error envelope, without polling or
  mutating job state.
- Limitation: ShellCheck remains unavailable locally; aggregate status stays
  `PARTIAL` pending CI/ShellCheck.
- Rollback: revert merge commit `28fbd7c` before reverting #3 or earlier work.
