# Round 1 experiment: offline release audit gate

Branch: `codex/idea-release-audit`

Commits: `1c75e10`, `a7aab9c`

Status: PARTIAL. Functional, adversarial, regression, and clean-fixture runtime
acceptance pass. Local ShellCheck is unverified because `shellcheck` is not
installed on the current MacStudio.

## Hypothesis

A read-only offline `omnilane release-audit [--json]` gate can turn release preparation into reproducible checks
for version consistency, changelog links, package contents, executable modes,
rollback documentation, secret-shaped artifacts, archive creation, and an
optional annotated tag without creating or publishing anything.

## Red/green evidence

- Red: the complete shell suite reported `50 passed, 1 failed`; the new oracle
  received exit 127 because `scripts/release-audit.sh` did not exist.
- The first implementation exposed a real documentation blocker: README said
  uninstall was reversible but did not give the complete rollback command. All
  five READMEs now document `./install.sh --uninstall` explicitly.
- Green: the complete shell suite passed `51 passed, 0 failed`.
- The complete Python suite passed `36 passed, 11 subtests passed`.
- A selection-gate review found the acceptance matrix required both a public
  wrapper command and versioned JSON. The new red oracle returned exit 2 for
  both current and future targets; the final public command emits one JSON
  document and preserves the human mode's exit status.
- Bash syntax, Perl syntax, Python compilation, and `git diff --check` passed.

## Runtime evidence

- The clean experiment branch passed the public script for target `0.6.0` with
  61 tracked paths plus manifest and archive SHA-256 evidence.
- The same branch with `--require-tag` correctly failed because the existing
  annotated `v0.6.0` tag points at the historical release, not the experiment
  commit.
- A clean isolated Git archive was committed and annotated locally as
  `v0.6.0`; `--require-tag` then passed. This created no tag in the source repo
  and made no remote or provider call.
- Target `1.0.0` correctly remains blocked by five current-state findings:
  VERSION mismatch, wrapper version mismatch, missing release heading, and the
  two missing changelog links.
- Public JSON and JSON-plus-manifest modes parse as schema version 1 and contain
  only stable check codes, hashes, tracked inventory, and release metadata.

## Adversarial evidence

- Strict mode rejects tracked, staged, or untracked worktree changes.
  `--allow-dirty` is explicit and prints a warning for development inspection.
- A hostile semicolon-bearing target fails with usage exit 2 and does not create
  its marker.
- Review found interpolated dots in the changelog regular expression were not
  escaped. The target is now escaped before every regex match.
- Tracked `.env`, certificate/private-key filename patterns, unsafe control
  characters in paths, symlinks, forbidden build artifacts, and embedded
  private-key markers fail with generic codes; matching content is never
  printed.
- An isolated hostile package containing both `.env` and a private-key marker
  failed with exactly the relevant package and content findings.
- Missing required files, non-executable runtime entrypoints, unavailable hash
  tools, an unbuildable Git archive, lightweight/missing tags, or annotated tags
  that do not resolve to HEAD all fail closed.

## Review

The gate reads local Git metadata and tracked public project files only. It
never invokes a provider, network API, installer, tag creation, archive write,
push, or release command. `--manifest` prints Git-quoted tracked entries;
default output stays to stable PASS/FAIL/WARN codes and hashes.

## Known limits

- This branch deliberately does not change VERSION to `1.0.0`; doing so belongs
  only on a Vincent-selected release candidate after integration decisions.
- `--allow-dirty` inspects the index/HEAD manifest rather than pretending
  uncommitted bytes are a release artifact. Formal acceptance must run without
  that flag.
- Annotated-tag verification proves type and target, not cryptographic tag
  signature or remote publication state.
- Filename/content checks catch high-confidence private-key and credential-file
  patterns; they are not a general-purpose secret scanner.
- ShellCheck must run before this branch can receive an overall PASS.

## Rollback

No merge is authorized. Delete the experiment branch to discard it. If Vincent
later selects and integrates the gate, revert `a7aab9c` and then `1c75e10` to
remove it. `main` remains unchanged pending Vincent's final judgment.
