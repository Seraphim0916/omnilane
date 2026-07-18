# feat/configure-noninteractive — scriptable lane configuration

Branch: `feat/configure-noninteractive` (off `main` @ `c45955e`, v0.8.3)
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

`scripts/configure.sh` was interactive-only (a tty menu). Automation and
scripted setups had to hand-edit `~/.omnilane/routing.local.yaml`. This branch
adds four non-interactive subcommands; no subcommand still opens the menu:

- `configure set LANE SPEC` — set/override one lane. `SPEC` is the routing RHS
  (`"vendor model effort [| … ]"` or `off`; quote the whole SPEC and the model
  when a model name contains spaces).
- `configure get LANE` — print the effective routing line for `LANE`.
- `configure unset LANE` — remove `LANE`'s local override.
- `configure list` — print the current local overrides.

## Safety design

- `set` requires a **known** lane (from `routing.yaml`) matching
  `^[a-z][a-z0-9-]*$`; unknown/invalid lanes exit 2.
- `SPEC` passes a byte whitelist that allows the routing grammar (`|`, quoted
  models, slashes, effort) but rejects shell-dangerous bytes (`$ \` \ # ; & < >`
  and newlines) — a written line can never be re-read as code.
- After writing, `set` runs `dispatch.sh --validate` and rolls back (restoring
  the prior file, or removing a newly created one) if **that lane** shows a
  structural `FAIL` (e.g. unknown vendor). Availability `WARN` (validate exit 4)
  is tolerated — the vendor CLI may be legitimately absent.
- Re-setting a lane replaces it (no duplicate lines); `set`/`unset` back up
  before mutating.

## Red/green oracle

New test `test_configure_noninteractive` in `tests/run.sh`:
- RED before implementation: `configure set …` fell through to the interactive
  path, read EOF, and wrote nothing → `set did not write the lane: rc=0`
  (`70`→`69 passed, 1 failed`).
- GREEN after implementation: `70 passed, 0 failed`.

Covers: set writes the lane; get echoes the effective line; list shows the
override; re-set replaces (single `^triage:` line); unsafe `$(...)` spec exits
non-zero and never executes; unknown-vendor spec rolls back leaving the prior
file byte-identical; unknown and uppercase lanes exit 2; unset removes it.

## Real runtime evidence

Full CONTRIBUTING check set, MacStudio, all green:

- `bash -n scripts/configure.sh` → OK
- `shellcheck -S warning scripts/configure.sh` → exit 0
- `bash tests/run.sh` (Bash 5) → `70 passed, 0 failed`
- `/bin/bash tests/run.sh` (macOS Bash 3.2) → `70 passed, 0 failed`

Out-of-harness smoke through the real `omnilane` entrypoint (isolated `HOME`
and `OMNILANE_HOME`):

```
1) set  -> set hardest-coding -> claude claude-opus-4-8 xhigh   (exit 0)
2) get  -> hardest-coding:  claude claude-opus-4-8 xhigh        (exit 0)
3) list -> # updated by 'configure set' … / hardest-coding: claude claude-opus-4-8 xhigh
4) effective (omnilane list) -> hardest-coding:  claude claude-opus-4-8 xhigh   (override wins)
5) unset -> unset hardest-coding (local override removed)       (exit 0)
6) list after unset -> no local overrides in …/routing.local.yaml
7) unknown lane -> omnilane: unknown lane 'nope' …             (exit 2)
8) injection `codex $(touch PROOF) low` -> unsafe routing spec  (exit 2, PROOF-absent-good)
```

Result: **VERIFIED** — real entrypoint end to end; the override changes the
effective `omnilane list` table; injection is rejected before any write.

## Docs

Added the `configure set|get|unset|list` line to all five READMEs' command
reference, expanded the English Configure section, and added an `## [Unreleased]`
CHANGELOG entry. VERSION and what's-new titles untouched — no release action.

## Known risks / notes

- Multi-word models must be passed as one quoted SPEC with the model quoted
  inside, e.g. `configure set triage 'gemini "Gemini 3.5 Flash (Low)" -'`.
  Documented in `cfg_usage` and the README.
- `set` requires a lane already present in `routing.yaml`; brand-new custom lanes
  still need a hand edit (intentional typo-guard, matches the interactive menu).

## Recommendation

Select. Fills a real automation gap, reuses the existing validator and safety
idioms, fully offline-verified incl. real runtime, no new dependencies.

## Follow-up commit — configure diff

A second commit adds `configure diff`, which shows how the local overrides change
the effective table versus the defaults. It resolves both tables through
`dispatch.sh --list` (the real one for `OMNILANE_HOME`, a defaults-only one via a
throwaway empty `OMNILANE_HOME`) and prints `default>` / `local>` pairs only for
lanes that actually differ, so annotation and formatting stay consistent and
unchanged lanes are omitted. With no overrides it says so and makes no diff.

New test `test_configure_diff` covers the empty case and asserts an override
surfaces `default>`/`local>` triage lines while an unchanged lane
(`hardest-coding`) does not appear. Suite: `71 passed, 0 failed` (Bash 5 and
Bash 3.2); `bash -n` + `shellcheck` clean.

Real smoke:

```
diff (no overrides) -> no local overrides (...); effective table equals the defaults
after set triage=claude, live-search=off ->
  default> live-search:     grok grok-4.5 -
  local  > live-search:     off
  default> triage:          codex gpt-5.6-luna medium
  local  > triage:          claude claude-opus-4-8 high
```

Result: **VERIFIED** — real entrypoint; only the two overridden lanes appear,
each with its default and local resolution.
