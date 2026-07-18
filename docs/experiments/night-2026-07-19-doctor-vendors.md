# feat/doctor-plus — vendor-CLI availability in doctor

Branch: `feat/doctor-plus` (off `main` @ `c45955e`, v0.8.3)
Operator: claude-s (MacStudio), 2026-07-19

## User-visible outcome

`omnilane doctor` reported lane counts ("N lanes parsed, M usable") but not
*which* vendor CLIs were actually present, so a user seeing a lane degrade could
not tell which CLI was missing. This branch adds a `vendors` check:

```
PASS  vendors      present: codex claude grok gemini openrouter; missing: kimi qwen opencode
```

It probes each vendor exactly the way the runners resolve their binary:

| Vendor | Probe |
| --- | --- |
| codex / claude / grok / kimi / qwen / opencode | `command -v` on `${*_BIN:-<name>}` |
| gemini | `command -v` on `${AGY_BIN:-agy}` (the Antigravity CLI) |
| openrouter | `OPENROUTER_API_KEY` set **and** `curl` present (no CLI) |

The probe runs in a subshell that sources `local.sh` (with `set +u`, so a
machine-local `local.sh` that references its own unset vars can't abort the
report) and keeps any side effects out of doctor's environment. It is a
`PASS`/`WARN` check — `WARN` only when no vendor is reachable — so it never turns
a healthy report into a failure.

## Verification

- `bash -n scripts/doctor.sh` → OK; `shellcheck -S warning scripts/doctor.sh` → exit 0
- `bash tests/run.sh` (Bash 5) → `70 passed, 0 failed`; `/bin/bash` (Bash 3.2) → `70 passed, 0 failed`
- The pre-existing `doctor is read-only and actionable` test still passes — the
  new check is additive `PASS`/`WARN` and does not disturb its `0 failed`
  assertions or the read-only guarantees.

New test `test_doctor_vendors` runs doctor under a restricted `PATH` containing
only fake `codex`, `claude`, and `agy` binaries plus `OPENROUTER_API_KEY`, and
asserts the present set is `{codex, claude, gemini, openrouter}` and the missing
set is `{grok, kimi, qwen, opencode}` in both human and `--json` output. The
fake `agy` proving `gemini` present is what validates the gemini→agy mapping.

Real smoke through the `omnilane doctor` entrypoint:

```
1) full PATH  -> present: codex claude grok gemini openrouter; missing: kimi qwen opencode
2) PATH=fake codex+agy only, key set -> present: codex gemini openrouter; missing: claude grok kimi qwen opencode
3) --json -> {"level":"PASS","check":"vendors","message":"present: codex gemini openrouter; missing: ..."}
```

Result: **VERIFIED** — real entrypoint, human + JSON, correct detection incl. the
gemini→agy mapping and openrouter's key+curl rule, PATH-respecting.

## Docs

CHANGELOG `## [Unreleased]` entry only. `doctor`'s command signature
(`doctor [--json]`) is unchanged and the READMEs already describe it as a
"routing and runtime health report", which covers the new line — no README edit.

## Recommendation

Select. Turns "why did my lane fall back" into a one-line answer, additive and
non-failing, offline-verified incl. real runtime and the vendor→binary mappings.
