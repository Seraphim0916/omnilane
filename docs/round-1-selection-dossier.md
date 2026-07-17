# Omnilane 1.0 Round 1 selection dossier

Status: awaiting Vincent's selection. No experiment commit is present on
`codex/release-1.0`, and neither this branch nor any experiment branch has been
merged into `main`.

## Fixed boundaries

- Divergence rounds used: 1 of at most 3.
- Concurrent experiment branches: 8 of at most 8.
- Common base commit: `528ecc6cdaa5051078e8d17eb5c7128394c0c67a`
  (the commit peeled from annotated tag `v0.6.0`).
- `main` and `origin/main` remain at the same base commit.
- No branch was pushed; no tag, package, release, provider call, service restart,
  installed symlink, or production state was created or changed.

## Candidate comparison

`PARTIAL*` means the branch-specific feature, adversarial checks, complete shell
suite, complete Python suite, and real local runtime path passed, with zero known
reproducible defect inside that branch's acceptance scope. Overall PASS is
withheld only because ShellCheck is unavailable on this MacStudio.

| # | Branch tip | Outcome | Release value | Integration collision | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | `codex/idea-dispatch-json` `b27e39f` | Versioned JSON for list, explain, and validate without provider calls | Stable automation contract for routing inspection | High with #2 in `dispatch.sh`; shared docs/tests | PARTIAL* |
| 2 | `codex/idea-dispatch-dry-run` `995b5e3` | Fully resolved, side-effect-free dispatch preview | Proves routing, workdir, timeout, and provider choice before execution | High with #1 in `dispatch.sh`; shared docs/tests | PARTIAL* |
| 3 | `codex/idea-jobs-json` `b8080e4` | Private-by-default versioned job JSON | Machine-readable lifecycle without task/result leakage | High with #4/#7 in `jobs.sh`; wrapper/docs/tests | PARTIAL* |
| 4 | `codex/idea-jobs-wait` `6b03887` | Bounded wait with preserved terminal exit | Removes unsafe external polling for automation | High with #3/#7 in `jobs.sh`; wrapper/docs/tests | PARTIAL* |
| 5 | `codex/idea-install-check` `5e39f94` | Read-only drift check and byte-stable install/uninstall preview | Directly reduces install, upgrade, ownership, and rollback risk | Low in code (`install.sh` only); shared docs/tests | PARTIAL* |
| 6 | `codex/idea-shell-completion` `e64279d` | Safe Bash/Zsh commands, lanes, options, and bounded job IDs | Strong interactive UX; not required for release safety | Low in new completion files; wrapper/docs/tests | PARTIAL* |
| 7 | `codex/idea-jobs-audit` `fdc366e` | Human and versioned JSON integrity/privacy audit | Detects permission drift, corrupt metadata, unsafe entries, and bounded large-store state | High with #3/#4 in `jobs.sh`; wrapper/docs/tests | PARTIAL* |
| 8 | `codex/idea-release-audit` `7bfa4b1` | Public human/JSON offline release gate and deterministic manifest | Direct evidence for version, package, rollback, secret-shaped files, archive, and tag readiness | Low in new script; wrapper/docs/tests | PARTIAL* |

## Verification summary

Every branch started with a failing test or runtime oracle, then passed its final
branch-specific oracle. Each final branch passed `51 passed, 0 failed` in the
complete shell suite and `36 passed, 11 subtests passed` in the complete Python
suite. Bash/Perl/Python syntax and `git diff --check` passed. Each branch has a
real public CLI or shell runtime proof, not only fixture assertions.

Detailed evidence is retained on each branch and can be read without checking
it out, for example:

```bash
git show codex/idea-dispatch-json:docs/experiments/round-1-dispatch-json.md
git show codex/idea-dispatch-dry-run:docs/experiments/round-1-dispatch-dry-run.md
git show codex/idea-jobs-json:docs/experiments/round-1-jobs-json.md
git show codex/idea-jobs-wait:docs/experiments/round-1-jobs-wait.md
git show codex/idea-install-check:docs/experiments/round-1-install-check.md
git show codex/idea-shell-completion:docs/experiments/round-1-shell-completion.md
git show codex/idea-jobs-audit:docs/experiments/round-1-jobs-audit.md
git show codex/idea-release-audit:docs/experiments/round-1-release-audit.md
```

## Defects found and fixed inside experiments

- #3: Bash 3.2 empty-stat arrays emitted invalid JSON; a regression now covers
  the empty store.
- #5: dry-run executed `local.sh`, and a symlinked parent could redirect an
  installer-owned path outside HOME. Both received adversarial regressions;
  HOME-internal parent links remain supported.
- #6: job completion had no real resource bound; it now stops after 1000 legal
  IDs and ignores symlink/malformed entries.
- #7: prefix-only metadata validation accepted JSON-shaped corruption; exact
  generated-schema validation now rejects it. Selection review also found and
  fixed the missing versioned JSON contract. A 1001-job fixture proves bounded
  default and explicit sampling.
- #8: rollback prose lacked the exact uninstall command, changelog target dots
  were not regex-escaped, high-confidence private-key artifacts were not gated,
  and the public JSON entrypoint required by the matrix was absent. All now have
  regression or clean/adversarial runtime evidence.

## Selection sets

These are decision aids, not automatic actions.

1. **Release-safety minimum:** #5 and #8. Lowest integration surface; covers
   install/rollback drift and release artifact gating.
2. **Recommended core 1.0:** #1, #2, #3, #4, #5, #7, #8. Adds complete routing
   and job automation while deferring shell completion as non-blocking UX.
3. **Full 1.0:** all eight. Maximum user value; adds #6 with modest code risk but
   more public surface and documentation to support.
4. **Custom:** Vincent lists any subset by number.

## Integration plan after explicit selection

No integration is authorized yet. For a selected set, use this low-to-high risk
order: #5, #6, #8, #1, #2, #3, #4, #7. Merge one branch at a time only into
`codex/release-1.0`; immediately run its oracle, the complete shell and Python
suites, syntax checks, and real runtime smoke. The dispatch pair (#1/#2) and job
trio (#3/#4/#7) require deliberate conflict resolution and new cross-feature
regressions. Every merge remains individually revertible.

Selection never authorizes merging `main`, pushing, tagging, publishing, or any
remote/production action. Those remain separate Vincent-only decisions.

## Remaining risks before a 1.0 release candidate

- ShellCheck is not installed locally, so every branch remains PARTIAL rather
  than overall PASS until selected code runs through ShellCheck/CI.
- Cross-feature behavior is unverified because Vincent has not selected or
  authorized integration; individual branch success cannot prove the combined
  result.
- VERSION, manifests, and changelog still say `0.6.0`. They must change to
  `1.0.0` only on a selected, fully verified release-candidate branch.
- No remote tag, package publication, provider-backed call, or production smoke
  has been attempted or authorized.

## Rollback

Before selection, rollback is simply deleting an unwanted experiment branch;
`main` is unchanged. After authorized integration, revert each recorded merge
commit in reverse order. Installer state, if Vincent later authorizes a clean
environment rehearsal, rolls back with `./install.sh --uninstall` plus the
byte-for-byte fixture snapshots required by the acceptance matrix.
