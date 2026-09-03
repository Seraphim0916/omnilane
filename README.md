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
    T -->|hardest-coding| C1["Claude — Fable 5.1"]
    T -->|bulk-mechanical| C2["Codex — GPT-5.6 Sol"]
    T -->|taste-final| C3["Claude — Fable 5.1"]
    T -->|long-context| C4["Gemini — 3.7 Flash"]
    T -->|live-search| C5["Grok — 4.6"]
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
| 🔥 hardest-coding | Claude Fable 5.1 (xhigh) | GPT-5.6 Sol (xhigh) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| 🏗️ bulk-mechanical | GPT-5.6 Sol (high) | Gemini 3.7 Flash (High) → Claude Sonnet 5 (high) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| 🧹 triage | GPT-5.6 Luna (high) | Gemini 3.7 Flash (Low) → Claude Haiku 4.5 | High-volume scans, first-pass filtering |
| ⚖️ hard-judgment | Claude Fable 5.1 (xhigh) | GPT-5.6 Sol (max) → Grok 4.6 | Architecture arbitration, deep reasoning, second opinions |
| ✒️ taste-final | Claude Fable 5.1 (high) | GPT-5.6 Sol (max) | User-facing prose, prompt/doc polish, style arbitration |
| 💬 consult | GPT-5.6 Sol (max) | Claude Fable 5.1 (high) → Grok 4.6 → Gemini 3.7 Flash (High) | Direct named-model consultation; keep `--vendor` to prevent fallback |
| 🎨 ui-draft | GPT-5.6 Sol (xhigh) | Claude Fable 5.1 (high) | UI drafts only WITH a design system / reference images |
| 📚 long-context | Gemini 3.7 Flash (Medium) | GPT-5.6 Terra (max) → Claude Opus 5 (medium) | Long-document retrieval and synthesis, ordered on AA-LCR, cost, and throughput |
| ⚡ fast-agentic | Gemini 3.7 Flash (Medium) | GPT-5.6 Luna (high) | Fast multi-step agentic loops, multimodal checks |
| 📡 live-search | Grok 4.6 | — (off) | Realtime X/web search and social context |
| 🚰 coding-overflow | Grok 4.6 | Gemini 3.7 Flash (High) → Kimi K3 → Qwen3 Coder Plus → OpenCode | Codex-quota relief valve for mid-tier coding |
| 🗳️ arbitrate | off (opt-in vote panel) | — | Built-in opinion panel for big calls — disabled by default; enable it in `routing.local.yaml`, one call per voter per round |

The **backup** is the next candidate in the lane's `routing.yaml` chain — what
dispatch falls back to when the first-choice vendor CLI is not installed. Every
lane is such a chain; when nothing in it is installed the lane degrades to `off`.

> **Fable 5.1 is in the defaults — and where Opus 5 still fits.** See the current three-way evidence and Opus override in the [FAQ](#-faq).

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

- **Claude Code · Fable 5.1** — self-execute: hard-judgment, taste-final, hardest-coding. Dispatch bulk → Codex Sol high; long-context and fast loops → Gemini 3.7 Flash; live-search → Grok.
- **Claude Code · Opus 5** — self-execute: hard-judgment and taste-final when its lower hallucination rate or price is preferred. Dispatch hardest coding → Fable 5.1 or Sol, bulk → Sol high, long-context and fast loops → Gemini 3.7 Flash, live-search → Grok.
- **Codex · Sol** — self-execute: hardest-coding, bulk-mechanical, hard-judgment, ui-draft. Dispatch taste-final → Claude, long-context and fast loops → Gemini 3.7 Flash, live-search → Grok.
- **Codex · Terra** — self-execute: long-context as the Codex fallback. Bulk-mechanical now defaults to Sol high; escalate hardest pieces to Sol xhigh, taste → Claude, fast loops → Gemini 3.7 Flash, live-search → Grok.
- **Grok Build · Grok 4.6** — self-execute: live-search and coding-overflow. Dispatch hard coding/judgment/taste to Codex/Claude/Gemini; verify API signatures and cited facts.
- **Antigravity · Gemini 3.7 Flash** — self-execute: long-context and fast loops at Medium, bulk/overflow at High, triage at Low. Dispatch hardest coding/judgment/taste to Codex/Claude; live-search → Grok.

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

Search and state filters apply to the latest 50 retained jobs. **Export visible**
downloads only the currently visible public metadata as local JSON; it excludes
tokens, task text, result bodies, workdirs, and raw logs.

The board reads in English, Japanese, Korean, Traditional Chinese and Simplified
Chinese. It follows the browser language on first load; the switcher in the
header overrides that and the choice is remembered locally.

Core routing does not need Python; only this UI requires Python 3.9 or newer.

### Foreman completion inbox

Finished dispatches write a private completion record under `$OMNILANE_HOME/inbox/`. A Claude Code `SessionStart` hook binds each foreman's hook-provided session ID to its live process ancestry, and the bundled `UserPromptSubmit` hook atomically delivers up to ten records owned by that session on the foreman's next prompt. Older records without a session ID still use the original dispatch `workdir` scope; claimed records move to `inbox/consumed/`. Set `OMNILANE_INBOX=0` on a dispatch to disable record creation; the default is enabled.

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

The foreman completion notice requires the Claude Code plugin install; the
skill-only symlink path does not deliver completion notices. Install the plugin
from this checkout with:

```bash
claude plugin marketplace add <path to this repo>
claude plugin install omnilane@omnilane
```

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
`jobs_stats`, `jobs_recommend`, `jobs_audit`, `doctor`, and the explicitly opt-in
`provider_probe`. `route` defaults to read-only `advise` mode. Calls that select `work` must also
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
omnilane doctor [--json] [--strict] [--probe V] [--probe-timeout SEC]  # live probe is opt-in
omnilane benchmark [--json] [--run] [--vendor V] [--cost-per-call V=USD] # dry-run by default
dispatch.sh [--background] [--dry-run] [--thread NAME] [--mode advise|work|sysops] [--workdir DIR]
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
jobs.sh [--json] recommend [--last N] [--lane L] [--min-samples N]  # evidence-gated vendor suggestion
jobs.sh audit [--last N] [--json]                  # read-only job integrity/privacy check
jobs.sh prune [--keep N] [--apply]                # preview by default; completed jobs only
configure.sh                                        # interactive lane menu
configure.sh set|get|unset|list|diff LANE [SPEC]    # script/inspect routing.local.yaml, no tty
```

`--thread NAME` continues named Claude, Codex, Grok, or Gemini conversations across
single-shot dispatches. In 0.33.0 it pins vendor, model, effort, and physical
workdir; use `jobs.sh threads`, `threads show NAME`, or `threads rm NAME` to
manage local state without deleting the vendor session.

`jobs recommend` reads only validated public metadata and exit codes. It ranks
eligible vendors by success rate, sample count, then name; the default minimum
is three completed jobs. It never reads task/result bodies or changes routing.

`doctor --probe V` makes one bounded advise-mode provider call and returns only
availability, selected model, timing, and response byte count—not the response
body. Without `--probe`, doctor remains offline. `benchmark` uses the fixed TSV
suite in `benchmarks/workloads.tsv`; its default dry-run resolves every route
without provider calls. `--run` is the explicit call gate, and cost totals are
estimates based only on values supplied with `--cost-per-call`.

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
- **sysops** — `work` minus the vendor sandbox, for service operations the
  sandbox denies (`launchctl` and friends). Codex runs it with
  `-s danger-full-access`; every other vendor treats it as plain `work`. This
  hands the worker full access to the machine, so it is an explicit
  per-dispatch opt-in and can never be a lane default. Reach for it only when
  you have watched `work` fail on a sandbox denial.

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

## 📬 Live mailbox

A live mailbox is a resident Claude or Gemini background dispatch, not a one-shot dispatch. The foreman opens it with `--background`, can send another instruction while it is still running, and is responsible for closing it with `jobs.sh close ID`. Leaving it unattended does not make it permanent: the idle cap and configured whole-job timeout (`--job-timeout`) can still end it.

```bash
scripts/dispatch.sh --background --vendor claude hard-judgment "Review the timeout failure"
# save the printed job ID as $ID
scripts/jobs.sh send "$ID" "Also inspect the retry path."
scripts/jobs.sh watch "$ID"
scripts/jobs.sh tail "$ID" --lines 20
scripts/jobs.sh close "$ID"
scripts/jobs.sh retry "$ID" --background
```

`watch` follows `$JOB_DIR/events.jsonl`; `tail` reads the public `out.txt`. Live mailbox support covers Claude and Gemini; other vendors run as normal one-shot dispatches, with a notice sent to stderr and stored in `$JOB_DIR/mode-notice.txt`. `--live` requires a resident session and fails fast when the resolved vendor is not capable. `--single-shot` forces one-shot execution even for Claude or Gemini. `--idle-timeout SECONDS` sets the inactivity cap (default 900; `0` disables it).

An idle mailbox makes no API calls and incurs no API spend. By default it closes after 900 seconds without a new inbox message or result event, while the whole-job timeout remains the outer cap. Close it sooner when its exchange is finished. `jobs.sh send` to a finished job or a job that is not live fails with a clear error. Do not use this for fire-and-forget work, vendors without live support, or a clean-slate rerun; start a fresh dispatch (or retry a completed job) instead.

## 🎯 Goal orchestration

`omnilane goal` is a foreman-driven ledger for exploratory work. The caller—an agent session or a human terminal—owns the loop: dispatch a job, receive its result through the completion inbox or `omnilane jobs wait`, decide the next job, and repeat. Job and elapsed-time budgets are unlimited by default; `--budget-jobs N` and `--budget-seconds S` opt in to each hard cap. omnilane supplies bookkeeping only, enforces any caller-supplied caps plus the always-on repeated-failure fuse before each goal dispatch, then assembles the report when the foreman closes the goal.

```bash
GOAL_ID="$(omnilane goal open "Fix the flaky checkout integration" \
  --budget-jobs 4 --budget-seconds 900 --workdir /path/to/repo)"
JOB_ID="$(omnilane goal dispatch "$GOAL_ID" --mode work hardest-coding \
  "Reproduce the checkout failure and implement the smallest verified fix")"
omnilane jobs wait "$JOB_ID" --timeout 900
omnilane goal note "$GOAL_ID" "Fix verified by the checkout integration test"
omnilane goal close "$GOAL_ID" --summary "Checkout integration is stable"
```

Goal state lives under `$OMNILANE_HOME/goals/<goal-id>/`. Use `goal status` to inspect budget usage, fuse trips, and each recorded job as its metadata and exit status land. `goal close` writes `report.md` and prints its path. For one obvious task, dispatch directly. When budget flags are supplied, those caps are hard bounds, not completion promises.

Do not use goal orchestration for a single obvious task; dispatch that task directly. The default unlimited budgets let the caller keep exploring without omnilane imposing a cap; pass either budget flag only when that limit is wanted.

## ❓ FAQ

<details>
<summary><b>Do I need all of these subscriptions?</b></summary>

<br/>

No. Every lane is a fallback chain, and dispatch picks the first candidate
whose CLI is actually installed. With one subscription the whole table collapses
onto that vendor; lanes with nothing available turn off rather than failing.
`omnilane doctor` shows exactly what your machine can reach today, and
`routing.local.yaml.example` ships starter profiles for common situations
(Claude-focused, Codex-heavy, no-Codex).

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
<summary><b>Fable 5.1 is in the defaults — and where Opus 5 still fits</b></summary>

<br/>

Fable 5.1 now leads `hardest-coding`, `hard-judgment`, and `taste-final`.
At matched xhigh effort it leads Opus 5 on intelligence, agentic work, and
coding. Sol max remains the far cheaper cross-vendor judgment fallback.

| Benchmark (AA, retrieved 2026-09-02) | Claude Fable 5.1 (xhigh) | Claude Opus 5 (xhigh) | GPT-5.6 Sol (max) |
|---|---:|---:|---:|
| Intelligence | 64.8 | 62.5 | 60.9 |
| Agentic | 59.8 | 58.4 | 57.8 |
| Coding | 80.7 | 77.0 | 77.4 |
| Hallucination rate (lower is better) | .71 | **.60** | .92 |
| AA $/task | $2.65 | $1.80 | **$0.95** |

Fable 5.1 is not a bulk or triage default: it costs twice Opus 5 per token and
consumes the most Claude Code subscription quota per turn. Opus 5 remains the
lower-hallucination, lower-price Claude option, stays in `long-context` at
medium, and remains selectable everywhere through
`~/.omnilane/routing.local.yaml`:

```yaml
hard-judgment: claude claude-opus-5 xhigh
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
CLI). Editing requires both `--mode work` and an explicit `--workdir`. A third
mode, `--mode sysops`, is `work` minus the vendor sandbox — for service
operations the sandbox denies (e.g. `launchctl`); codex runs it with
`-s danger-full-access`, other vendors treat it as `work`, and it is an
explicit per-dispatch opt-in, never a lane default. Workers
also cannot dispatch again — the depth guard refuses nested fan-out with exit 86,
so one command can never spiral into a chain of agents spending your quota.

</details>

## 📊 Defaults and provenance

Default lane assignments follow Artificial Analysis coding/intelligence data
(2026-07 snapshot, cross-checked against AA site records and vendor pricing
pages) plus published head-to-head reviews; they are opinions, not laws — the
configurator and `routing.local.yaml` exist so you can disagree. The full
working notes, including per-benchmark caveats, live in
[`docs/model-capabilities-2026-09.md`](docs/model-capabilities-2026-09.md).

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

## What's new in v0.33.0

- **Four-vendor threaded dispatch.** `--thread NAME` continues pinned Claude,
  Codex, Grok, or Gemini conversations across foreground or background
  single-shot jobs; direct-API vendors, `exec`, live mode, and pin mismatches
  stop with visible exit-2 notices.
- **Thread inspection.** `jobs.sh threads`, `threads show NAME`, and
  `threads rm NAME` list, inspect, or remove local thread state.

## What's new in v0.32.0

- **Full 2026-09 routing re-evaluation.** Fable 5.1 and Gemini 3.7 Flash enter the defaults, backed by the dated Artificial Analysis snapshot.
- **Catalogs match live CLI surfaces.** Fable 5.1 is added and retired Gemini 3.5 Flash rows are removed.
- **Opus 5 remains available.** It stays in `long-context` and can override any lane through `routing.local.yaml`.

## What's new in v0.31.0

- **Unlimited goal budgets by default.** `budget_jobs` and `budget_seconds` now persist as JSON `null` and render as `unlimited`; the previous implicit 8-job and 900-second caps are gone. Use `--budget-jobs N` or `--budget-seconds S` to opt in to a hard cap. The repeat-failure fuse remains enabled by default.
- **Pipe-safe goal status.** `omnilane goal status` now exits 0 when its consumer closes the pipe early instead of raising `BrokenPipeError`, so `| head` and `| grep -q` work under `pipefail`.

## What's new in v0.30.0

- **Goal ledger.** `omnilane goal open` creates a goal ledger with unlimited default budgets; `goal dispatch` gates each job on caller-supplied job or wall-clock caps and the always-on repeat-failure fuse. `goal note` preserves the caller's narrative, `goal status` shows budgets and per-job records, and `goal close` writes `goals/<id>/report.md`.
- **Caller owns the loop.** The session or person that opened the goal chooses, dispatches, reviews, and closes the work; omnilane does not run a built-in planning model.
- **Doctor coverage.** `omnilane doctor` now checks the goal-orchestrator surface.

## What's new in v0.21.0

- **Explicit session mode.** Use `dispatch --live` to require a resident session or `--single-shot` to force a one-shot job. `--live` fails immediately for incompatible vendors and lists the live-capable choices.
- **Gemini joins the live mailbox.** Gemini can now run a resident live job through the `agy` stream protocol alongside Claude.
- **Idle cap for live jobs.** `--idle-timeout N` automatically closes an untended live session and preserves the close reason and timeout in `meta.json`.

## What's new in v0.20.0

- **Foreman completion inbox.** Finished background dispatches write a private
  completion record, and the bundled Claude Code plugin delivers matching
  records into the foreman's next prompt. The output tail is injection-hardened:
  control characters and `U+2028`/`U+2029` are removed, and worker output is
  framed as indented data.
- **Installable Claude Code plugin.** `.claude-plugin/marketplace.json` uses a
  self-referencing source, and the published npm tarball includes `hooks/`,
  `skills/`, and `.claude-plugin/`.
- **Claude live mailbox.** Resident background Claude jobs can receive messages
  while they run and record `events.jsonl`. For operations, see
  [📬 Live mailbox](#-live-mailbox); other vendors explicitly fall back to
  single-shot mode with a `stderr` and `mode-notice.txt` notice, and
  `jobs.sh wait` ends with `done exit=N`.
- **Foreman session identity.** The `SessionStart` hook binds a Claude
  `session_id` to a PID plus start time, making PID reuse safe. Dispatch walks
  its ancestors to stamp `foreman_session` into `meta.json` and completion
  records; the inbox prefers a session match and falls back to `workdir` for
  legacy records, so two foremen in one repository do not take each other's
  notices.

## What's new in v0.15.0

- **Streaming Codex progress evidence** — `codex exec --json` now streams JSONL
  events into `out.txt.progress.log`, so a timeout still records its last known
  step. `out.txt` and the Jobs display remain unchanged.
- **Evidence-led timeout diagnostics** — timeouts now state that they do not
  identify the cause, list a three-step check, and clarify that an empty
  progress log does not prove Codex made no progress.
- **Direct rollout recovery path** — timeout output now prints the absolute path
  to the matching `rollout-*.jsonl`, found from the first progress event's
  `thread_id`, so the interrupted conversation history can be inspected.

## What's new in v0.14.0

- **Evidence-based routing recommendations** — `jobs recommend` and MCP
  `jobs_recommend` rank vendors from completed public job metadata, enforce a
  minimum sample gate, and never change routing automatically.
- **Opt-in live capability probes** — `doctor --probe V` and MCP
  `provider_probe` distinguish an installed CLI from a working provider call.
  Default doctor remains offline; probe reports never include response bodies.
- **Search, filter, and export Live Board history** — search and state filters
  cover the latest 50 jobs, while **Export visible** downloads only filtered
  public metadata.
- **Repeatable quality/cost benchmark** — `omnilane benchmark` ships a fixed
  workload suite, defaults to provider-free dry-run, and requires `--run` for
  actual calls. Cost estimates use only explicit `--cost-per-call` values.
- **Strict installation acceptance** — CI runs isolated
  `omnilane doctor --strict --json`, catching incomplete runtime wiring without
  contacting providers.

## What's new in v0.13.0

- **`long-context` is ordered on AA-LCR** — Artificial Analysis's long-context
  reasoning benchmark, which scores exactly this lane's work. Gemini 3.1 Pro
  leads both fallbacks there, so its first place is now positively justified
  rather than merely unrevisited.
- **The lane's old advice was backwards and is gone.** It used to send
  multi-hop synthesis to the Claude candidate on second-hand figures for a
  prior model generation; on first-party current-generation data Claude is the
  weakest of the three shipped candidates. The fallbacks swapped, so GPT-5.6
  Sol (high) now precedes Claude Opus 5 (high).
- **`release-audit --require-tag` flags tags with no GitHub release.** It warns
  rather than fails, skips when `gh` is absent or offline so the audit still
  runs in CI, and looks only at recent tags.
- **Scope note:** AA-LCR runs on 10k-100k-token documents, so it settles
  synthesis across long documents and nothing at a full 1M. GPT-5.6 Luna tops
  that table far more cheaply and was deliberately *not* promoted, because this
  lane exists for the 1M sweep the benchmark does not reach.

## What's new in v0.12.0

- **`hardest-coding` drops Sol from `max` to `xhigh`** — on AA's per-effort
  Coding Index, Sol at xhigh outscores Sol at max and every Claude tier while
  costing about a third less. Past xhigh, effort buys overthinking rather than
  accuracy on this workload.
- **`fast-agentic` leads with GPT-5.6 Luna**, Gemini 3.6 Flash second. Luna
  leads Flash on AA's Agentic Index by a wide margin and, after OpenAI's
  2026-07-30 reprice, costs a fraction as much per task. Flash keeps only a
  throughput edge — put it back in front locally if your loops are
  latency-bound.
- **Lane comments no longer carry numbers.** `routing.yaml` now states why each
  ordering holds; every score, price and throughput figure lives in
  `docs/model-capabilities-2026-09.md` with its retrieval date, so a stale
  figure never requires a routing-table edit.
- **A value profile** in `routing.local.yaml.example` trades about one
  Intelligence Index point for 30-40% off the cost per task.
- **New `--mode sysops`** — `work` without the vendor sandbox, for service
  operations the sandbox denies. It gives the worker full machine access, so it
  is a per-dispatch flag only and can never be a lane default.
- **Refreshed pricing and benchmark data** for the 2026-07-30 OpenAI reprice,
  and documented that AA's Coding Index is *not* the Coding Agent Index — they
  share no components and their numbers collide.

## What's new in v0.11.0

- **The Live Board reads in five languages** — English, Japanese, Korean,
  Traditional Chinese and Simplified Chinese. It follows the browser languages
  on first load, the switcher in the header overrides that, and the choice is
  remembered locally. Headings, the search placeholder, filter buttons, empty
  and error states, content markers and the `aria-label` attributes screen
  readers announce are all covered, and `<html lang>` follows the selection.
- **Job states are translated without breaking anything that reads them** — the
  `state-` CSS classes keep the raw value so status colours are unchanged, and
  the search index holds both spellings, so `running` and its translation match
  the same job.
- **No routing changes.** Dispatch behaviour is identical to v0.10.4.

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
  `docs/model-capabilities-2026-09.md` against the Artificial Analysis source
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
  see [`docs/model-capabilities-2026-09.md`](docs/model-capabilities-2026-09.md).
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
  [`docs/model-capabilities-2026-09.md`](docs/model-capabilities-2026-09.md).
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
