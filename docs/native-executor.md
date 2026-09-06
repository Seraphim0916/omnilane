# Native executor protocol (v1)

`native` means a **caller-owned agent tool**, not a shell executable. Omnilane
resolves a lane, checks explicitly supplied capabilities, and emits a pending
handoff. The host executes the declared new/reuse strategy and ingests its actual result separately.
Creating a handoff is not task success. Native delegation still counts as
delegation when commander and worker use the same exact model.

## Selection and terminal compatibility

```sh
# Standalone terminal: no native capability context, legacy CLI selection.
omnilane route --dry-run hardest-coding "Review the change"
omnilane route --executor cli --dry-run hardest-coding "Review the change"

# Host-generated capability file: native when ALL requirements match.
omnilane route --executor auto --native-context /absolute/capability.json \
  --workdir /absolute/repo --dry-run hardest-coding "Review the change"

# Fail closed instead of falling back. JSON on stdout; no provider is called.
omnilane route --executor native --native-context /absolute/capability.json \
  --workdir /absolute/repo hardest-coding "Review the change"
```

Native-aware selection fixes the first configured lane candidate (or the
explicit `--vendor` row) **before** checking native or CLI availability.
`--model` and `--effort` override that row without renaming either value. No
model aliases/family inference or vendor substitution are performed. A native
rejection in `auto` may use only that same resolved target's CLI; if it is
missing, exit 4 rather than walk another vendor's fallback chain. Without a
context, `auto` and forced `cli` preserve the historical available-CLI chain.
`--executor cli` ignores the native context. CLI plans and metadata include
`executor=cli` and a reason; native plans are JSON.

`--executor native` returns exit 2 for missing/incompatible capability context.
Malformed contexts are errors even in `auto`, not invitations to call a CLI.
Native protocol support requires Python 3.9+ on the local host; the unchanged
terminal path does not acquire that dependency. `--list` / `--explain` retain
their legacy CLI-availability meaning and do not advertise native readiness.

## Capability context

The caller constructs this object from the **currently exposed tool contract**
and its known runtime, not credentials, installed binaries, environment sniffing,
or model family guesses. These example model/harness values are slots, not a
catalog. Paths must be existing absolute directories (canonicalized for exact
comparison; a parent directory does not grant child workdirs).

```json
{
  "schema_version": 1,
  "harness": "HARNESS_FROM_RUNTIME",
  "vendor": "codex",
  "current_model": "MODEL_FROM_RUNTIME",
  "requirements": {
    "tools": [],
    "isolation": "shared-inherited",
    "lifecycle": "single-shot"
  },
  "capabilities": [{
    "model": "EXACT_SUPPORTED_MODEL",
    "efforts": ["EXACT_SUPPORTED_EFFORT"],
    "modes": ["advise"],
    "workdirs": ["/absolute/repo"],
    "tools": [],
    "isolations": ["shared-inherited"],
    "lifecycles": ["single-shot"]
  }]
}
```

Every field shown except `current_model` is required. Capability rows describe
joint constraints; permission from separate rows is never combined. All arrays
except `tools` must be nonempty. `current_model` is only needed when the routed
model is absent or `-`; when the route explicitly selects an exact supported
model, the caller may omit an unknown current model. The effort string is
matched exactly; an absent effort or `-` is unknown, so native rejects it until
the caller supplies a known explicit `--effort`. Model names are never
hardcoded in the engine.

Codex `collaboration.spawn_agent` exposes no sandbox, tool allowlist, or
workdir restriction parameter and inherits the parent's tool/filesystem access.
Its honest native capability uses `shared-inherited` in both the request and the
same matching capability row. `tools: []` means no tool restriction is
requested or advertised. `advise` and `work` remain task intent; the matched
workdir is task context, not an OS boundary. Requests for hard `read-only`,
`workspace-write`, or any other unsupported isolation never become shared
native jobs: `auto` stays on the same resolved model through CLI, while forced
native fails closed.

Native supports only a caller-supervised single task. `--background`, explicit
`--live` / `--single-shot`, `--thread`, `sysops`, explicit/environment whole-job
or idle watchdogs, vote/multi-round and `exec` arbitration paths stay CLI or
reject forced native. The handoff's `timeout` is a caller-enforced deadline,
not a shell watchdog. Native has no durable worker, FIFO, implicit follow-up,
CLI lock, scheduling, or inherited named session. Supplying a context does not
change `routing.yaml`, `routing.local.yaml`, or host configuration.

## Handoff, completion and cancellation

Native route stdout is one JSON object: `schema_version`, `executor`,
`executor_reason`, `vendor`, `model`, `effort`, `harness`, `lane`, `task`,
`mode`, `workdir`, `requirements`, `timeout`, `worker_contract`, `job_id`,
`task_id`, `agent_id`, `state`, `provider_invoked`, `job_state_created`.
Normal routing returns `state=pending`, equal task/job IDs and null agent ID.
Dry run returns `state=planned`, null IDs and creates neither job store nor job.
Dry run does not read task stdin. It never spawns or calls a provider.

`worker_contract` records `no_nested_dispatch`, the matched
`shared-inherited` isolation, that mode is task intent, and that the caller owns
deadline enforcement. It does not claim tool or filesystem restriction.

The host invokes its real agent tool with the resolved model and effort, then
passes the workdir, mode, task, and deadline as task intent rather than claimed
tool or filesystem enforcement. With an explicit model override, Codex must use
`fork_turns: "none"` (or a bounded positive history count), never
`fork_turns: "all"`:

```javascript
collaboration.spawn_agent({
  task_name: "native_shared_smoke",
  fork_turns: "none",
  model: handoff.model,
  reasoning_effort: handoff.effort,
  message: "Shared/inherited access. Do not delegate. Intended workdir: " +
           handoff.workdir + ". Deadline: " + handoff.timeout +
           " seconds. Task: " + handoff.task
})
```

The call does not establish a filesystem boundary. The parent keeps the actual
agent ID, waits for the real outcome, independently verifies the public result,
and only then writes **public, sanitized** completion input:

```json
{
  "schema_version": 1,
  "job_id": "JOB_ID_FROM_HANDOFF",
  "agent_id": "ACTUAL_AGENT_ID",
  "runtime": {
    "vendor": "codex",
    "model": "ACTUAL_EXACT_MODEL",
    "effort": "ACTUAL_EFFORT",
    "harness": "ACTUAL_HARNESS",
    "backend": "ACTUAL_AGENT_TOOL_BACKEND"
  },
  "outcome": "success",
  "result": "Public result summary, not raw logs",
  "evidence": ["Public command/result or artifact reference"]
}
```

```sh
omnilane jobs --json status JOB_ID
omnilane jobs --json complete-native JOB_ID /absolute/completion.json
omnilane jobs --json result JOB_ID
omnilane jobs --json list --status pending
omnilane jobs --json cancel JOB_ID
```

The reusable `jobs complete-native` interface checks the actual runtime's
vendor/model/effort/harness against the resolved request, requires agent ID,
backend, outcome (`success` or `failure`), result and nonempty evidence, and
rejects malformed/extra fields, symlinks, oversized inputs and duplicate JSON
keys. Missing/invalid/duplicate completions leave state unchanged. Completion
and cancellation share a per-job lock and an atomic state replacement.

`pending -> completed` exposes `state=done`, `native_state=completed`, exit 0
for success or 1 for failure. Ingestion itself returns 0 for a valid failure
record; `jobs result` returns the recorded exit code. `pending -> cancelled`
records exit 143 and permanently rejects later completion. Neither transition
signals a PID. If an agent was already spawned, **the caller must cancel it
using that agent tool separately**; cancelling the record cannot stop it.

Data lives under `$OMNILANE_HOME/jobs/ID/`: `task.txt`, `meta.json`,
`native.json` and `native.lock`. Creation writes all four 0600 files inside a
0700 hidden same-filesystem staging directory, then atomically renames it to the
final ID under a private publication lock. Listing only accepts final ID names,
so it never observes construction or an interrupted hidden stage. Normal
failures clean only the creator's own stage; collisions leave the existing
final directory untouched. The context itself and raw provider logs are not
stored. Completion is a
caller attestation, not independent backend authentication or proof that the
model honored the task; the parent still verifies evidence. Do not submit
tokens, cookies, credential/session/cache values or raw logs in any public field.

Only `list`, `status`, `result`, `cancel`, `complete-native` integrate native
jobs. Native `send`, `watch`, `close`, `wait`, `retry`, `tail`, and `rm` reject;
inspect status while the host owns execution. Native records do not have CLI
exit markers and are not included in completed-CLI stats/recommend/prune.
Do not use CLI-only UI/audit/goal-loop summaries as native acceptance evidence.

## Explicit existing-agent reuse（明示重用）

協定版本：2026-09-07。
Default `agent_strategy` is `new`; exhausted creation capacity never silently becomes reuse.
Explicit `reuse` keeps the existing context and uses caller-owned `collaboration.followup_task`,
not `collaboration.spawn_agent`. This remains one supervised task, not an automatic loop.

Reuse extends the v1 capability object (values must be caller-observed, not inferred):

```json
{
  "schema_version": 1,
  "harness": "codex",
  "vendor": "codex",
  "current_model": "gpt-6-astra",
  "current_effort": "medium",
  "agent_strategy": "reuse",
  "preserve_existing_context": true,
  "new_agent_capacity": "exhausted",
  "existing_agent": {
    "agent_id": "/root/EXISTING_AGENT",
    "vendor": "codex",
    "model": "gpt-6-astra",
    "effort": "medium",
    "harness": "codex",
    "state": "idle",
    "observed_by": "caller",
    "evidence": ["Caller-observed creation configuration and current idle state"]
  },
  "requirements": {"tools": [], "isolation": "shared-inherited", "lifecycle": "single-shot"},
  "capabilities": [{
    "model": "gpt-6-astra", "efforts": ["medium"], "modes": ["advise"],
    "workdirs": ["/absolute/repo"], "tools": [],
    "isolations": ["shared-inherited"], "lifecycles": ["single-shot"],
    "agent_strategy": "reuse", "existing_agent_id": "/root/EXISTING_AGENT"
  }]
}
```

Reuse requires routed vendor/model/effort/harness and existing runtime to match exactly.
`current_model` and `current_effort` must match too; capability rows/agent IDs are never combined.
The idle declaration is caller evidence, not provider authentication or a reservation. Recheck idle
immediately before followup; the tool may still fail. Model self-description is not creation evidence.
Retained context may contain old instructions, so supply the new task boundary and existing AA child contract.

Missing evidence is malformed context. Busy/unknown state, missing preservation, identity/strategy mismatch,
hard isolation or unsupported lifecycle yield no reuse handoff. Forced native rejects; auto may choose
only the original exact target's CLI for a well-formed but incompatible context.
`new_agent_capacity` accepts available/exhausted/unknown; exhausted rejects new native selection.
Unknown retains the old pending-handoff behavior, never asserts that an agent successfully started.

Plans/status/results preserve `agent_strategy`, `existing_agent_id`, `preserve_existing_context`.
Pending reuse `agent_id` identifies the existing target, not proof followup ran.
`worker_contract.backend` is `collaboration.followup_task`; `reuse_observation` records the caller's evidence.

```javascript
collaboration.followup_task({
  target: handoff.existing_agent_id,
  message: "Keep existing context. Shared/inherited access; do not delegate. " +
           "Apply the handoff's AA child context and task boundary. " + handoff.task
})
```

After real response and independent verification, ingest the normal completion object with
`agent_strategy: "reuse"`, exact existing `agent_id`, and runtime backend `collaboration.followup_task`.
Different strategy/ID/backend is rejected without completing the job; duplicate completion is rejected.
New completions may omit strategy or supply `new`, but must not claim the followup backend.
Both strategies retain AA preflight, approved original registry bytes, child context, atomic publication,
and per-job completion/cancellation locking. Reuse adds no automatic creation, permission upgrade or service.

Offline coverage: `TMPDIR="$PWD/.native-test-artifacts/tmp" python3
tests/test_native_executor.py`. Those fixtures are not a live native smoke.
