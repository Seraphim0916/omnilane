---
name: omnilane
description: 'Universal model-routing table + cross-vendor dispatch for ANY harness (Claude Code, Codex, Grok Build, Antigravity). Use when delegating subtasks, choosing a model for work, planning multi-part tasks, or when asked about model routing, delegate, dispatch, which model, tier selection, escalate, 派工, 模型路由. One routing table; every task dispatches by default via dispatch.sh — the main loop self-executes only reserved commander items.'
---

# omnilane — one routing table, every harness

You are the **commander**: the main loop of Claude Code, Codex, Grok Build or
Antigravity. omnilane gives you one table that says which model does which kind
of work, and one command that hands the work to it. Follow the steps in order.

**What you keep for yourself:** planning, writing task briefs, reading results,
acceptance, replies to the operator, `git commit`/`push`, governance edits.
**Everything else is dispatched**, even when the best model for it is your own.
A worker executes its one task and never dispatches again.

Commands below are written `omnilane …`. If that is not on `PATH`, use
`<omnilane-repo>/bin/omnilane …`; `omnilane route …` is the same as
`<omnilane-repo>/scripts/dispatch.sh …`. `/absolute/repo` below always means the
project the work is about, not omnilane's own directory.

**Run every omnilane command as the only command of its tool call.** No
`; echo $?`, `&&`, pipe or `F=$(…)`. Read output and exit code from the tool
result; when a later command needs a path that an earlier one printed, type the
path into it. (Codex cannot be identified through a wrapping shell; see Step 1.)

## Step 1 — Find out who you are

```sh
omnilane whoami
```

Success prints `omnilane: caller is <vendor>/<model>-<effort> (score N)` and the
path of a caller-context file. You do not pass that file on: every dispatch reads
the same identity by itself. N is your **ceiling**: you may dispatch only to a
target that scores at or below N. Nothing you say raises it. Codex means every
Codex surface here: CLI, desktop app and IDE (the latter two are "app-server").

| `whoami` says | Meaning | Do this |
|---|---|---|
| `caller is …` | identified | go to Step 2 |
| `degraded to its lowest-scored row … (ceiling N)` | Codex turn with no recorded effort (a heartbeat automation, or `codex` launched without `model_reasoning_effort`). You are held to your model's lowest score | carry on; low lanes work. A lane above the floor needs a session launched with an explicit effort, or the operator |
| `codex direct child CODEX_THREAD_ID is missing … (read from pid N, zsh)` | you wrapped the command. Codex starts `zsh -lc '<command>'`; one simple command becomes that process and is read, but `; echo $?`, `&&`, a pipe or `F=$(…)` leaves a shell in between | run the same omnilane command again as the only command of the tool call |
| `… rerun … outside the Codex sandbox` | process inspection, `~/.omnilane` writes and networking are blocked in the sandbox | request the unsandboxed run; do not retry inside it with a hand-made file |
| `no vendor CLI among this process's ancestors` or any other refusal | not readable | report the exact message to the operator. Only if the operator is unavailable: declare the **lowest**-scoring row of your model in a caller-context file (schema at the end) and say so in your report |

Never raise a declared effort to unblock a target. Never pass
`--operator-asserted-human` for yourself: it is the human operator's statement.

## Step 2 — Pick the lane

Split the work into subtasks; give each one lane. `omnilane list` shows the live
table (local overrides win). Each chain is ordered value-first on the measurement
that fits the lane's kind of work, and dispatch takes the first candidate that is
installed, proven on this host and within your ceiling; a chain steps down through
the score range so that whatever your ceiling, the first one you can reach is the
best-value one you can reach. When a task fits two lanes, pick by what failure
costs: unknown root cause or correctness-critical → `hardest-coding`; a change you
could specify line by line → `bulk-mechanical`.

The table shows no scores, and you cannot tell from it whether a target is within
your ceiling. Ask: add `--dry-run` to the dispatch in Step 3. It prints the
decision and calls nothing. A refusal names the lanes you *can* reach (Step 4).

| Lane | First choice | Backup | Use for |
|---|---|---|---|
| hardest-coding | GPT-6 Astra (xhigh) | GPT-6.1 Sol (xhigh) → Claude Opus 5.5 (medium) → GPT-6.1 Sol (high) → GPT-6 Astra (medium) → GPT-6.1 Sol (medium) → Claude Fable 5.1 (medium) → Claude Opus 5 (high) → Claude Sonnet 5.5 (high) → GPT-6 Astra (low) → Claude Opus 5 (medium) → Claude Opus 5.5 (low) → Claude Sonnet 5.5 (medium) → Claude Opus 5 (low) → Grok 4.7 → Grok 4.6 → Gemini 3.8 Flash (High) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| bulk-mechanical | GPT-6.1 Sol (medium) | Claude Opus 5.5 (medium) → GPT-6 Astra (medium) → GPT-6.1 Sol (high) → Claude Sonnet 5.5 (high) → GPT-6.1 Sol (low) → GPT-6 Astra (low) → GPT-6 Sol (high) → GPT-5.6 Sol (high) → Claude Opus 5.5 (low) → Claude Sonnet 5.5 (medium) → Claude Sonnet 5.5 (low) → GPT-6 Sol (medium) → Claude Opus 5 (low) → GPT-6 Sol (low) → Gemini 3.8 Flash (High) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| triage | GPT-6 Luna (high) | GPT-5.6 Luna (high) → Gemini 3.8 Flash (Low) → Claude Sonnet 5.5 (low) → Claude Sonnet 5 (low) → Claude Haiku 4.5 | High-volume scans, first-pass filtering |
| hard-judgment | Claude Opus 5.5 (xhigh) | Claude Fable 5.1 (xhigh) → Claude Opus 5.5 (medium) → Claude Fable 5.1 (medium) → Claude Opus 5 (high) → Claude Opus 5 (medium) → Claude Opus 5.5 (low) → GPT-6.1 Sol (xhigh) → GPT-6 Astra (high) → Grok 4.7 → Gemini 3.8 Flash (High) | Architecture arbitration, deep reasoning, second opinions |
| taste-final | Claude Opus 5.5 (xhigh) | Claude Sonnet 5.5 (xhigh) → Claude Opus 5.5 (high) → Claude Sonnet 5.5 (high) → Claude Opus 5.5 (medium) → Grok 4.7 → Grok 4.6 → GPT-6 Astra (xhigh) → Gemini 3.8 Flash (High) | User-facing prose, prompt/doc polish, Chinese phrasing, style arbitration |
| consult | GPT-6 Astra (xhigh) | Claude Opus 5.5 (xhigh) → Grok 4.7 → Grok 4.6 → Gemini 3.8 Flash (High) | Direct named-model consultation; always keep `--vendor` |
| ui-draft | Claude Opus 5.5 (high) | GPT-6.1 Sol (high) → Claude Opus 5.5 (medium) → GPT-6 Astra (medium) → GPT-6.1 Sol (medium) → GPT-6.1 Sol (low) → Claude Opus 5.5 (low) → GPT-6 Astra (low) → GPT-6 Sol (medium) → Gemini 3.8 Flash (High) | UI drafts only WITH design system / reference images; open-ended taste goes taste-final |
| long-context | Claude Opus 5 (high) | Claude Opus 5 (medium) → Claude Opus 5 (low) → GPT-5.6 Terra (xhigh) → Gemini 3.8 Flash (High) | Long-context synthesis; context size alone is not a quality result |
| fast-agentic | GPT-6.1 Sol (medium) | GPT-6 Astra (medium) → GPT-6 Sol (low) → GPT-6.1 Sol (low) → GPT-6 Astra (low) → Gemini 3.8 Flash (Medium) → Claude Sonnet 5.5 (medium) → Claude Opus 5.5 (low) | Fast multi-step agentic loops and multimodal checks |
| live-search | Grok 4.7 | Grok 4.6 → Gemini 3.8 Flash (High) → Claude Opus 5.5 (low) → off | Realtime X/web search; non-Grok fallbacks provide generic web search, not equivalent X context |
| coding-overflow | Grok 4.7 | Grok 4.6 → Gemini 3.8 Flash (High) → Kimi K3 → Qwen3 Coder Plus → OpenCode → off | Explicit Codex-quota relief; no automatic cross-vendor retry after provider failure |
| arbitrate | off (opt-in vote panel) | — | Disabled by default. The operator enables it with `arbitrate: vote codex,claude,grok -` in routing.local.yaml (1-4 voters; a `2` in place of the final `-` adds a rebuttal round). One quota hit PER VOTER PER ROUND; you chair and own the decision |

Chains are ordered value-first: for each caller ceiling, the cheapest row whose
quality on the lane's own measurement is close to the best that ceiling can reach.
"Close" is a near-tie in hardest-coding, hard-judgment, taste-final, long-context
and coding-overflow, and a wider band in the throughput lanes. No chain contains a
max row; ask for one only explicitly with `--vendor … --effort max`. There is no
automatic effort escalation, and a higher effort never bypasses your ceiling.

**When the operator names a model or asks a question:**

1. "Which model is good at X?" → answer from `omnilane list` (reading the table is your own work); do **not** dispatch.
2. A vendor name (`Claude`, `Codex`, `Grok`, `Gemini`, `OpenCode`) → `omnilane route --vendor <vendor> consult "<task>"`.
3. A model alias → pass its vendor, model and effort from the alias table at the end. Never substitute another family.
4. No named target → classify into a lane and dispatch.
5. Unknown or ambiguous nickname → ask; do not guess or run.

Never drop `--vendor` to get a fallback; a missing explicit target fails clearly.

## Step 3 — Dispatch

**Read-only work** (reviews, questions, scans, second opinions) — the default
`advise` mode, 600 s per CLI call:

```sh
omnilane route <lane> "<task brief>"
```

**Anything that edits files, runs tests, builds or deploys** — all three flags,
every time. Without them you get a read-only worker that produces nothing:

```sh
omnilane route --mode work --workdir /absolute/repo --timeout 3600 <lane> "<task brief>"
```

Without `--background` the command blocks and prints the worker's answer on
stdout. With it, stdout is just the job id; collect the answer in Step 5.
`--workdir` defaults to your current directory; a read-only worker can read it.
Put what the worker needs into the brief, or name the files by absolute path.

Every task brief states the goal, the files, what must not be touched, the
acceptance criteria and the **exact verification command**. "Done" without
evidence is not accepted. For example:

```text
Goal: find which of the 40 files under /srv/app/logs/2026-09-19/ contain "ECONNRESET upstream".
Do not modify, move or delete anything.
Report: one line per matching file with its match count, then the total.
Verify with: grep -c "ECONNRESET upstream" /srv/app/logs/2026-09-19/*.log
```

Useful flags:

| Flag | When |
|---|---|
| `--background` | long tasks; returns a job id at once |
| `--timeout N` | cap for each CLI call (raise to 1200+ for hard-judgment / long-context) |
| `--job-timeout N` | one fuse over the whole dispatch incl. lock wait, retries, voters; off by default, 7200–14400 for full-repo audits, expiry returns 124 |
| `--thread NAME` | later dispatches must keep earlier context; a thread pins vendor, model, effort and workdir (`omnilane jobs threads`, `threads show NAME`, `threads rm NAME`) |
| `--vendor V [--model M] [--effort E]` | an explicit target; still subject to your ceiling |
| `--live` | keep a Codex or Grok background job open for follow-up messages (Claude and Gemini background jobs already are) |
| `--caller-context FILE` | only when you were handed a context as a worker, or for the last-resort fallback in Step 1 |
| `--mode sysops` | unrestricted native policy for service/host operations; per dispatch only, never a default, and the brief must name the allowed operations |
| `--dry-run` | print the decision without calling anything |

**A `work` worker cannot commit in a linked git worktree, and cannot run every
test.** Its sandbox writes only inside `--workdir`. A directory made by
`git worktree add` keeps its index under the main repository's `.git`, outside
that sandbox, so `git add` and `git commit` fail there with `index.lock:
Operation not permitted`; the sandbox also denies `ps` and local servers, so
tests that need them fail for the worker and pass for you. In such a brief say:
do not commit, run the new tests and the directly related test files, and
report exactly which ran. You then run the full suite outside the sandbox and
make the commit yourself. Do not widen the sandbox to the shared `.git`.

`work` confines file and command changes to `--workdir` and disables the
worker's tool networking (not the model connection); `advise` is read-only with
the vendor's native web tools. Neither turns into `sysops` by itself. macOS Grok
`work` is blocked by default (its child-network isolation is Linux-only). The
operator may enable single-shot work **without isolation** using
`option.grok-macos-work: unconfined` in local routing only. When off, macOS work
chains skip Grok; explicit selection still refuses. When on, `doctor --strict`
fails. **Commander: when this host has the option on, every brief sent to Grok
must follow the ordered [worker-brief template](../../docs/grok-unconfined-work.md#required-task-brief-template).**
Same-directory
Codex dispatches are serialized by a lock; do not parallelize them yourself.

### When the target is your own harness, use your own sub-agent tool

If your harness has a sub-agent tool, prefer it over an external CLI for work
your own vendor's model will do: no second login, no overlay, no process to
supervise. It is a preference, not a rule. The plain `omnilane route …` of the
previous section always remains correct, and when it sends your own vendor's
model out through the CLI, dispatch prints a notice on stderr (not a refusal)
with the command that would have kept it inside. Two ways to stay inside:

**A. A worker on your own runtime — `--inherit`.** Your sub-agent, spawned with
**no model and no effort argument**, runs what you run, so it scores what you
score and can never be an upward dispatch. It needs no vendor CLI and no
transport overlay, and it works when your effort is unrecorded. Use it for
diagnosis, evidence gathering and work your own model is good enough for.

```sh
omnilane native-context --workdir /absolute/repo --inherits-caller-runtime
omnilane route --inherit --native-context /path/printed/above --workdir /absolute/repo <lane> "<task brief>"
```

Pass `--inherits-caller-runtime` only if it is true of your tool:

| Harness | True when |
|---|---|
| Claude Code | you call `Agent` with no `model` argument, the agent type's definition sets neither `model` (other than `inherit`) nor `effort`, and `CLAUDE_CODE_SUBAGENT_MODEL` is unset. The built-in general-purpose agent qualifies; a custom or plugin agent with its own frontmatter does not. Such a worker follows the session's current effort, including a change made mid-session (observed: the session switched to max and an inherited worker ran max on all 66 of its records) |
| Codex | you call `collaboration.spawn_agent` with no model and no effort |
| Grok Build | you call `spawn_subagent` with no `model` argument and `[subagents.models]` pins nothing for the agent type (the bundled `general-purpose` is `model: inherit`). Model and effort inheritance observed on grok 1.0.41: parent grok-4.7/high, child grok-4.7/high. Harness name `grok-build` |
| Hermes | `delegation.model` is not configured: `delegate_task` takes no model argument, and the child then runs the parent's model. omnilane cannot read a Hermes caller; state it as described below |
| OpenClaw | you call `sessions_spawn` with no `model` or `thinking` argument, and no `agents.defaults.subagents.model` or `.thinking` pin (or per-agent pin) differs from your own. omnilane cannot read an OpenClaw caller; state it as described below. No cycle has been run |
| Antigravity | agy 1.2.12 exposes only a session-level `--agent`, no sub-agent tool; do not assert it |

Here the lane is only a label for what kind of work it is; it is not checked
against your ceiling, because no lane target runs. The handoff says
`effort: "inherited"`, `model_override: false` and
**`satisfies_lane_target: false`**, and you report it that way: "my own sub-agent
did this", never "hardest-coding did this". So it is not a way around a lane that
needs a stronger model than you: work that needs that model still needs that
model, and that dispatch is still refused.

`--inherit` accepts `--mode work --workdir DIR` and `--timeout N` (the deadline
you enforce on the sub-agent; default 600). It takes no
`--vendor`/`--model`/`--effort`, has no CLI fallback, and refuses `--background`,
`--live`, `--thread`, `--mode sysops`, `--job-timeout` and `--idle-timeout`.

If `whoami` cannot read you even when run alone, `--inherit` still works on your
own statement — add `--vendor <yours> --model <the model you really run>` to
`native-context`. The handoff then says `caller_identity_verified: false` and
carries no ceiling; completion is checked against what you stated. Lane dispatch
stays refused while you are unread; report that instead of guessing an identity.

Hermes and OpenClaw callers are always in this case. From Hermes:

```sh
omnilane native-context --vendor V --model M --harness hermes --inherits-caller-runtime --workdir /absolute/repo
omnilane route --inherit --native-context /path/printed/above --workdir /absolute/repo <lane> "<task brief>"
```

If a vendor CLI is an ancestor of the Hermes process (Hermes started from a
Claude Code shell, for example), set `OMNILANE_AA_CALLER_FROM_PROCESS=0`;
otherwise omnilane reads the outer CLI as the caller and `native-context` refuses
the contradiction.

**B. A specific model your sub-agent tool can select.** Describe your tool's real
contract in a capability file (start from `omnilane native-context`, add rows for
the exact model/effort pairs your tool accepts) and pass it:

```sh
omnilane route --native-context /absolute/capability.json --workdir /absolute/repo <lane> "<task brief>"
```

A row matches only on the exact model, effort, mode, workdir, tools, isolation
(`shared-inherited`: a native sub-agent shares your tools and filesystem, there
is no OS sandbox) and lifecycle (`single-shot`). Same vendor is not same model;
nothing is inferred from installed CLIs. `--executor native` fails instead of
falling back; `--executor cli` forces the external CLI. With a Codex model
override use `fork_turns: "none"` or a bounded count, never `"all"`.

Instead of adding those rows to every file by hand, a host declares its extra
pairs once in `$OMNILANE_HOME/native-rows.json`:

```json
{"schema_version": 1, "harnesses": {"claude-code": [
  {"model": "claude-opus-5-5", "efforts": ["medium", "high", "xhigh"]},
  {"model": "claude-opus-5", "efforts": ["high"]}]}}
```

`omnilane native-context` then appends one row per declared model for your
harness, with the workdirs and modes you asked for. `--host-rows FILE` reads
another file; `--no-host-rows` skips it. Every pair must be a scored
configuration for your vendor, or nothing is written (exit 2). A host-asserted
caller (the statement in A) skips the file; it serves `--inherit` only.

In Claude Code each such row is one agent definition per model and effort, named
`omnilane-<model>-<effort>` (e.g. `omnilane-claude-opus-5-5-medium`), with
frontmatter `model: <exact id>`, `effort: <effort>` and `disallowedTools: Agent`,
plus a PreToolUse Bash hook in the definition that refuses
`omnilane route|goal|native-context` and running `dispatch.sh`. Call `Agent` with
`subagent_type` set to that name and no `model` argument. These definitions set
`model` and `effort`, so they serve B, never `--inherits-caller-runtime`. A
definition added during a turn is not callable in that turn ("Agent type … not
found"); it loads at the next turn. Every Claude Code sub-agent loads the full
CLAUDE.md hierarchy, so even a one-word reply costs noticeably.

**Both A and B print a PENDING handoff as one JSON object on stdout, not a
result.** Its `job_id`, `task`, `workdir`, `mode`, `timeout` and
`worker_contract` are what you need. You then:

1. Call your own sub-agent tool with the handoff's task, workdir, mode and
   deadline, and tell it: no nested delegation.
2. Verify its result yourself.
3. Write a completion file and ingest it:

```json
{"schema_version": 1, "job_id": "<from the handoff>", "agent_id": "<the real agent id>",
 "runtime": {"vendor": "<v>", "model": "<exact model>", "effort": "<observed, or \"unknown\">",
             "harness": "<as in the handoff>", "backend": "<your agent tool's name>"},
 "outcome": "success", "result": "<public summary, not raw logs>",
 "evidence": ["<command/result or artifact reference>"]}
```

```sh
omnilane jobs --json complete-native JOB_ID /absolute/completion.json
omnilane jobs --json status JOB_ID
```

`outcome` is `"success"` or `"failure"`; report a failure as a failure. `vendor`,
`model` and `harness` must equal the handoff's; a mismatch is rejected. In Claude
Code, read `runtime.model` and `runtime.effort` from the sub-agent transcript
`~/.claude/projects/<project>/<sessionId>/subagents/agent-<id>.jsonl`: every
assistant record carries `message.model` and `effort`, and `agent-<id>.meta.json`
beside it records `agentType`.
Never report completion before ingestion. Background, live, named-thread,
multi-round, vote, `sysops` and hard-isolation work stays on the CLI path. Reuse
of an existing agent, cancellation and the strict schemas are in
[docs/native-executor.md](../../docs/native-executor.md).

## Step 4 — If dispatch refuses

A refusal is one JSON line on stderr and no job exists. Read `failed_gate`,
`reason`, `next_command` and `eligible_lanes`, and act on them instead of guessing:

```json
{"allowed": false, "code": "target-above-effective-ceiling", "failed_gate": "downward-ceiling",
 "required_caller_effort": "xhigh", "next_command": "omnilane list",
 "lane_requirement": {"lane": "hardest-coding", "target": "codex/gpt-6-astra-xhigh", "score": 52},
 "eligible_lanes": [{"lane": "bulk-mechanical", "target": "codex/gpt-5-6-sol-high", "score": 42, "transport_verified": true}]}
```

`eligible_lanes` is every lane you can reach right now. Moving to one of them is
right only when that lane also fits the work. If the work needs the refused
lane's quality, do not downgrade it: report the refusal, `required_caller_effort`
and the eligible lanes to the operator and wait. The same holds when your
operator's rules say a reroute needs their approval.

| `failed_gate` / `code` | Meaning | Do this |
|---|---|---|
| `caller-identity` · `missing-caller-context` | nobody could tell who is asking | run `omnilane whoami` alone and apply Step 1 |
| `caller-identity` · `invalid-degraded-caller` | a context file is marked `effort_unverified` but is not its model's lowest row | use the file `whoami` writes; do not edit it |
| `downward-ceiling` · `target-above-effective-ceiling` | the target scores above you | a fitting lane from `eligible_lanes`, or report `required_caller_effort` to the operator |
| `target-transport` · `runtime-mapping-unverified` / `unknown-target-runtime` | you are identified; this host has not (or no longer) proven that target. Usually a vendor CLI updated itself | tell the operator to run `omnilane resign`; meanwhile a fitting lane from `eligible_lanes` whose `transport_verified` is true |
| `lane-disabled` · `lane-disabled` | the lane's whole chain is `off` (for example `arbitrate` by default); nothing is wrong with you or the transport | pick another lane that fits, or tell the operator the lane has to be enabled in `routing.local.yaml`; `omnilane resign` does not help |
| `native-capability` · `native-inherit-unavailable` | your capability file does not allow the inherited worker; `reason` says why | fix the file (`omnilane native-context …`) or drop the CLI-only flag named in `reason` |
| `invalid-policy-input` "transport contract evidence changed" | the overlay will not load; nothing is wrong with you or your target | `omnilane doctor`, then the operator runs `omnilane resign` |
| `inherit-requires-model-caller` | a human has no runtime to inherit | dispatch a lane |

`omnilane resign --approve …`, `--record-signers` and `--trust-adhoc …` are
operator actions. A model never runs them. After two failed attempts at a task, reassess the scope;
an upward move needs the human. On vendor quota exhaustion (429, "stream
disconnected", usage limit) send mid-tier coding through `coding-overflow`;
never silently downgrade `hardest-coding` — wait or escalate.

## Step 5 — Collect, verify, close

A job id, a PENDING handoff, exit 0 or "scheduled" is **not** completion.

```sh
omnilane jobs wait JOB_ID       # block until it ends
omnilane jobs status JOB_ID
omnilane jobs result JOB_ID     # the worker's public result
```

Read the result, run the verification command from your brief (or send a second
worker to), and only then report. Other job commands: `cancel JOB_ID`;
`send JOB_ID "<text>"` and `close JOB_ID` for a live session (Claude and Gemini
background jobs stay resident; Codex and Grok are single-shot unless `--live`,
and Grok live needs `--mode sysops`); `retry JOB_ID --caller-context FILE` (keeps
the original target and never inherits an earlier human exemption);
`recommend`, `stats`, `audit` (read-only, never change routing);
`prune --keep N [--apply]`.

How you hear about a background job finishing:

- **Codex:** before ending a turn with unobserved jobs, register an active
  callback: `scripts/completion-wakeup.py prepare` with the real controller
  thread, host, a unique run id and the exact job list, then the app's
  `automation_update` heartbeat tool, then record the receipt. On callback:
  `poll`, acknowledge, verify, acknowledge acceptance, and close the automation
  when every tracked job is handled. Details: `docs/completion-wakeup.md`.
- **Claude Code:** after `omnilane route --background`, run
  `omnilane jobs wait JOB_ID --timeout N` with the Bash tool's `run_in_background`.
  When it exits, the controller is woken: an idle one starts a new turn in the
  second the job ended, a busy one gets it right after its current turn (verified
  on Claude Code desktop 2.1.284; the terminal CLI was not tested). `jobs wait`
  gives up after 600 s by default (exit 124; the job keeps running), so make N at
  least the job's own timeout. Without such a waiter the completion inbox arrives
  only with the next prompt.
- **Native sub-agents:** the host's own callback gives you the result; still
  ingest and verify it.

When the next step depends on the previous result, group the dispatches:
`omnilane goal open "<objective>" --workdir DIR`, then `goal dispatch <id> …`,
`goal note`, `goal status`, `goal close --summary`. A single obvious task is
dispatched directly.

If `goal dispatch` fails or its reply is lost, inspect `goal status` and the
associated job before submitting again. Journal reconciliation never launches
work; a new invocation, even with identical task text, is a new submission.
Unresolved reservations block further dispatch and closing. Do not delete or
rewrite ambiguous intent/claim records to force progress, and do not infer a
worker started from its allocated job directory or claim alone.

`omnilane doctor` reports health without repairing anything (offline unless
`--probe V`). `omnilane benchmark` prints a no-call route plan. `omnilane ui
start|status|url|stop` runs a read-only board of jobs; it cannot dispatch.

## Notes for your main model

These never widen what you may execute yourself. With no matching row, use the
lane table; do not assume an older model is equivalent.

- **Claude Opus 5.5:** leads hard-judgment and taste-final (xhigh) and ui-draft
  (high); its medium row stands behind GPT-6.1 Sol in hardest-coding and
  bulk-mechanical until a host proves Sol, and it is the Claude row in consult
  (xhigh) and the Claude fallback in live-search (low). Running at medium your
  ceiling is 51: you reach GPT-6.1 Sol xhigh or Opus 5.5 medium in the hard lanes,
  not its xhigh or high rows or Astra xhigh. Send long documents to Opus 5.
- **Claude Sonnet 5.5:** the second rung of taste-final (xhigh, then high), mid rungs
  of hardest-coding and bulk-mechanical (high, medium), the quick Claude row in
  fast-agentic (medium) and the Claude row in triage (low). It has no published
  visual or long-context result, and its knowledge score is low: keep it out of
  ui-draft, long-context and live-search. A host has to prove it with `omnilane
  resign` first; until then each chain serves the Opus row right behind it.
- **Claude Fable 5.1:** its xhigh row is second in hard-judgment, where it is ahead of
  Opus 5.5 high by more than a near-tie; its medium row is a mid rung of
  hardest-coding and hard-judgment. Everywhere else a cheaper row is as good.
- **Claude Opus 5:** leads long-context (high, down to low) and supplies mid rungs
  of hardest-coding and hard-judgment; an independent reviewer when explicitly
  selected.
- **Claude Sonnet 5:** coordination and tools; the triage fallback behind Sonnet 5.5
  at low. Never self-assign judgment, coding or search: its effort rows score low
  there.
- **GPT Astra:** leads hardest-coding (xhigh) and consult (xhigh); its medium and low
  rows are rungs of the coding, bulk and UI lanes, and Astra high stands behind
  GPT-6.1 Sol xhigh as Codex's row in hard-judgment. Resolve your real effort first;
  an explicit higher effort does not bypass the ceiling.
- **GPT-6.1 Sol:** the best-value Codex row below Astra xhigh: leads bulk-mechanical
  and fast-agentic (medium), is the second rung of hardest-coding (xhigh, then high
  and medium) and ui-draft (high, then medium and low), and Codex's row in
  hard-judgment (xhigh). A host has to prove it with `omnilane resign` first; until
  then each chain serves the Opus, Astra or GPT-6 Sol row right behind it.
- **GPT-6 Sol / Luna:** Sol low is the fastest rung of fast-agentic and the cheapest
  of bulk-mechanical; Sol high and medium now stand behind GPT-6.1 Sol. Luna high
  leads triage. Do not promote Luna's low price into correctness-critical work.
- **GPT-5.6 Sol / Terra / Luna:** fallbacks behind their GPT-6 successors; Terra
  xhigh is the Codex row in long-context.
- **Grok 4.7 / 4.6:** live-search and coding-overflow are yours, plus the third
  family in the hard lanes. 4.7 leads everywhere and 4.6 sits behind it for a
  host that has not proven 4.7. Verify API signatures and cited facts before shipping.
- **Gemini 3.8 Flash:** the cross-vendor row: fast-agentic at medium, triage at
  low, the tail of every other lane at high. Do not infer visual taste or
  controller authority from coding benchmarks.
- **Gemini 3.1 Pro:** directly selectable, not promoted; route hard coding and
  judgment to the stronger lanes.

## Reference

### Model aliases

| Alias | Vendor | Model | Effort |
|---|---|---|---|
| Opus | claude | claude-opus-5 | high |
| Opus 5.5 | claude | claude-opus-5-5 | xhigh |
| Fable 5.1 | claude | claude-fable-5-1 | xhigh |
| Sonnet | claude | claude-sonnet-5 | high |
| Sonnet 5.5 | claude | claude-sonnet-5-5 | high |
| Haiku | claude | claude-haiku-4-5 | - |
| Sol | codex | gpt-5.6-sol | high |
| Terra | codex | gpt-5.6-terra | max |
| Luna | codex | gpt-5.6-luna | high |
| Astra | codex | gpt-6-astra | xhigh |
| GPT-6.1 Sol | codex | gpt-6.1-sol | xhigh |
| GPT-6 Sol | codex | gpt-6-sol | xhigh |
| GPT-6 Luna | codex | gpt-6-luna | high |
| Grok 4.7 | grok | grok-4.7 | - |
| Grok 4.6 | grok | grok-4.6 | - |
| Gemini 3.1 Pro | gemini | Gemini 3.1 Pro (High) | - |
| Gemini 3.8 Flash High | gemini | gemini-3.8-flash-high | - |
| Gemini 3.8 Flash Medium | gemini | gemini-3.8-flash-medium | - |
| Gemini 3.8 Flash Low | gemini | gemini-3.8-flash-low | - |
| Gemini 3.7 Flash | gemini | gemini-3.7-flash-high | - |
| Kimi | kimi | kimi-k3 | - |
| Qwen | qwen | qwen3-coder-plus | - |
| OpenCode | opencode | provider/model form, or `-` for its own default | - |
| OpenRouter | openrouter | explicit OpenRouter slug (e.g. anthropic/claude-sonnet-5) | - |

OpenCode is a multi-provider CLI, work-capable, last resort in coding-overflow.
OpenRouter is direct API (`OPENROUTER_API_KEY`), **advise/consult only**, and its
slug is mandatory: `omnilane route --vendor openrouter --model <slug> consult "<task>"`.

Examples: "Ask Opus to challenge this architecture" →
`omnilane route --vendor claude --model claude-opus-5 --effort high consult "challenge this architecture"`.
「請 Grok 查最新公開資訊」→ `omnilane route --vendor grok consult "查最新公開資訊"`.
「哪個模型適合檢查大型 repo？」→ answer only.

### How your identity is read

Dispatch finds the nearest vendor CLI among your process's ancestors; nearest
wins, so a Codex worker started by a Claude session is a Codex caller. Claude,
Grok and Agy are read from their launch flags (`--model`, `--effort`). A Claude
caller is then read from its current turn, because the desktop app changes model
and effort without relaunching: omnilane binds the nearest `claude` to its
session through `sessions/<pid>.json` in `$CLAUDE_CONFIG_DIR` or `~/.claude` (pid
and process start time must match), requires `CLAUDE_CODE_SESSION_ID`, when
present, to name the same session, finds the unique
`projects/*/<sessionId>.jsonl`, and takes the model and effort of its latest
main-thread assistant record (the lower of `perTurnEffort` and `effort`; a
trailing `-YYYYMMDD` on the model id is dropped). Where they differ from the
launch flags they win, and `whoami` says `transcript <id8>: <model> at <effort>
(launch flags said …)`. Any missing or mismatched piece leaves the launch-flag
reading; only identity metadata is read, never message content, and
`OMNILANE_AA_CLAUDE_TRANSCRIPT=0` turns this off. Codex outside app-server is
read from `-m`/`--model` or `-c model=…` plus
`model_reasoning_effort`; a profile is not a selector. Codex app-server ignores
launch flags and reads the **current turn** instead: `CODEX_THREAD_ID` must match
between your process and codex's direct child, the rollout
`$CODEX_HOME/sessions/**/rollout-*-<thread>*.jsonl` most recently written must
carry that thread id, and its last `turn_context` must name a model and a turn
that has not ended. Missing, ambiguous or stale evidence refuses; there is no
config-default or other-thread fallback, and only identity metadata is read,
never message content. An explicit `--caller-context`, an identity inherited as a
worker, and the operator's `--operator-asserted-human` all take precedence over
this process reader; `OMNILANE_AA_CALLER_FROM_PROCESS=0` turns it off. This is host
request-selector evidence, not proof of the upstream provider's identity.

### The caller-context file

Only for the fallback in Step 1, or when you are handed one as a child.
`caller` must reproduce one `scored_configs` row of `config/aa-model-policy.json`
exactly, and `snapshot_id` must equal that registry's `snapshot.id`:

```json
{"schema_version": 1, "snapshot_id": "<registry snapshot.id>", "kind": "model",
 "caller": {"vendor": "claude", "model": "claude-opus-5", "effort": "high",
            "reasoning": "adaptive", "fallback": null},
 "inherited_ceiling": 52}
```

`inherited_ceiling` is your own row's score when you are the root caller, or the
ceiling you were handed when you are a child. Your effective ceiling is the lower
of the two. Pass it as `--caller-context /absolute/context.json`. Unknown
identities fail closed; no family name, displayed grade, retry or fallback grants
an uplift. The registry is frozen: its sha256 is pinned in
`scripts/lib/aa_policy.py`, so editing it fails the whole gate.

### What a job records

A CLI job stores the original authorizer, the narrowed child identity, the
decision and the registry snapshot; the provider process receives the child
identity, not yours. A retry keeps the original target and intersects your
current ceiling with the original one. A native handoff carries the same decision
and `worker_contract.caller_context_path`; give that context to the sub-agent
with the no-nested-dispatch rule. `OMNILANE_DEPTH` is an independent guard:
workers that try to dispatch are stopped.

For Gemini, the model id encodes the effort (`gemini-3.8-flash-high`); a matching
or absent `--effort` is accepted, a conflicting one is rejected.

### For the operator, not the model

Why the transport overlay goes stale, how `omnilane resign` re-signs it, evidence
tiers and hand re-signing: [docs/transport-overlay.md](../../docs/transport-overlay.md).
First install and the daily re-sign job: the README.
