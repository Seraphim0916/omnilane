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
table (local overrides win). The backup is what dispatch uses when the first
choice's CLI is not installed. When a task fits two lanes, pick by what failure
costs: unknown root cause or correctness-critical → `hardest-coding`; a change you
could specify line by line → `bulk-mechanical`.

The table shows no scores, and you cannot tell from it whether a target is within
your ceiling. Ask: add `--dry-run` to the dispatch in Step 3. It prints the
decision and calls nothing. A refusal names the lanes you *can* reach (Step 4).

| Lane | First choice | Backup | Use for |
|---|---|---|---|
| hardest-coding | Claude Fable 5.1 (max) | GPT-6 Astra (xhigh) → Grok 4.6 → Gemini 3.8 Flash (High) | Hardest implementation, deep root-cause debug, correctness-critical edits |
| bulk-mechanical | GPT-5.6 Sol (high) | Gemini 3.8 Flash (High) → Claude Sonnet 5 (high) | Refactors, migrations, tests, review sweeps — mechanical endurance |
| triage | GPT-5.6 Luna (high) | Gemini 3.8 Flash (Low) → Claude Haiku 4.5 | High-volume scans, first-pass filtering |
| hard-judgment | Claude Fable 5.1 (xhigh) | GPT-6 Astra (xhigh) → Grok 4.6 | Architecture arbitration, deep reasoning, second opinions |
| taste-final | Claude Fable 5.1 (xhigh) | GPT-6 Astra (xhigh) → Grok 4.6 → Gemini 3.8 Flash (High) | User-facing prose, prompt/doc polish, Chinese phrasing, style arbitration |
| consult | GPT-6 Astra (xhigh) | Claude Fable 5.1 (xhigh) → Grok 4.6 → Gemini 3.8 Flash (Medium) | Direct named-model consultation; always keep `--vendor` |
| ui-draft | GPT-5.6 Sol (high) | Claude Fable 5.1 (xhigh) → Gemini 3.8 Flash (High) | UI drafts only WITH design system / reference images; open-ended taste goes taste-final |
| long-context | Gemini 3.8 Flash (Medium) | GPT-5.6 Terra (max) → Claude Opus 5 (medium) | Long-context synthesis; context size alone is not a quality result |
| fast-agentic | Gemini 3.8 Flash (Low) | GPT-5.6 Luna (high) → Claude Haiku 4.5 | Fast multi-step agentic loops and multimodal checks |
| live-search | Grok 4.6 | Gemini 3.8 Flash (High) → Claude Sonnet 5 (high) → off | Realtime X/web search; non-Grok fallbacks provide generic web search, not equivalent X context |
| coding-overflow | Grok 4.6 | Gemini 3.8 Flash (High) → Kimi K3 → Qwen3 Coder Plus → OpenCode → off | Explicit Codex-quota relief; no automatic cross-vendor retry after provider failure |
| arbitrate | off (opt-in vote panel) | — | Disabled by default. The operator enables it with `arbitrate: vote codex,claude,grok -` in routing.local.yaml (1-4 voters; a `2` in place of the final `-` adds a rebuttal round). One quota hit PER VOTER PER ROUND; you chair and own the decision |

Astra defaults to `xhigh` on the hard lanes; ask for more only explicitly with
`--vendor codex --effort max`. There is no automatic effort escalation, and a
higher effort never bypasses your ceiling.

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

`work` confines file and command changes to `--workdir` and disables the
worker's tool networking (not the model connection); `advise` is read-only with
the vendor's native web tools. Neither turns into `sysops` by itself. macOS Grok
`work` is blocked (its child-network isolation is Linux-only). Same-directory
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
| Claude Code | you call `Agent` with no `model` argument, the agent type's definition sets neither `model` (other than `inherit`) nor `effort`, and `CLAUDE_CODE_SUBAGENT_MODEL` is unset. The built-in general-purpose agent qualifies; a custom or plugin agent with its own frontmatter does not |
| Codex | you call `collaboration.spawn_agent` with no model and no effort |
| Grok Build | model inheritance is documented (`spawn_subagent`, bundled `general-purpose` is `model: inherit`, no `[subagents.models]` pin); effort inheritance is not verified |
| Antigravity | no sub-agent tool verified; do not assert it |

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
`model` and `harness` must equal the handoff's; a mismatch is rejected.
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
 "lane_requirement": {"lane": "hardest-coding", "target": "codex/gpt-6-astra-xhigh", "score": 54},
 "eligible_lanes": [{"lane": "bulk-mechanical", "target": "codex/gpt-5-6-sol-high", "score": 48, "transport_verified": true}]}
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
| `native-capability` · `native-inherit-unavailable` | your capability file does not allow the inherited worker; `reason` says why | fix the file (`omnilane native-context …`) or drop the CLI-only flag named in `reason` |
| `invalid-policy-input` "transport contract evidence changed" | the overlay will not load; nothing is wrong with you or your target | `omnilane doctor`, then the operator runs `omnilane resign` |
| `inherit-requires-model-caller` | a human has no runtime to inherit | dispatch a lane |

`omnilane resign --approve …` and `--record-signers` are operator actions. A
model never runs them. After two failed attempts at a task, reassess the scope;
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
- **Claude Code:** the completion inbox arrives with the *next* prompt; it does
  not wake an idle controller. With nothing else to do, stay on `omnilane jobs wait`.
- **Native sub-agents:** the host's own callback gives you the result; still
  ingest and verify it.

When the next step depends on the previous result, group the dispatches:
`omnilane goal open "<objective>" --workdir DIR`, then `goal dispatch <id> …`,
`goal note`, `goal status`, `goal close --summary`. A single obvious task is
dispatched directly.

`omnilane doctor` reports health without repairing anything (offline unless
`--probe V`). `omnilane benchmark` prints a no-call route plan. `omnilane ui
start|status|url|stop` runs a read-only board of jobs; it cannot dispatch.

## Notes for your main model

These never widen what you may execute yourself. With no matching row, use the
lane table; do not assume an older model is equivalent.

- **Claude Fable 5.1:** quality-first controller. Hardest coding at max, judgment
  and taste at xhigh. Send bulk work to Sol high, long/fast work to Gemini 3.8
  Flash, and use Astra as an independent review path.
- **Claude Opus 5:** balanced controller and independent reviewer when explicitly
  selected (`high`, or `xhigh` for deeper review); Claude's long-context fallback.
- **Claude Sonnet:** coordination, tools, mid-tier coding; fallback in
  bulk-mechanical and live-search. Never self-assign top judgment or hardest coding.
- **GPT Astra:** controller backup and independent reviewer. Resolve your real
  effort first; an explicit higher-effort request does not bypass the ceiling.
- **GPT Sol / Terra / Luna:** mechanical work, long context and triage
  respectively, only within your exact ceiling. Do not infer eligibility from the
  family name, and do not promote Luna's low price into correctness-critical work.
- **Grok 4.6:** live-search and coding-overflow are yours, plus fallback in hard
  lanes. Verify API signatures and cited facts before shipping.
- **Gemini 3.8 Flash:** long-context at medium, fast-agentic and triage at low,
  bulk/overflow/web fallbacks at high. Do not infer visual taste or controller
  authority from coding benchmarks.
- **Gemini 3.1 Pro:** directly selectable, not promoted; route hard coding and
  judgment to the stronger lanes.

## Reference

### Model aliases

| Alias | Vendor | Model | Effort |
|---|---|---|---|
| Opus | claude | claude-opus-5 | high |
| Fable 5.1 | claude | claude-fable-5-1 | xhigh |
| Sonnet | claude | claude-sonnet-5 | high |
| Haiku | claude | claude-haiku-4-5 | - |
| Sol | codex | gpt-5.6-sol | high |
| Terra | codex | gpt-5.6-terra | max |
| Luna | codex | gpt-5.6-luna | high |
| Astra | codex | gpt-6-astra | xhigh |
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
Grok and Agy are read from their launch flags (`--model`, `--effort`). Codex
outside app-server is read from `-m`/`--model` or `-c model=…` plus
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
