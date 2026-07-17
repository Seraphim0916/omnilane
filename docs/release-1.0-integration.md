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
