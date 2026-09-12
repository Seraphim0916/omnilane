---
name: omnilane
description: 'Universal model-routing table + cross-vendor dispatch for ANY harness (Claude Code, Codex, Grok Build, Antigravity). Use when delegating subtasks, choosing a model for work, planning multi-part tasks, or when asked about model routing, delegate, dispatch, which model, tier selection, escalate, 派工, 模型路由. One routing table; every task dispatches by default via dispatch.sh — the main loop self-executes only reserved commander items.'
---

# omnilane — one routing table, every harness

You (the main loop) may be Claude, GPT, Grok, or Gemini. The procedure is identical:

1. **Identify the main model from current runtime metadata.** If the identity is unavailable, report it as unverified instead of guessing from a skill name or prior session.
2. **Split the work into subtasks and classify each into a lane** (table below).
3. **Delegate every task by default, even to the commander's exact model.**
   Native agents count as delegation; a model match is not permission for
   commander self-execution. Resolve vendor/model/effort separately from
   executor selection. Terminal `auto` without capabilities stays legacy CLI.
   The commander owns planning, task briefs, handoff/completion orchestration,
   reading public results, acceptance, operator replies, git commit/push and
   governance edits. Workers execute the assigned task and never delegate again.
   Read-only work uses advise; edits require `--mode work --workdir <repo>`.
   `<repo>/scripts/dispatch.sh --caller-context FILE [--executor auto|native|cli] [--native-context FILE] [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"`

   A model caller needs a verifiable caller identity. Codex app-server reads the
   current turn from its rollout; other launches read the model and effort from
   the CLI that launched you, so an ordinary session passes nothing. When that
   identity cannot be read, dispatch is refused with `missing-caller-context`
   before a job exists: run `omnilane whoami` (or `<repo>/bin/omnilane whoami`)
   and pass the file it prints as `--caller-context`. See **Frozen exact-AA
   downward gate** for the schema and what to do when effort is genuinely
   unverifiable.

   Add `--background` for long tasks; poll with `scripts/jobs.sh status|result <id>`.
   Use `--thread NAME` when later claude, codex, grok or gemini dispatches
   must retain earlier context. Threads in 0.33.0 pin vendor, model, effort and
   physical workdir; inspect or remove local state with `scripts/jobs.sh threads`,
   `threads show NAME`, and `threads rm NAME` (removal leaves the vendor session).
   Implementation dispatches (code edits, new files, tests, builds, deploys)
   must carry `--mode work --workdir <repo>` and a `--timeout` of at least
   3600 seconds. The advise default is a read-only worker under a 600 s
   per-call watchdog, and on an implementation task it yields zero output.
   Advise stays the default for reviews, questions, and second opinions.
   Before changing lane order from anecdotal outcomes, run
   `scripts/jobs.sh recommend [--last N] [--lane L] [--min-samples N]` and report
   its evidence threshold. The command is read-only and never changes routing.
   Preview old completed-job cleanup with `scripts/jobs.sh prune --keep <N>`;
   deletion requires the explicit `--apply` flag and never targets running jobs.
   A deep task whose CLI call may outrun the 600s per-call watchdog can raise its
   cap with `--timeout <seconds>` (e.g. `--timeout 1200` for hard-judgment /
   long-context). It bounds each CLI call, not the whole dispatch.
   For one aggregate fuse across lock wait, retries, voters, and rounds, add
   `--job-timeout <seconds>`. It is disabled by default; deep full-repository
   audits typically need 7200–14400 seconds, and expiry returns 124. The one
   automatic exception is non-Git Codex `work`: without an explicit, lane, or
   global job timeout, its resolved per-call timeout becomes the whole-job fuse,
   capped at the supervisor's 999999999-second maximum. If the bundled Perl
   supervisor is unavailable, it warns and continues through the existing
   per-call watchdog path.

Run `scripts/dispatch.sh --list` to see the effective table (local overrides win).
When routing is unexpectedly unavailable, run `bin/omnilane doctor` before
changing configuration; it reports state and dependencies without repairing them.
Doctor remains offline unless the operator explicitly adds `--probe V`; that
bounded probe returns metadata only. Use `bin/omnilane benchmark` for a fixed
no-call route plan, and add `--run` only when actual advise-mode comparison calls
were explicitly requested. Neither command changes routing.
Without native context, fallback chains use the first vendor CLI installed,
so the same table works with any subset of subscriptions.

## Caller-owned native delegation

Use explicit current-harness capabilities from the real agent-tool contract:
active harness/vendor, exact supported model/effort combinations, optional known
current model, task modes/workdirs, tools, isolation, and lifecycle. Codex
`collaboration.spawn_agent` has no sandbox/tool/workdir restriction parameters;
it inherits the parent's tools and filesystem. Advertise `shared-inherited` in
both request and matching capability row, with empty tool arrays. Treat
`advise`/`work` and workdir as task intent, not an OS sandbox. Hard isolation is
CLI-only. Same vendor is not same model. Unknown capabilities do not match.
Never inspect credentials or infer support from installed CLIs. Explicit
vendor/model/effort survive native fallback; no next-vendor substitution.

```sh
omnilane route --executor native --native-context /absolute/capability.json --workdir /absolute/repo hardest-coding "Review the change"
omnilane jobs --json complete-native JOB_ID /absolute/completion.json
omnilane jobs --json status JOB_ID
omnilane jobs --json result JOB_ID
```

For explicit reuse, the capability must prove the exact existing agent and its
idle state, and explicitly preserve its existing context. Recheck idle immediately
before `collaboration.followup_task`; completion must match the reuse strategy,
agent ID and backend. Do not substitute an unknown inherited model or reuse a busy
agent. New-agent capacity exhaustion is not success; see `docs/native-executor.md`.

Route returns **pending handoff JSON**, not a successful agent run. The host calls
its own agent tool with the resolved exact model and effort, passes workdir,
mode, task, and deadline as intent, then ingests the actual agent ID, runtime
vendor/model/effort/harness/backend, outcome, public result, and evidence. An
explicit model override uses `fork_turns: "none"` or bounded positive history;
never combine a model override with `fork_turns: "all"`. An unknown caller
current model may be omitted when the route explicitly selects an exact model
declared by the matching capability row. Never report completion before
ingestion. Native is not a shell executable. The host
passes **no nested delegation** to native workers; shell workers retain their
depth guard. Workers do not create handoffs or call agent-spawn tools.

Forced CLI retains external workers. Auto explains its CLI fallback reason;
forced native rejects missing/incompatible capability. CLI sessions (background,
live, named threads, explicit single-shot), durable/multi-round work,
vote/arbitration, sysops and unsupported isolation remain CLI-only. The native
deadline is host-enforced, not a shell watchdog. Native cancellation changes
pending state without PID signals; the host separately stops any spawned agent.
Native goal-loop, retry, mailbox, CLI wait and managed-block sync are not included.

See [native protocol](../../docs/native-executor.md) for strict schemas, terminal
examples, lifecycle and public-data boundaries. The parent alone backs up and
syncs the host's managed `~/.codex/AGENTS.md` block after review.

## Lanes (defaults; see routing.yaml for the live values)

Each lane's **backup** is the next candidate in its `routing.yaml` chain —
what dispatch picks when the first-choice vendor CLI is not installed.

| Lane | First choice | Backup | When |
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
| arbitrate | off (opt-in vote panel) | — | Disabled by default. Enable with `arbitrate: vote codex,claude,grok -` in routing.local.yaml or via the configurator (any 1-4 voters). One quota hit PER VOTER PER ROUND; you chair: read the opinions and own the decision. Effort field 2 = debate round (voters rebut each other) |

Claude Fable 5.1 leads the current hardest-coding, hard-judgment, and
 taste-final defaults at task-specific max/xhigh efforts. GPT-6 Astra is the
 Codex-family fallback and independent-review path. Fable max is the quality-first prompt-level controller. Opus high/xhigh is
 the balanced controller/independent-review option; Astra is the existing-
 Codex-quota backup/reviewer. These are role recommendations, not a lane or
 automatic selector. Opus remains explicitly selectable and in long-context
 fallback.

Astra defaults to `xhigh` on the high-difficulty lanes. For an explicitly needed
upgrade, use `--vendor codex --effort max`; no automatic risk classification or
failure-triggered effort escalation is added. AA API task costs do not prove
subscription-quota savings.

## Natural-language consultation

Users may speak normally; they do not need lane names.

1. Capability-only question (`which model`, `what can Claude do`, `哪個模型`,
   `誰適合`) → classify the need, then answer with the first available model
   shown for that lane by `dispatch.sh --list`; do not dispatch unless execution
   is also requested.
2. Generic vendor name (`Claude`, `Codex`, `Grok`, `Gemini`, `OpenCode`) → run
   `dispatch.sh --vendor <vendor> consult "<task>"`.
3. Canonical model alias → pass its vendor, model, and effort from the table
   below. Never silently substitute another model family.
4. No named target → classify into an existing lane and dispatch normally.
5. Unknown or ambiguous nickname → ask for clarification; do not guess or run.

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

OpenCode is the multi-provider aggregator CLI (75+ providers): work-capable,
last resort in coding-overflow. OpenRouter is direct-API — no CLI needed, only
`OPENROUTER_API_KEY` — and is **advise/consult only** (it cannot edit files);
its model slug is mandatory. "Ask <any hosted model> via OpenRouter" →
`dispatch.sh --vendor openrouter --model <slug> consult "<task>"`.

Examples:

- Ask Opus to challenge this architecture →
  `dispatch.sh --vendor claude --model claude-opus-5 --effort high consult "challenge this architecture"`
- 請 Grok 查最新公開資訊 →
  `dispatch.sh --vendor grok consult "查最新公開資訊"`
- 哪個模型適合檢查大型 repo？ → answer only; do not dispatch.

Consultation defaults to `advise`. Use `--mode work --workdir <dir>` only for
an explicit edit request. Missing explicit targets fail clearly; never remove
`--vendor` to obtain a fallback.

## Live UI is observation only

The optional Live UI is a read-only observer, not a prompt or dispatch path.
It displays existing jobs' `task.txt` and public `out.txt`, but never raw logs;
its history search and state filters can export only the currently visible public
metadata as local JSON; tokens and task/result bodies are excluded from export.
it cannot interpret natural language, choose routes, dispatch, retry, cancel,
delete jobs, or edit configuration. Natural-language interpretation and
dispatch stay in this skill and the CLI. Manage the local board with
`omnilane ui start|status|url|stop`, and stop it when monitoring is finished.

## Job lifecycle defaults

- **Active completion (Codex)**: after background CLI dispatch, use
  `scripts/completion-wakeup.py prepare` with the actual controller app thread,
  host, unique run ID and exact job allowlist. Use the returned handoff with the
  app `automation_update` heartbeat tool (reuse an existing monitor), then record
  the actual registration receipt. Do this before ending a turn with unobserved
  jobs. On callback, `poll`, keep unchanged state quiet, acknowledge delivery,
  inspect public results and verify, then acknowledge acceptance with evidence.
  Pause the real automation and record `closed` after all tracked events are
  handled. Never reuse a historical run ID or infer delivery from registration.
  See `docs/completion-wakeup.md` for exact commands and receipt schemas.
- **Other completion surfaces**: native agent callbacks provide the host result;
  still ingest and verify it. Claude's `UserPromptSubmit` inbox is passive and
  requires another prompt; it does not wake an idle controller. If no supported
  active callback exists, keep the controller active with `scripts/jobs.sh wait`
  and resume acceptance on return rather than asking the user to check again.
- **Live mailbox**: Claude and Gemini retain automatic resident background
  sessions for supported modes. Codex and Grok remain single-shot by default;
  explicit `--live` opts in. Grok live requires explicit `--mode sysops` because
  ACP has no enforceable restricted-mode boundary. Send follow-up instructions
  with `scripts/jobs.sh send <id> "<text>"` and finish with
  `scripts/jobs.sh close <id>`. `--single-shot` forces one-shot execution.
- **Goal orchestration**: when the next step depends on the previous result,
  wrap the dispatches in `omnilane goal open "<objective>" --workdir DIR`, then
  `goal dispatch <goal-id> ...`, `goal note`, `goal status`, `goal close --summary`.
  Budgets are unlimited unless `--budget-jobs` / `--budget-seconds` is passed.
  A single obvious task is dispatched directly, never through a goal.
- **Job hygiene**: `scripts/jobs.sh cancel <id>` stops a runaway job.
  `stats`, `recommend`, and `audit` are read-only and never change routing.

## Rules

- **Dispatch in `advise` mode by default** (read-only worker). Use `--mode work`
  only when the worker must edit files, and give it an explicit `--workdir`.
- **Mode contract**: `advise` is read-only with supported native web tools;
  `work` confines file/command changes to explicit `--workdir` and disables
  agent-tool networking, not the model connection. Codex and Claude have
  distinct policies for these modes. Agy 1.1.27 work has bounded new/resume
  acceptance with native sandboxed commands and four validated tools; external
  temp/cache reads are also restricted. A separate two-turn work live/FIFO
  check passed readback, outside-write denial and normal close. macOS Grok work remains blocked because native child-network
  isolation is Linux-only. Do not turn gaps into sysops implicitly or claim
  every provider/mode/session path has passed the runtime matrix.
- **Agy work tools** are `view_file`, `write_to_file`, `run_command`, and `finish`.
  The native `commandExecutionPolicy: sandbox`, `--sandbox`, and
  `proceed-in-sandbox` policy permits tested workspace edits/builds and denies
  tested outside writes, shell networking and explicit unsandboxed execution.
  Settings are rewritten explicitly before each start/resume: native omission
  of false/empty fields has not been proven default-equivalent. Workspace-local
  caches and the verified empty owned policy directory remain; external cached
  dependencies may be inaccessible, and the tested successful C build still
  emitted an xcrun default-cache denial warning. See the dated capability notes
  for the exact evidence boundary; complete effective SBPL was not captured.
- **`--mode sysops`** explicitly selects unrestricted native policies for
  Codex, Claude, Grok, and Agy; it is not an alias for work. It is a per-dispatch
  opt-in, never a lane default, and task text must name the allowed operations.
  Codex `work`/`sysops` supports non-Git directories through
  `--skip-git-repo-check`. Without an existing whole-job timeout, dispatch
  adds one when its supervisor is available; otherwise it warns and retains
  the per-call watchdog path.
  The CLI defaults an omitted `--workdir` to the caller’s current directory;
  task briefs must still specify it explicitly. The MCP work interface
  separately requires an explicit `workdir`.
- **Grok advise web tools** use internal `web_search` / `web_fetch` selectors,
  while permission rules keep their native `WebSearch` / `WebFetch` class names.
  The complete single-shot `plain` path has real search, fetched-page, and native
  denied-write evidence on Grok 1.0.13. MCP readiness is job-local because this
  mode denies MCPTool; hooks and their security checks remain enabled. A caller
  supplied nonempty `CONTEXT_MODE_MCP_SENTINEL_DIR` is a conflict and stops before provider
  startup rather than being overwritten. Do not extend this result to restricted
  live or macOS work.
- **Every dispatched task states acceptance criteria and the exact verification
  command.** Do not accept "done" without evidence.
- **No nested dispatch**: workers must not fan out again (enforced via
  `OMNILANE_DEPTH`). Escalate back to the main loop instead.
- **Same-directory codex dispatches are serialized automatically** (lock);
  do not try to parallelize them yourself.
- After two failed attempts, reassess scope and retry only an eligible exact configuration.
  An upward AA move requires the human to take over; a model cannot approve its own uplift.
- Vendor quota exhausted (429 / "stream disconnected" / usage-limit message):
  send mid-tier coding through coding-overflow instead; never silently downgrade
  hardest-coding — wait or escalate to the user.

## Per-model notes (apply the row matching YOUR main model)

These notes never expand the commander's reserved self-execution scope. If the verified main model has no matching row, use the configured lane table under the current user request and `rules.d/60`; do not assume the nearest older model is equivalent or silently override vendor/model/effort. If a required capability or explicit model choice is unresolved, report that exact gap before dispatch rather than inventing a fallback.

- **Claude Fable 5.1 main**: recommended prompt-level controller for
  quality-sensitive work (not a lane or automatic selector). Hardest coding
  uses max; judgment and taste use xhigh. Dispatch bulk work to Sol high,
  long/fast work to Gemini 3.8 Flash, and use Astra as an independent Codex
  review path.
- **Claude Opus 5 main**: balanced prompt-level controller and independent
  reviewer when explicitly selected (`high`, or `xhigh` for deeper review),
  plus Claude long-context fallback. This is a role/opt-in choice, not a new
  lane or the current hard-judgment default.
- **Claude Sonnet main**: coordination/tools/mid-tier coding only, plus fallback
  duty in bulk-mechanical and live-search; never self-assign top judgment or
  hardest implementation.
- **GPT Astra main**: controller backup and independent reviewer. Resolve the
  actual caller effort first; select only an exact target at or below its ceiling.
  An explicit higher-effort request does not bypass model-level AA policy.
- **GPT Sol main**: mechanical work and constrained UI drafts only within the
  exact effective ceiling; return higher-score needs to the operator.
- **GPT Terra main**: long-context and mechanical work only within the exact
  effective ceiling; do not infer eligibility from the Terra family label.
- **GPT Luna main**: high-volume triage is delegated at high; do not promote its
  low price into correctness-critical or controller work.
- **Grok 4.6 main**: live-search and coding-overflow are yours, plus fallback
  duty in hard lanes. Grok effort remains ignored; verify API signatures and
  cited facts before shipping.
- **Gemini 3.8 Flash main**: long-context uses medium, fast-agentic and triage
  use low, and bulk/overflow/web fallbacks use high. Do not infer visual taste
  or controller authority from agent/coding benchmarks.
- **Gemini 3.1 Pro main**: remains directly selectable, but is not promoted by
  this refresh; route hard coding and judgment to the stronger configured lanes.

## Frozen exact-AA downward gate

All lane and per-model preferences above are subordinate to this gate, including
explicit vendor/model/effort requests. Supply `--caller-context /absolute/context.json`
with schema_version=1, snapshot_id, kind=model, caller containing exact vendor/model/
effort/reasoning/fallback, and inherited_ceiling. Effective ceiling is the minimum of
that exact frozen score and the inherited ceiling. Targets at or below it are allowed;
unknown identities and unresolved request-selector mappings fail closed. No family,
displayed grade, highest-effort assumption, retry or fallback grants an uplift.

Build the file before the first dispatch, not after a refusal. Every field is an
exact identity: `caller` must reproduce one `scored_configs` row byte-for-byte,
and `snapshot_id` must equal the registry's own `snapshot.id`.

```json
{
  "schema_version": 1,
  "snapshot_id": "<registry snapshot.id>",
  "kind": "model",
  "caller": {"vendor": "claude", "model": "claude-opus-5", "effort": "high",
             "reasoning": "adaptive", "fallback": null},
  "inherited_ceiling": 52
}
```

Set `inherited_ceiling` to your own row's score when you are the root caller, or
to the ceiling you were handed when you are a child.

**Your identity is read from the CLI that launched you.** Without explicit or
inherited caller-context, dispatch finds the nearest vendor CLI. Claude, Grok
and Agy retain their launch selectors. Codex outside app-server retains explicit
`-m` / `--model` or `-c model=...` / `--config` selectors; TOML model overrides
require Python 3.11+. A profile is not an explicit model selector.

Codex app-server always ignores startup selectors, even explicit model flags;
other Codex launches without a model use the same current-turn rollout reader.
It requires matching UUID-shaped `CODEX_THREAD_ID` values in this process and the
codex direct child's initial environment (not text embedded in argv). Exactly
one active rollout under `$CODEX_HOME/sessions` (default `~/.codex`) must have
matching `session_meta.id`. The last `turn_context` must provide non-empty model,
effort and turn id, with no later `task_complete`, `turn_complete` or
`turn_aborted` event.
Missing, ambiguous, malformed or stale evidence refuses: no config defaults,
model-list, archive or other-thread fallback. JSONL is streamed; only identity
metadata and event types reach diagnostics, never message content.

`omnilane whoami` prints the resulting caller-context path and reports thread
and turn ids for rollout evidence. The existing scored-row resolver still makes
the decision; this is host request-selector evidence, not proof of upstream
provider identity. Nearest wins, including workers launched by another vendor.
Explicit `--caller-context`, inherited identity and `--operator-asserted-human`
retain their precedence; `OMNILANE_AA_CALLER_FROM_PROCESS=0` disables both readers.

Ancestor lookup still runs first. If it fails under `CODEX_SANDBOX=seatbelt`,
`whoami` explains that process inspection, `~/.omnilane` writes and networking
require rerunning the command outside the Codex sandbox; do not retry with a
caller-context inside that sandbox.

Only when `omnilane whoami` refuses: ask the operator, or declare the
lowest-scoring row of your model and say so in your report. Understating only
narrows what you may dispatch to, so it fails in the safe direction — but it is
the fallback, not the first move, and an unnecessarily low ceiling silently
closes lanes and pushes the question back onto the operator. Never raise the
declared effort to unblock a refused target, and never assert
`--operator-asserted-human` on your own behalf.

Three refusal codes mean different things and need different fixes.
`missing-caller-context` means no identity reached the gate: you passed no file
and dispatch could not read one from your launching CLI. Run `omnilane whoami`;
its refusal says exactly why, and that reason is what to fix or report.
`runtime-mapping-unverified` means the file is fine but the *target* has no proven
host-local request selector; that is fixed by a `--transport-overlay` entry backed
by real evidence, never by editing the frozen registry (its sha256 is pinned in
`scripts/lib/aa_policy.py`, so any edit fails the whole gate closed).
`invalid-policy-input` with "transport contract evidence changed" is neither: the
overlay itself will not load, so nothing about your caller or your target is wrong.
Run `omnilane doctor` first — its `transport-overlay` check names the offending
file and the vendor it belongs to. Do not go hunting by hand.

Upgrading a vendor CLI is the usual cause. The overlay pins the sha256 of each
vendor's executable and runner script, so a new release invalidates that vendor's
selector evidence. Evidence entries carry a `vendor` tag: a tagged entry that
drifts marks only its own vendor stale, and the other three keep dispatching.
Untagged evidence — `probe-manifest.json`, and any overlay built before the tags
existed — still fails the whole gate closed, which is what an unpatched host
looks like. Codex and Claude resolve through version directories
(`releases/0.153.4-…`, `versions/2.1.266`), so their upgrades remove the anchored
file rather than change its digest; both are treated as staleness, not corruption.

Do not expect these upgrades to be operator actions. agy and grok update
themselves in the background when invoked — agy's own `cli.log` records
`auto_updater.go: Spawned background update process`, and both binaries changed
under a probing session on 2026-09-10, minutes after their first call. Overlay
drift is therefore a routine consequence of using a vendor, not an occasional
maintenance event, which is why per-vendor degradation matters more than it
looks. It also means any test asserting a fixed number of verified live
mappings will go red on its own schedule.

Because of that, `build_overlay.py` anchors the executable `shutil.which` resolves
rather than a version written into the script. A pinned path drifts out of use
silently: before 0.42.6 the overlay hashed claude `2.1.263` while every dispatch
ran `2.1.266`, so eleven mappings were "verified" against a binary that had not
run for a day.

Re-signing is a probe, a rebuild, and an install, in that order. Back up
`~/.omnilane/transport-contracts.local.json` first; restoring it is the rollback.
`scripts/lib/probe.py --expect TOKEN [--vendor V] NAME COMMAND…` invokes the CLI
directly through `subprocess`, so it works while the gate is refusing everything —
this is what breaks the deadlock. `scripts/provider-probe.sh` goes through
`dispatch.sh` and therefore through the gate, so it is useless in this state.
Then `scripts/lib/build_overlay.py` rebuilds, and you copy the result over the
live overlay. Verify with a real dispatch on a lane belonging to the vendor you
re-probed; loading the registry in Python is not the runtime surface.

Keep the sweep where its default `--root` puts it,
`~/.omnilane/transport-evidence/<sweep-id>/`. The rebuilt overlay anchors
`probe-manifest.json` by absolute path as untagged evidence, so a sweep parked
inside a repository is one `git clean -fdx` away from taking every vendor down
at once — the same global refusal a re-signing session is usually trying to end.

Every mapping carries an `evidence_tier` saying how strongly its probe pinned the
responder. `billed-model` means the provider named the model it charged for —
Claude's `modelUsage`, grok's under `--output-format json`. `client-echo` means
the CLI wrote down the model it asked for — codex's session rollout, agy's
`cli.log` resolver line. `selector-only` means the CLI accepted the selector and
said nothing more. Put plainly: `client-echo` is the CLI's copy of your order,
`billed-model` is the provider's receipt. Neither certifies upstream identity,
but only one of them was written by the party that answered.

The tier is reported, never enforced. Dispatch still turns on `runtime_verified`
alone, so a mapping that drops to `selector-only` keeps working and simply shows
up in doctor as worth re-probing. Do not add a tier check to the gate: that would
rebuild the failure 0.42.5 removed, where evidence quality could refuse a lane
that runs. The tier is read off the evidence a run produced rather than assigned
per vendor, so a sweep predating 0.42.6 re-judges as `selector-only` and a CLI
that starts reporting a billed model is promoted with no code change.

Two probe details follow from this. Codex needs `exec --json` (the thread id that
locates the rollout) and must *not* use `--ephemeral`, which suppresses the very
rollout the tier reads. agy needs its own app data directory, prepared exactly
the way `run-gemini.sh` does it — `prepare-agy-mode.py --mode advise` returns a
path relative to `~/.gemini` that is passed as `--app_data_dir=`; the environment
variables that look like they would do this are ignored.

Never sign a probe you did not read. `probe.py` records a `verdict` because exit
status alone is not evidence: the Claude CLI answers a quota refusal with a JSON
body carrying `is_error`, and it accepts an unknown `--effort` by silently using
the default, returning exit 0, the right `modelUsage`, and the expected token
with only a stderr warning to show for it. Effort is half of a scored identity,
so that path would certify a mapping at the wrong tier. Configurations whose
probes failed are recorded in the overlay's `unproven[]` and surfaced by doctor
instead of vanishing — six Fable rows sat unusable for two days in September
2026 because a 429 quota refusal left no trace anywhere. A refused probe is not
always transient: re-probing those six two days later returned the same 429, so
an `unproven[]` entry can mean the account, not the moment. Read the reason
before assuming a retry will clear it.

A `--transport-overlay /absolute/overlay.json` may prove a small set of host-local
request selectors using exact identities and hashed local contract evidence. It does
not change frozen AA scores or certify upstream provider identity. The explicit
`--operator-asserted-human` exemption is cooperative operator metadata, not automatic
model detection or OS authentication; model callers must not assert it for themselves.

CLI jobs atomically save an original authorizer, exact child caller context, decision,
and registry snapshot. Provider processes receive the child identity, not the parent's.
Retries preserve original target config, check stored hashes, and intersect the
current exact caller score/inherited ceiling with the original authorizer ceiling.
Use `omnilane jobs retry ID --caller-context FILE`; missing current identity fails
closed, and a model retry never inherits an earlier human exemption. Native handoffs carry the same decision plus a job-owned
`worker_contract.caller_context_path`; pass that context to the native child together
with the no-nested-dispatch requirement. `OMNILANE_DEPTH` remains an independent guard.

A background job or PENDING native handoff is not completion. Observe its terminal
result and acceptance evidence before closing the controller task. Completion wakeup
availability must be separately verified; never claim delivery from scheduling alone.

For Gemini `model_id_encoded_effort` selectors, the proven native model ID encodes
the AA effort. An absent parity effort or the matching effort is accepted; a conflicting
parity effort is rejected. Do not represent a discarded EFFORT parameter as an active
provider setting. Frozen reasoning=`unspecified` remains a literal, not a wildcard.
