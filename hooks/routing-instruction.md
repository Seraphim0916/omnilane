<!-- omnilane-routing:start -->
<!-- source: codex-s / MacStudio; repo omnilane; approved AA v4.2 snapshot 2026-09-07 -->
## omnilane — model routing and executor selection

Delegate tasks by default, including when the resolved model is the commander's
exact model. Native agents count as delegation; a model match is not permission
to self-execute. Consult `omnilane list` and classify the lane, then resolve
vendor/model/effort separately from executor choice:

    omnilane route [--executor auto|native|cli] [--native-context FILE] [--vendor V] [--mode work] [--workdir DIR] <lane> "<task>"

Terminal `auto` without capability context preserves legacy CLI. Build native
capabilities only from the active agent-tool contract. Codex
`collaboration.spawn_agent` has no sandbox/tool/workdir restriction parameters
and inherits the parent's tools/filesystem. Its request and matching capability
row must explicitly use `shared-inherited` with empty tool arrays;
`advise`/`work` and workdir remain task intent, not OS isolation. Hard isolation
stays same-model CLI in auto and rejects forced native. Supply the active
harness/vendor, exact supported model/effort, optional known current model,
mode/workdir, isolation, and lifecycle. Same vendor is not the same model.
Unknown capabilities do not match. Do not infer capability from credentials,
installed binaries, or model families. Explicit vendor/model/effort must survive
fallback unchanged.

Native is a host-callable tool, not a shell executable. A native route emits a
machine-readable PENDING handoff JSON with task/job ID and resolved requirements.
The caller executes the declared native strategy, passes intent plus a
no-nested-delegation instruction, waits for the actual result, and records it with:

    omnilane jobs --json complete-native JOB_ID /absolute/completion.json
    omnilane jobs --json status JOB_ID
    omnilane jobs --json result JOB_ID

Completion includes actual agent ID, runtime vendor/model/effort/harness/backend,
outcome, public result and evidence. A handoff alone is not success. Duplicate or
invalid completion is rejected. Native cancellation records cancellation without
signaling PIDs; the caller must separately stop an already-spawned agent.

When the caller supplies an explicit model override, use `fork_turns: "none"` or
a bounded positive history count; never combine it with `fork_turns: "all"`.
Unknown caller current model may be omitted only when an exact requested model is
explicitly selected and declared in the matching capability row.

Forced CLI preserves external execution; forced native rejects missing or
incompatible capability. Auto emits an explicit CLI reason, never a different
vendor/model on native fallback. Background/durable/live/named CLI sessions,
sysops, unsupported isolation and vote/arbitration/multi-round paths remain CLI.

The commander owns planning, decomposition, task briefs, routing/handoff/result
orchestration, acceptance, operator replies, git commit/push and governance edits.
Workers execute assigned tasks and never delegate again (shell depth guard:
exit 86; native callers must enforce the same rule). Read-only work defaults to
advise. Implementation uses explicit `--mode work --workdir DIR --timeout 3600`
or longer; native timeouts are caller-enforced. External long jobs can use
`--background`, CLI `jobs wait`, live send/close and CLI goal-loop as before.

If a vendor/model is explicitly named, retain `--vendor` and apply the
omnilane skill's consultation rules. The repository skill and
`docs/native-executor.md` define schemas, examples and limits. Only the parent
backs up and syncs the host's managed AGENTS block after review.
Exact-AA downward policy applies before every native/CLI candidate, fallback, retry,
and vote constituent. Supply an exact model `--caller-context FILE`; unknown identity
fails closed, and target score must be <= min(caller score, inherited ceiling).
Explicit targets do not override the gate. Use `--transport-overlay FILE` only for
host-local hashed request-selector evidence, never as a score or identity upgrade.
Pass the job-owned child caller context to native workers; CLI propagates it itself.
Retries intersect the current caller with the original authorizer ceiling, retain
the target config, and revalidate integrity. Missing current caller fails closed;
a model retry does not inherit a previous human exemption.
Human exemption is an explicit cooperative operator assertion, never inferred for a
model. Preserve OMNILANE_DEPTH. Failed work returns to the operator rather than an
unapproved upward route. Observe terminal result and acceptance before ending a
controller task; a background job/PENDING handoff is not success, and wakeup delivery
requires its own evidence.

For Codex background CLI jobs, actively bind completion to this controller before
ending the turn: use the repository's `scripts/completion-wakeup.py prepare` with
the actual app thread ID, local host ID, a unique run ID and exact job allowlist.
Use its handoff with the app `automation_update` heartbeat tool; update an existing
controller monitor instead of duplicating it. Record the successful tool receipt
with `record-registration`. Never write scheduler files directly or label an
unregistered handoff as active. Current caller metadata must come from this
controller's verified runtime, not another task's context or a default model.

On the scheduled callback, `poll`; stay quiet when nothing changed. For a terminal
event, record `ack-delivered`, inspect the public result and required verification,
then `ack-accepted` with PASS/FAIL/PARTIAL evidence. Exit zero is not acceptance.
After all tracked events are handled, pause the actual automation with the app
tool and record `closed` using its receipt. New runs get fresh, never-reused IDs.
See `docs/completion-wakeup.md` for binding, leases, replay and expiry rules.
Heartbeat is scheduled polling, not instant push. A next-prompt inbox is not wakeup.
Without a supported callback tool, keep the controller active using `jobs wait`
and continue acceptance on return; do not end with an unobserved background job.
Native completion uses the host's agent callback and the same actual-result gate.

Native reuse is explicit, never a silent substitute for new-agent creation. It
requires a proven exact existing agent, caller-observed idle state and preserved
context consent in the capability. Recheck idle before `collaboration.followup_task`;
the completion must match strategy, agent and backend. Unknown identity or busy
agents do not qualify. See `docs/native-executor.md`; a thread quota failure is
not a successful run, and a cancelled pending job stays cancelled.

<!-- omnilane-routing:end -->
