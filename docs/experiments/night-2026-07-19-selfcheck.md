# feat/selfcheck — one-command CONTRIBUTING check runner

Branch: `feat/selfcheck` (off `main` @ `c45955e`, v0.8.3)
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

`CONTRIBUTING.md` lists seven required checks as a copy-paste block that
contributors run by hand. This branch adds `scripts/check.sh`, which runs them
all in one command and prints a `PASS/SKIP/FAIL` line per check plus a summary:

```
PASS bash-syntax
PASS shellcheck
PASS perl-syntax
PASS py-compile
PASS unittest
PASS shell-suite
PASS dispatch-list

summary: 7 passed, 0 skipped, 0 failed
```

- `--quick` skips the two slow suite runs (`unittest`, `tests/run.sh`) for a fast
  pre-commit lint.
- A `SKIP` (an unavailable tool such as ShellCheck, or an absent target) is not a
  failure; it exits non-zero only on a real `FAIL`. This matches CONTRIBUTING's
  rule that a missing local check must not be treated as passed.
- Optional `[REPO_DIR]` argument checks a different tree (used by the test's
  failure-path fixture).
- Contributor tool only — not wired into `bin/omnilane` (no new user surface).

## Red/green oracle

New test `test_selfcheck_script` in `tests/run.sh`:
- Asserts `check.sh --quick` exits 0 on the clean tree with a `PASS bash-syntax`
  line and no `FAIL` (recursion-safe: `--quick` skips `tests/run.sh`).
- Asserts a temp repo containing one syntactically broken shell file makes
  `check.sh --quick` emit `FAIL bash-syntax` and exit non-zero.
- RED before implementation: `check.sh` absent → exit 127. GREEN after:
  `70 passed, 0 failed`.

## Real runtime evidence

- `bash -n scripts/check.sh` → OK; `shellcheck -S warning scripts/check.sh` →
  exit 0 (the intentional glob carries a scoped `# shellcheck disable=SC2086`).
- `bash tests/run.sh` (Bash 5) → `70 passed, 0 failed`
- `/bin/bash tests/run.sh` (macOS Bash 3.2) → `70 passed, 0 failed`
- Full dogfood run `bash scripts/check.sh` on the real repo → all seven checks
  `PASS`, `summary: 7 passed, 0 skipped, 0 failed`, exit 0.

Result: **VERIFIED** — the script really runs the entire CONTRIBUTING set and
reports correctly, and its failure path is proven on a broken fixture.

## Docs

Added a pointer to `scripts/check.sh` in CONTRIBUTING.md's "Required checks"
section. No README/CHANGELOG change — this is contributor tooling, not a
user-facing `omnilane` command, and not a user-visible behavior change.

## Recommendation

Select. Low-risk convenience that dogfoods the project's own gate; recursion-safe
under `--quick`; honest SKIP semantics for absent tools.
