<div align="center">

# omnilane

### One routing table, every harness.

*Your main loop stops guessing which model to use.*<br/>
Drive it from **Claude Code · Codex · Grok Build · Antigravity**, and every subtask goes<br/>
to the model that is actually best at it — Codex, Claude, Grok, Gemini, Kimi, Qwen, OpenCode,<br/>
or any hosted model via OpenRouter — on the subscriptions you already pay for, or a single API key.

<img src="docs/hero.png" alt="omnilane routes each subtask to the best model across Claude Code, Codex, Grok and Antigravity" width="820"/>

[![ci](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml/badge.svg)](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml)
[![license](https://img.shields.io/github/license/Seraphim0916/omnilane)](LICENSE)
[![version](https://img.shields.io/github/v/tag/Seraphim0916/omnilane?label=version)](https://github.com/Seraphim0916/omnilane/tags)

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

</div>

---

## 🤔 What is omnilane?

**The problem.** You already drive an AI coding assistant — Claude Code, Codex,
Cursor, Gemini CLI. Each one talks to a single model family. So every task you
give it runs on that one model, whether or not it is the right tool: a
throwaway file rename burns your most expensive model, and a genuinely hard
architecture question runs on whatever you happened to open.

**What omnilane does.** It gives your assistant a routing table. Work gets
sorted into **lanes** — hardest coding, bulk mechanical, triage, hard judgment,
final polish — and each lane names the model that is best (and cheapest) for
it. Your assistant keeps the lanes it is already good at and hands the rest to
another vendor's CLI in the background, using the logins you already have.

**What it is not.** Not a proxy, not a new subscription, not another service to
keep alive. It is a table plus a dispatch script that runs behind the tool you
already use. `./install.sh --uninstall` removes every trace.

**You do not need every vendor.** Each lane is a fallback chain. Install one
CLI or seven — dispatch picks the first candidate you actually have, and a lane
with nothing available simply turns off. The default table works on a single
subscription.

**[⬇ Jump to the 60-second start](#-60-second-start)** · **[❓ Read the FAQ](#-faq)**

## ⚡ 60-second start

**The quick way — install from npm:**

```bash
npm i -g omnilane                                    # install the CLI
omnilane route hardest-coding "fix the flaky auth token refresh"
omnilane doctor                                      # see which AI CLIs / keys you have
omnilane ui start                                    # optional: watch jobs live in your browser
```

**Or clone the repo** (gets you the routing table and skill to customise):

```bash
git clone https://github.com/Seraphim0916/omnilane && cd omnilane
./install.sh          # finds your CLIs, links the skill, speaks your language
omnilane route hardest-coding "fix the flaky auth token refresh"
```

> New to this? Run `omnilane doctor` first — it tells you which model CLIs and
> API keys omnilane can already reach, so you know what will actually run.

## 🧭 How it works

omnilane lets the main loop of **any** agentic CLI classify subtasks into
lanes and dispatch each lane to the best vendor — headlessly, using your
existing subscription logins (or, for the `openrouter` vendor, a direct API
key with no extra CLI at all):

```mermaid
flowchart LR
    M["main loop<br/><i>any CLI you drive</i>"] --> T{{"routing.yaml<br/>one shared table"}}
    T -->|hardest-coding| C1["Codex — GPT-5.6 Sol"]
    T -->|bulk-mechanical| C2["Codex — GPT-5.6 Terra"]
  T -->|taste-final| C3["Claude — Opus 5"]
    T -->|long-context| C4["Gemini — 3.1 Pro"]
    T -->|live-search| C5["Grok — 4.5"]
    T -->|"arbitrate (opt-in)"| C6["vote — 1-4 model panel"]
```

- **`routing.yaml`** — lane → vendor + model + effort. One file, read by every
  harness.
- **Fallback chains** — a lane can list candidates
  (`codex … | claude … | off`); dispatch picks the first vendor CLI you actually
  have, so the default table works even with a single subscription.
- **`scripts/dispatch.sh [--vendor V] <lane> "<task>"`** — resolves the lane
  and shells out to the vendor's CLI headlessly. `--vendor` selects one named
  vendor without fallback.
- **`skills/omnilane/SKILL.md`** — a single skill every harness can load:
  identify your own model, self-execute your lane, dispatch the rest.
- **`omnilane mcp`** — the same routing surface as an MCP stdio server,
  for hosts that integrate via MCP instead of skills.

<div align="center">

| | | |
|:---:|:---:|:---:|
| 🧭 **One table**<br/>four harnesses share it | 🪂 **Fallback chains**<br/>degrades to the CLIs you have | 🗳️ **Opinion panel**<br/>multi-model vote for big calls |
| 🔒 **Safety rails**<br/>locks · watchdogs · no nesting | 🌏 **Five languages**<br/>the installer speaks your locale | ↩️ **Reversible**<br/>`--uninstall` undoes everything |

</div>

## 🛤️ Lanes

Defaults below — run `scripts/dispatch.sh --list` for the table your machine
actually resolves.

| Lane | First choice | Backup | When |
|---|---|---|---|
| 🔥 hardest-coding | GPT-5.6 Sol (max) | Claude Opus 5 (xhigh) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| 🏗️ bulk-mechanical | GPT-5.6 Terra (max) | Claude Sonnet 5 (high) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| 🧹 triage | GPT-5.6 Luna (medium) | Gemini 3.6 Flash (Low) | High-volume scans, first-pass filtering |
| ⚖️ hard-judgment | Claude Opus 5 (xhigh) | GPT-5.6 Sol (max) | Architecture arbitration, deep reasoning, second opinions |
| ✒️ taste-final | Claude Opus 5 (high) | GPT-5.6 Sol (max) | User-facing prose, prompt/doc polish, style arbitration |
| 💬 consult | Explicit named vendor/model | — (no fallback) | Direct natural-language consultation; always keep `--vendor` |
| 🎨 ui-draft | GPT-5.6 Sol (xhigh) | Claude Opus 5 (high) | UI drafts only WITH a design system / reference images |
| 📚 long-context | Gemini 3.1 Pro (High) | Claude Opus 5 (high) | 1M-token sweeps and retrieval; for multi-hop synthesis prefer the Claude candidate, and Flash for fast repeated loops |
| ⚡ fast-agentic | Gemini 3.6 Flash (High) | GPT-5.6 Luna (high) | Fast multi-step agentic loops, multimodal checks |
| 📡 live-search | Grok 4.5 | — (off) | Realtime X/web search and social context |
| 🚰 coding-overflow | Grok 4.5 | Kimi K3 → Qwen3 Coder Plus → OpenCode | Codex-quota relief valve for mid-tier coding |
| 🗳️ arbitrate | off (opt-in vote panel) | — | Built-in opinion panel for big calls — disabled by default; enable it in `routing.local.yaml`, one call per voter per round |

The **backup** is the next candidate in the lane's `routing.yaml` chain — what
dispatch falls back to when the first-choice vendor CLI is not installed. Every
lane is such a chain; when nothing in it is installed the lane degrades to `off`.

### Natural-language consultation

With the `omnilane` skill or `/route`, you can ask normally:
**“Ask Opus to challenge this architecture.”** The Agent Skill interprets the
request; this is not a free-form shell parser in `dispatch.sh`.

- A capability-only question recommends the first available model for the
  matching lane and makes no model call.
- A generic vendor name uses that vendor's configured candidate in `consult`.
- A canonical alias such as Opus pins its exact model family from the skill
  table. If an explicit target is absent or unavailable, the command fails
  clearly instead of falling back to another vendor or family.

<details>
<summary><b>👉 Which lanes do you run yourself? Pick your main model</b></summary>

<br/>

The table above is vendor-neutral — the *best* model for a lane doesn't change
with who is driving. What changes is which lanes you **self-execute** (you
already are that model, so no second call) versus **dispatch**. Your harness's
`omnilane` skill applies the right row automatically; this is the human view.

- **Claude Code · Fable 5** — self-execute: hard-judgment, taste-final, the hardest correctness-critical fixes. Dispatch mechanical coding volume → Codex, long-context → Gemini, live-search → Grok.
- **Claude Code · Opus 5** — self-execute: hard-judgment and taste-final. Dispatch bulk coding to Codex lanes, long-context → Gemini, live-search → Grok.
- **Codex · Sol** — self-execute: hardest-coding, hard-judgment, ui-draft. Dispatch taste-final → Claude, long-context → Gemini, live-search → Grok, bulk → Codex Terra.
- **Codex · Terra** — self-execute: bulk-mechanical. Escalate the genuinely hardest pieces to Sol; dispatch taste → Claude, long-context → Gemini, live-search → Grok.
- **Grok Build · Grok 4.5** — self-execute: live-search, coding-overflow (mid-tier coding). Dispatch everything hard to Codex/Claude/Gemini — and verify every API signature and cited fact first.
- **Antigravity · Gemini** — self-execute: long-context and context-heavy agentic work on 3.1 Pro, fast repeated loops on Flash. Dispatch hardest coding/judgment/taste to Codex/Claude; live-search → Grok.

</details>

## 🖥️ Live Board

Every dispatch — foreground or `--background` — is a job on disk. The Live
Board is an optional, read-only local workbench over that job store: what each
model was asked, what it answered, how it was routed, and whether it is still
running.

<div align="center">

<img src="docs/live-board.png" alt="Omnilane Live Board on desktop — job list on the left, task, public result and model path for the selected job on the right" width="820"/>

<img src="docs/live-board-mobile.png" alt="Omnilane Live Board on mobile — searchable job list with status filters" width="280"/>

</div>

```bash
omnilane ui start    # start or reuse the server and print its authenticated URL
omnilane ui status   # inspect the local server
omnilane ui url      # print the current authenticated URL
omnilane ui stop     # stop it cleanly
```

The desktop view keeps the job list and detail pane independently scrollable;
mobile uses a list/detail flow with Back and Esc navigation. Server-sent events
stream updates without replacing focused rows, and a short disconnect keeps the
last snapshot while reconnecting. Pin any loaded task as a reference, then
select another task to compare both model paths and public results side by side.
The reference is memory-only and disappears when the page closes. The board
binds only to `127.0.0.1`, uses a random token, and is read-only. It shows
`task.txt` and the public `out.txt`, but never raw worker or vendor logs.

The board reads in English, Japanese, Korean, Traditional Chinese and Simplified
Chinese. It follows the browser language on first load; the switcher in the
header overrides that and the choice is remembered locally.

Core routing does not need Python; only this UI requires Python 3.9 or newer.

## 📦 Install

Requirements: the vendor CLIs you want to route to, logged in (`codex`,
`claude`, `grok`, `agy`, and optionally `kimi`, `qwen`, `opencode`) and on
`PATH` — install only the ones you have; the rest of the table degrades
automatically. The `openrouter` vendor is the exception: it needs no CLI,
only `curl` and an `OPENROUTER_API_KEY` in your environment.

Quickest: `./install.sh` — symlinks the skill for the CLIs it finds, prints
the plugin commands for the rest, shows your effective routing, and offers the
interactive lane configurator (`--uninstall` reverses it). The installer
speaks English, 繁體中文, 简体中文, 日本語 and 한국어 (auto-detected from
your locale; force with `OMNILANE_LANG=zh-TW` etc.). It also offers an
optional per-CLI **routing reminder**: a marked, reversible block appended to
each CLI's instruction file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.grok/Agents.md`, `~/.gemini/GEMINI.md` — paths may vary across CLI
versions) so the main loop remembers to consult the table; non-interactive
installs can pass `OMNILANE_HOOKS=all|none|claude,codex`.

Use `./install.sh --check` for a read-only drift report. Add `--dry-run` to an
install or `--uninstall` to preview every checkout-owned file action.
Rollback the installer-owned links and marked reminders with
`./install.sh --uninstall`.

Manual wiring:

- **Claude Code**: install as a plugin (ships the skill + `/route`,
  `/route-jobs` commands, and a `SessionStart` hook that auto-injects the
  routing reminder at session open — no CLAUDE.md edit needed), or drop
  `skills/omnilane` into `~/.claude/skills/`.
- **Codex**: drop/symlink `skills/omnilane` into `~/.codex/skills/`.
- **Grok Build**: `grok plugin install <this repo> --trust`
- **Antigravity**: `agy plugin install <this repo>` (check first with
  `agy plugin validate <this repo>`)

### MCP server

`omnilane mcp` starts a zero-dependency, local MCP stdio server so any
MCP-capable host can discover and call omnilane without installing the skill or
adding a routing reminder. Configure the host to launch the installed CLI:

```json
{
  "mcpServers": {
    "omnilane": {
      "command": "omnilane",
      "args": ["mcp"]
    }
  }
}
```

The server exposes `route` plus read-only introspection: `list_lanes`,
`explain`, `validate`, `dry_run`, `jobs_list`, `jobs_status`, `jobs_result`,
`jobs_stats`, `jobs_audit`, and `doctor`. `route` defaults to read-only `advise` mode. Calls that select `work` must also
provide an explicit `workdir`.

Node.js is the only runtime requirement (no npm packages). If you prefer
npm, `npm install -g omnilane` installs the CLI with the MCP server
included.

## ⚙️ Configure

Three layers, all optional:

1. **Interactive menu** — `scripts/configure.sh` lists configurable lanes, lets you
   pick vendor → model → effort per lane from suggestions (or free text for
   future models), and writes the result to `~/.omnilane/routing.local.yaml`.
   It intentionally skips the multi-vendor `consult` lane; edit that one by
   hand if needed. `install.sh` offers to run the menu at the end of a normal install.
   For scripting, `configure set|get|unset|list|diff LANE [SPEC]` edits or inspects the same file
   without a tty — `set` validates the lane and rejects an unsafe or structurally
   invalid spec, rolling back on failure.
2. **`~/.omnilane/routing.local.yaml`** — hand-edited overrides, same format
   as `routing.yaml`; local lines win. See `routing.local.yaml.example`.
3. **`~/.omnilane/local.sh`** — per-machine binaries, proxies, auth wrappers;
   sourced by every runner, never committed. See `local.sh.example`.

Check the result any time:

```
scripts/dispatch.sh --list     # effective table, fallback resolution annotated
```

## 📖 Command reference

```
omnilane list | route … | jobs … | configure   # global wrapper, works anywhere
                                               # (install.sh links it into ~/.local/bin)
eval "$(omnilane completion bash)"             # enable Bash completion for this shell
source <(omnilane completion zsh)               # enable Zsh completion for this shell
omnilane completion fish | source              # enable Fish completion for this shell
omnilane mcp                                   # MCP stdio server (needs Node.js)
omnilane release-audit [--target VERSION] [--json] # offline, read-only release gate
omnilane ui start                              # start/reuse the local Live UI; print its URL
omnilane ui status                             # report whether the Live UI is running
omnilane ui url                                # print the current authenticated local URL
omnilane ui stop                               # stop the Live UI
omnilane doctor [--json]                       # read-only routing and runtime health report
dispatch.sh [--background] [--dry-run] [--mode advise|work] [--workdir DIR]
            [--vendor V] [--model M] [--effort E] [--timeout SEC] [--job-timeout SEC]
            LANE "TASK"                              # "-" reads task from stdin
dispatch.sh [--json] --list [--json]
dispatch.sh [--json] --explain LANE [--json]       # offline candidate-by-candidate decision trace
dispatch.sh [--json] --validate [--json]           # lint effective routing; no provider calls
jobs.sh [--json] {list | status ID | result ID}    # JSON result reports metadata, never bodies
jobs.sh [--json] list [--lane L] [--vendor V] [--status running|done]  # filter the listing
jobs.sh wait ID [--timeout N]                     # job exit; 124 timeout; 125 dead worker
jobs.sh cancel ID                                 # stop a running job: group SIGTERM, then SIGKILL
jobs.sh rm ID                                     # delete one finished/dead job (refuses a running job)
jobs.sh [--json] stats [--last N] [--lane L] [--vendor V]  # local success and routing aggregates
jobs.sh audit [--last N] [--json]                  # read-only job integrity/privacy check
jobs.sh prune [--keep N] [--apply]                # preview by default; completed jobs only
configure.sh                                        # interactive lane menu
configure.sh set|get|unset|list|diff LANE [SPEC]    # script/inspect routing.local.yaml, no tty
```

**Big decisions can get a panel, not a person.** The `arbitrate` lane ships
**disabled** — a panel costs one call per voter per round, so it is opt-in.
Enable it with `arbitrate: vote codex,claude,grok -` in `routing.local.yaml`,
or through the configurator, which lets you pick any 1-4 voters from
codex/claude/grok/gemini. The same question then goes to every voter, the
opinions come back side by side, and the calling model chairs the verdict.
Set the effort field to `2` for a debate round — every voter sees the whole
panel and rebuts only the disagreements. Power users can swap in their own
gate via the `exec` vendor:
`arbitrate: exec /path/to/script -` — the script receives
`MODE WORKDIR EFFORT PROMPT_FILE OUTPUT_FILE` and writes its verdict to
`OUTPUT_FILE` (see `scripts/runners/run-exec.sh`).

Exit codes: `2` bad usage (including an invalid vendor or a requested vendor
absent from the lane), `3` lane disabled (off), `4` no vendor CLI available in
the chain or the requested vendor is configured but its CLI is unavailable,
`5` too few successful Round 1
voters, `6` no Round 2 rebuttal succeeded, `86` nested dispatch refused, `87`
lock timeout, `124` whole-job timeout expired; otherwise the worker's own exit
code passes through.

## 🎭 Modes

- **advise** (default) — read-only worker. Codex runs in a read-only sandbox;
  Claude gets only Read/Glob/Grep; Grok runs in plan mode; Kimi and OpenCode
  pin their read-only plan modes; OpenRouter is advise-only by design (pure
  inference). Use for reviews, questions, second opinions.
- **work** — the worker may edit files, only inside the `--workdir` you name.
  Codex gets a workspace-write sandbox; Claude auto-accepts edits; Gemini runs
  in accept-edits mode. The `openrouter` vendor refuses work mode with a clear
  error — route edits to an agentic CLI vendor instead.

## 🔒 Safety rails

- **No nested dispatch** — workers cannot fan out again (`OMNILANE_DEPTH`
  guard, exit 86): no runaway agent-calls-agent quota chains.
- **Serialized codex** — same-target-directory codex dispatches queue behind a
  lock keyed on the normalized workdir; stale locks from crashed jobs are
  detected by owner PID and stolen safely.
- **Watchdog** — every worker runs under `timeout`/`gtimeout`, or a perl-alarm
  fallback when neither exists (stock macOS), so a hung CLI cannot block
  forever. The cap applies to **each CLI invocation**, highest priority first:
  `--timeout SECONDS` beats a per-lane `OMNILANE_TIMEOUT_<LANE>` (the lane
  upper-cased with `-`→`_`, e.g. `OMNILANE_TIMEOUT_HARD_JUDGMENT`) beats the
  global `OMNILANE_TIMEOUT`, default 600s. It is a per-call hang-guard, not a
  whole-job budget: a retrying vendor (grok) or the `vote` panel (voters ×
  rounds) makes several calls, so total wall-clock can be a multiple of this
  value.
- **Whole-job fuse** — optional `--job-timeout SECONDS` caps lock wait plus all
  retries, voters, and rounds under one process-group supervisor. Priority is
  flag > `OMNILANE_JOB_TIMEOUT_<LANE>` > `OMNILANE_JOB_TIMEOUT` > disabled,
  with one automatic exception: Codex `work` outside a Git worktree uses the
  resolved per-call watchdog as its whole-job fuse when none was configured,
  capped at the supervisor's 999999999-second maximum. This automatic guard
  needs the bundled Perl supervisor; if unavailable, dispatch warns and keeps
  non-Git work running through the existing per-call watchdog path, which emits
  its own warning if no watchdog tool exists.
  Expiry cleans the supervised process group and returns 124. For a deep audit
  of a large repository, start around 2–4 hours (7200–14400s)
  with a 30-minute per-call watchdog; these are recommendations, not defaults.
- **Background lifecycle** — `--background` workers run in their own process
  group and survive the caller's exit; killed workers record an exit code, and
  `jobs.sh status` reports `dead` instead of `running` forever.
- **Payload caps** — oversized task text is truncated head+tail before it can
  blow a worker's context.

## ❓ FAQ

<details>
<summary><b>Do I need all of these subscriptions?</b></summary>

<br/>

No. Every lane is a fallback chain, and dispatch picks the first candidate
whose CLI is actually installed. With one subscription the whole table collapses
onto that vendor; lanes with nothing available turn off rather than failing.
`omnilane doctor` shows exactly what your machine can reach today, and
`routing.local.yaml.example` ships starter profiles for common situations
(Claude-only, Codex-heavy, no-Codex).

</details>

<details>
<summary><b>Does omnilane send my code somewhere new?</b></summary>

<br/>

No new destination. Dispatch shells out to vendor CLIs you already installed
and logged into, so your code reaches exactly the vendors you already use.
Runners strip API-key environment variables before invoking a subscription CLI,
so a stray key cannot silently switch you onto pay-per-token billing. The one
exception is the direct-API vendor family (`openrouter`, `deepseek`, `zai`,
`mistral`, `groq`, `cerebras`), which by definition calls that provider's API
with the key you set — those are advise-only and never edit files.

</details>

<details>
<summary><b>Where is Claude Fable 5? Why is it not in the default table?</b></summary>

<br/>

**Because the top Claude tier is usually the main loop itself, not a dispatched
worker.** Lanes exist to send work to a model *other than* the one you are
driving. If Fable 5 is your main loop, routing judgment and taste back to Fable 5
just adds a second call for no gain — which is why the "pick your main model"
list above gives Fable 5 its own row as a **driver**, self-executing
hard-judgment, taste-final, and the hardest correctness-critical fixes.

**The measurements do not argue for it as a worker either.** On the Artificial
Analysis Intelligence Index (2026-07-24) Opus 5 (max) scores 61 and Fable 5 (max)
scores 60 — Artificial Analysis calls them "effectively tied", and Epoch AI's
Capability Index ranks them the other way (Fable 5 161, Opus 5 159). Call it a
draw on general intelligence. Where they are not tied is agentic professional
output, and Opus 5 leads by a wide margin:

| Benchmark | Claude Opus 5 (max) | Claude Fable 5 | |
|---|---:|---:|---|
| AA-Briefcase (agentic knowledge work, Elo) | 1720 | 1574 | **+146** |
| GDPval-AA v2 (Elo) | 1861 | 1747 | **+114** |
| Cost per AA-Briefcase task | $17.79 | $22.30 | **-20%** |
| API price, input / output per 1M | $5 / $25 | $10 / $50 | **half** |

Opus 5's max, xhigh and high tiers sweep the top three AA-Briefcase places, and
its `high` tier still beats Fable 5 at under half the cost per task. So Fable 5
costs twice as much without buying an advantage on any axis a lane is defined
around.

**What Fable 5 is genuinely better at**: factual breadth. It stays ahead of
Opus 5 on AA-Omniscience, as its size class suggests, and Opus 5 answers more
readily when uncertain — its hallucination rate is 50%, up 14 points from
Opus 4.8. If your task is recall-heavy rather than execution-heavy, name
Fable 5 explicitly:

```bash
dispatch.sh --vendor claude --model claude-fable-5 --effort high consult "…"
```

**This is a cost / main-loop policy choice, not a capability verdict.** Fable 5
is in the configurator's model menu, and one line in `routing.local.yaml`
overrides the default if you disagree:

```yaml
taste-final: claude claude-fable-5 high
```

</details>

<details>
<summary><b>Why do the Claude lanes use <code>xhigh</code> instead of <code>max</code>?</b></summary>

<br/>

Because more effort is not monotonically better. Anthropic documents `xhigh` as
the starting point for coding and agentic work, `high` as the floor for other
intelligence-sensitive work, and `max` as the setting for cases where
correctness outweighs cost. Independent testing agrees: on Vals.ai's Vibe Code
Bench, Opus 5 scores 89.8% at `high` but only 88.3% at `xhigh` and 88.4% at
`max` — the top tiers produce more elaborate solutions that fail more often.
Raise any lane locally if your workload disagrees:

```bash
omnilane configure set hard-judgment "claude claude-opus-5 max"
```

</details>

<details>
<summary><b>What happens when a lane's first-choice CLI is missing?</b></summary>

<br/>

Dispatch walks the chain and uses the first vendor you have. Inspect the
decision without spending a call:

```bash
scripts/dispatch.sh --explain hardest-coding   # candidate-by-candidate trace
scripts/dispatch.sh --list                     # whole effective table
scripts/dispatch.sh --dry-run hardest-coding "…"   # fully resolved plan, no provider call
```

</details>

<details>
<summary><b>Can a dispatched worker edit my files?</b></summary>

<br/>

Only if you ask for it. Dispatch defaults to `advise`, a read-only mode enforced
per vendor (read-only sandbox, plan mode, or read-only tool set depending on the
CLI). Editing requires both `--mode work` and an explicit `--workdir`. Workers
also cannot dispatch again — the depth guard refuses nested fan-out with exit 86,
so one command can never spiral into a chain of agents spending your quota.

</details>

## 📊 Defaults and provenance

Default lane assignments follow Artificial Analysis coding/intelligence data
(2026-07 snapshot, cross-checked against AA site records and vendor pricing
pages) plus published head-to-head reviews; they are opinions, not laws — the
configurator and `routing.local.yaml` exist so you can disagree. The full
working notes, including per-benchmark caveats, live in
[`docs/model-capabilities-2026-07.md`](docs/model-capabilities-2026-07.md).

## ⚠️ Known limitations

- **Antigravity tool calls in print mode are unstable** in current CLI builds
  (tool calls may be denied or rejected with invalid-argument errors). The
  long-context lane is designed for content-you-paste-in synthesis, which is
  unaffected; for repo *inspection* prefer the claude/codex candidates.
- **Grok has no reasoning-effort knob**; the effort field is accepted for
  interface parity and ignored.
- **Non-Git Codex work is supported.** Some Codex CLI builds may stall outside
  a Git worktree, so the automatic fuse above bounds that case and cleans the
  supervised process group. Omnilane neither initializes nor requires a repository.

## 📜 Release history

## What's new in v0.10.4

- **`long-context` no longer points multi-hop work at the wrong model** — the
  lane called itself long-document *synthesis* while shipping Gemini first, but
  published multi-needle scores at 1M favour Claude by roughly threefold while
  Gemini leads single-needle retrieval. The lane now describes retrieval and
  volume sweeps and names the Claude candidate for integration work. Ordering is
  unchanged; the evidence is secondary and covers prior model generations.
- **The Coding Agent Index is no longer quoted as a number** — the same model
  reads 80, 78 or 67 depending on index version and harness. It is now cited for
  ordering only, with every observed value and its provenance recorded.
- **`taste-final` has writing evidence behind it** — previously ordered from
  general and agentic indexes that do not measure prose. Added EQ-Bench Creative
  Writing v3, EQ-Bench Longform, and the Lech Mazur benchmark, read from the
  publishers.
- **Per-effort cost and throughput** added to the model notes, showing why the
  defaults use `xhigh`: it reaches the same index score as `max` for 30-53% less
  per task.

## What's new in v0.10.3

- **Restructured READMEs in all five languages** — the reader now meets a plain
  "what is this and why would I want it" section first, version history is
  consolidated at the bottom instead of interrupting the introduction, and a new
  FAQ answers the questions that kept coming up: do I need every subscription,
  where does my code go, why is Fable 5 not in the table, why `xhigh` and not
  `max`, what happens when a CLI is missing, can a worker edit files.
- **Fixed: plugin manifests advertised a stale version** — `plugin.json` and
  `.claude-plugin/plugin.json` still reported `0.10.0` after the 0.10.1 and
  0.10.2 releases, so plugin installs showed the wrong version.
- **Fixed: `routing.local.yaml.example` shipped retired models** — the starter
  profiles still pointed at `claude-opus-4-8` and Gemini 3.5 Flash; they now use
  Claude Opus 5 (with lane-appropriate effort) and Gemini 3.6 Flash.
- **Corrected the Intelligence Index figures** in
  `docs/model-capabilities-2026-07.md` against the Artificial Analysis source
  (index points, not percentages), added the AA-Briefcase / GDPval-AA v2
  comparison, and recorded the two results that cut against the defaults:
  Fable 5's lead on factual knowledge and Sol's lead on presentation quality.

## What's new in v0.10.2

- **Claude effort on `hardest-coding` and `hard-judgment` moved from `max` to
  `xhigh`**, matching Anthropic's documented guidance for Claude Opus 5: start
  at `xhigh` for coding and agentic work, keep `high` as the floor for other
  intelligence-sensitive work, and reserve `max` for cases where correctness
  outweighs cost. Raise it back per lane with
  `omnilane configure set <lane> "<spec>"`.
- **Fixed two dead CHANGELOG compare links** that pointed at a `v0.10.0` tag
  which was never published.

## What's new in v0.10.1

- **`claude-opus-5` joins the default table** as first choice for
  `hard-judgment` and `taste-final`, plus a fallback for the hardest coding work.
- **`omnilane configure` covers all 13 providers** with 106 selectable model
  entries — current native catalogs for Codex, Claude Code, Grok Build and
  Antigravity, plus verified OpenRouter/OpenCode shortcuts. Custom model IDs
  remain available through `c`.

<details>
<summary>Older releases (v0.10.0 and earlier)</summary>

## What's new in v0.10.0

- **Gemini 3.6 Flash defaults** — the gemini candidates in `fast-agentic`,
  `triage`, and `bulk-mechanical` (and the `Gemini Flash` alias) now run
  Gemini 3.6 Flash: fewer output tokens, a lower output price, and the fastest
  output speed measured by Artificial Analysis.
- **Evidence re-audit** — routing comments, model capability notes, and the
  Gemini price table refreshed against official sources.

## What's new in v0.9.1

- **Fix:** `configure set` no longer deletes hand-written comments from
  `routing.local.yaml` — it rewrites only its own stamp header and the lane
  being replaced.

## What's new in v0.9.0

- **Five OpenAI-compatible direct-API vendors** — `deepseek`, `zai` (GLM),
  `mistral`, `groq`, and `cerebras` join `openrouter` as CLI-free lanes (curl +
  a `<VENDOR>_API_KEY`). A one-line `lib/common.sh` registry entry adds each;
  see [`docs/model-capabilities-2026-07.md`](docs/model-capabilities-2026-07.md).
- **Fish shell completion** — `omnilane completion fish | source`.

## What's new in v0.8.3

- **MCP server** — `omnilane mcp` starts a zero-dependency stdio MCP server,
  so any MCP-capable host (Claude Code, Codex, Gemini CLI, Cursor, OpenCode…)
  can discover and call omnilane without installing the skill: tools `route`,
  `jobs_status`, `jobs_result`, and `list_lanes`. `route` defaults to
  read-only advise mode; work mode requires an explicit workdir.

## What's new in v0.8.2

- **`openrouter` vendor** — dispatch straight to the OpenRouter API with
  nothing but `curl` and an `OPENROUTER_API_KEY`: hundreds of hosted models
  become reachable from any omnilane install, no coding-agent CLI required.
  Advise/consult only (it cannot edit files; work mode fails with guidance)
  and the model slug is mandatory, e.g.
  `dispatch.sh --vendor openrouter --model anthropic/claude-sonnet-5 consult "..."`.
- **`deepseek`, `zai`, `mistral`, `groq`, `cerebras` vendors** — the same
  CLI-free direct-API path as `openrouter`, for OpenAI-compatible providers:
  DeepSeek, Z.ai GLM, Mistral, Groq, and Cerebras. Each needs only `curl` and
  its `<VENDOR>_API_KEY`; advise/consult only. A one-line `lib/common.sh`
  registry entry defines each endpoint, key env, and default model. See
  [`docs/model-capabilities-2026-07.md`](docs/model-capabilities-2026-07.md).
- **`opencode` vendor** — headless dispatch through the OpenCode
  multi-provider aggregator CLI (`opencode run`). Advise mode pins OpenCode's
  built-in read-only `plan` agent; work mode uses `--auto`. Joins the default
  `coding-overflow` chain as its last fallback.

## What's new in v0.8.1

- **Claude Code plugin auto-loads the routing reminder** — the plugin now
  ships a `SessionStart` hook (`hooks/hooks.json`) that injects the routing
  reminder at session open (`startup|resume|clear`), so plugin installs get
  the persistent reminder with no edit to `~/.claude/CLAUDE.md`. The
  `install.sh` instruction-file reminder still covers the other CLIs.

## What's new in v0.8.0

- **Two new dispatch vendors** — `kimi` (Moonshot Kimi Code CLI) and `qwen`
  (Alibaba Qwen Code CLI) join the vendor set with the uniform runner
  contract: advise stays read-only, work auto-approves, API-key env is
  stripped so the CLIs use their own subscription logins, and empty output
  is a loud failure. Pin them with `--vendor kimi|qwen`.
- **coding-overflow grows a chain** — the quota relief valve now falls back
  grok → kimi → qwen before `off`, so it works with any one of the three
  vendors installed. Runners are contract-tested against fake binaries;
  real-model reports welcome.

## What's new in v0.7.1

- **Routing refresh (2026-07 model data)** — hardest-coding now dispatches
  GPT-5.6 Sol at **max** effort: Artificial Analysis Coding Agent Index v1.1
  scores Sol (max) at 80, the current state of the art, retiring the older
  xhigh-beats-max snapshot.
- **Claude backups sharpened** — the Claude Opus 4.8 fallback on
  hardest-coding and hard-judgment moves to **xhigh** effort, following
  Anthropic's guidance to use extra effort for difficult tasks and
  long-running work.

## What's new in v0.7.0

- **Preview any dispatch first** — `--dry-run` prints the fully resolved plan
  (vendor, model, mode, timeouts, side-effect decision) with no provider call
  and no job state.
- **Automate with versioned JSON** — one `--json` envelope for `--list`,
  `--explain`, `--validate`, and `jobs list|status|result|stats`, plus
  read-only `jobs wait`, `jobs audit`, and an offline `omnilane release-audit`
  gate with a deterministic manifest.
- **Drive local jobs end to end** — `jobs tail` peeks at live output,
  `jobs retry` re-dispatches a completed job fail-closed,
  `prune --older-than` ages out old jobs, and `--help` covers every command.
- **Install and complete safely** — `install.sh --check`/`--dry-run` report
  drift without writing, `omnilane completion bash|zsh` ships safe tab
  completion, and five macOS stock Bash 3.2 crashes are fixed.

## What's new in v0.6.0

- **Explain and validate routes offline** — inspect every fallback candidate
  with `--explain`, or lint the complete effective table with `--validate`,
  without invoking a provider or creating job state.
- **Inspect local health and outcomes** — bounded `jobs.sh stats` aggregates and
  `omnilane doctor --json` make local automation observable without exposing
  task or result bodies.
- **Compare runs in Live Board** — pin one loaded job as a memory-only reference
  and compare its model path and public result with the current selection.
- **Keep lock recovery quiet** — transient owner-file read races no longer leak
  misleading missing-file diagnostics.

## What's new in v0.5.1

- **Use Codex work outside Git** — ordinary directories remain supported;
  Omnilane never requires or runs `git init`.
- **Stop non-Git hangs cleanly** — the resolved per-call watchdog becomes an
  automatic process-group fuse when no whole-job timeout was configured, while
  explicit timeout precedence and exit semantics remain intact.
- **Trust the displayed version** — `VERSION` now drives `omnilane --version`
  and both plugin manifests, with CI checking the changelog and all five READMEs.

</details>

## 🌱 Status

omnilane now spans thirteen dispatch vendors — four harness natives (codex,
claude, grok, gemini), three aggregator/overflow CLIs (kimi, qwen, opencode),
and six CLI-free OpenAI-compatible direct-API vendors (openrouter, deepseek,
zai, mistral, groq, cerebras) — on the uniform runner contract with
contract tests, plus the Claude Code `SessionStart`
auto-reminder and an MCP stdio server surface (`omnilane mcp`). The direct-API
and aggregator runners are contract-tested against fake binaries;
real-model reports welcome. Grok/Antigravity command-shell behavior may still
vary across CLI versions. Issues and PRs welcome.

Project policies: [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Changelog](CHANGELOG.md)
