# omniroute

One routing table, every harness. omniroute lets the main loop of **any** agentic
CLI — Claude Code, OpenAI Codex, Grok Build, Google Antigravity — classify
subtasks into lanes and automatically dispatch each lane to the best vendor's
CLI, using your existing subscription logins.

```
            ┌────────────── routing.yaml (one table) ──────────────┐
 main loop ─┤ hardest-coding → Codex Sol      taste-final → Claude │
 (any CLI)  │ bulk-mechanical → Codex Terra   long-context → Gemini│
            │ triage → Codex Luna             live-search → Grok   │
            └────────────── scripts/dispatch.sh ───────────────────┘
```

## How it works

- **`routing.yaml`** — lane → vendor + model + effort. Override any lane in
  `~/.omniroute/routing.local.yaml`.
- **`scripts/dispatch.sh <lane> "<task>"`** — resolves the lane and shells out
  to the vendor's CLI headlessly. `--background` + `scripts/jobs.sh` for long
  tasks; `--mode work --workdir DIR` when the worker may edit files.
- **`skills/omniroute/SKILL.md`** — a single skill every harness can load:
  identify your own model, self-execute your lane, dispatch the rest.
- Safety rails built in: read-only workers by default, no nested dispatch
  (depth guard), same-directory codex dispatches serialized, payload caps.

## Install

Requirements: the vendor CLIs you want to route to, logged in (`codex`,
`claude`, `grok`, `agy`) and on `PATH`.

- **Claude Code**: install as a plugin (ships the skill + `/route`,
  `/route-jobs` commands), or drop `skills/omniroute` into `~/.claude/skills/`.
- **Codex**: drop/symlink `skills/omniroute` into `~/.codex/skills/`.
- **Grok Build**: `grok plugin install <this repo> --trust`
- **Antigravity**: `agy plugin install <this repo>` (check first with
  `agy plugin validate <this repo>`)

Per-machine binaries, proxies, or auth wrappers go in `~/.omniroute/local.sh`
(sourced by every runner; never committed).

## Defaults and provenance

Default lane assignments follow Artificial Analysis coding/intelligence data
(2026-07) plus published head-to-head reviews; they are opinions, not laws —
edit `routing.yaml` to taste. The `arbitrate` lane is off by default: wire it
to your own multi-model review gate if you have one.

## Status

Early. Runner interfaces are stable; Grok/Antigravity command-shell behavior
may vary across CLI versions. Issues and PRs welcome.
