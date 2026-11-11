# Codex connection cuts and manual continuation

The final structured `error.message` or `turn.failed.error.message` must exactly
match `Incomplete response returned, reason: upstream_eof` or
`Incomplete response returned, reason: upstream_error`. Task text quoting that
message and generic stream failures do not qualify. Provenance for those two
shapes is recorded in `EOF-RESUME-REPORT.md` and `EOF-RESUME-FIX1-REPORT.md`.

## No automatic continuation

Every Codex single-shot dispatch runs its runner once. `work` and `sysops` can
change files, so a transport cut never starts a second invocation, even after
all of the first invocation's processes have exited. `advise`, including named
persistent threads, also has automatic continuation disabled. There is no
attempt process guard, stop-proof artifact or host-wide stable scan.
`--no-resume` and `OMNILANE_NO_RESUME=1` remain accepted compatibility options.

A transport cut without completion evidence exits **75**, with
`state=interrupted`, `outcome=interrupted-needs-decision` and the original vendor
exit retained in `attempts`. A final `turn.completed` (the last `turn.started`,
`turn.completed` or `item.*` event) or a nonempty final reply file instead
preserve output and the contradictory original exit as
`completed-with-transport-error`; they cannot qualify for continuation.
A later `turn.started`, any `item.*` activity, or an unparseable or over-1 MiB
progress line withdraws an earlier `turn.completed`. Each `turn.started` opens
a turn; `turn.completed` and `turn.failed` close one, never below zero. A
`turn.completed` establishes completion evidence only when no turn is left open
after it closes one (a `turn.completed` without any `turn.started` still
counts); while a turn stays open (a late completion of an older turn, or an
abandoned turn), that `turn.completed` does not prove completion. Exit 75
applies only to an eligible nonzero exit with an exact transport error, a
zero-byte final reply file and no completion evidence.
An `agent_message` item is provisional: later tool or new-turn activity withdraws
any apparent finality. The item type and text alone cannot distinguish progress
from a final answer, including when EOF hides what would have followed. Without
either definitive completion signal above, classify it as interrupted and let
the operator decide.
Ordinary failures, refusal/setup, watchdog and signal exits retain their exits.
A supervisor timeout or cancellation overrides stale transport diagnosis.

Status, receipts and notification expose only validated classification fields,
not the raw vendor error, task text, process environments or vendor session ID.
The file-changing-mode receipt uses this fixed wording:

```text
omnilane: job JOB_ID was cut in transit (upstream_eof) after N s; interrupted, needs decision; the task itself did not fail; nothing was restarted automatically because this mode can change files. Running resume is the operator's statement that the earlier writer has stopped and continuing after its partial effects is safe. Next: omnilane jobs resume JOB_ID
```

Advise says automatic continuation is disabled. Completion evidence offers
`omnilane jobs result JOB_ID`. Notifications publish the final outcome once,
using `TRANSPORT_INTERRUPTED NEEDS_DECISION` or
`COMPLETED_WITH_TRANSPORT_ERROR` as appropriate.

## Explicit continuation

```sh
omnilane jobs status JOB_ID --json
omnilane jobs resume JOB_ID
```

The second command is the operator/commander's statement that the earlier writer
has stopped and continuing after its partial effects is safe. Repeating the
command is not an exactly-once guarantee: the original job is not marked consumed,
and each accepted command creates another continuation job for the same session.
It checks only exact `OMNILANE_JOB_TOKEN` ownership and prints
`PID=... START_US=...` for any still-live matching process, then refuses. Start
time is microseconds since epoch on macOS and since boot on Linux. Unrelated
process starts/exits, unreadable unrelated processes and changing host process
membership do not constitute refusal votes. This is not a proof that every
possible descendant stopped: a process that clears its inherited marker is
outside the marker mechanism. Global inspection denial still refuses, rather
than silently bypassing a sandbox or treating an unavailable scan as empty.

The saved regular progress artifact must still match its recorded identity,
contain one valid original session ID, and belong to the unchanged job route,
mode and workdir. Ephemeral advise sessions, missing/ambiguous sessions,
completion evidence and a changed or unavailable original session cannot be
continued. The selected CLI must support `exec resume`; refusal never falls
back to a fresh execution. `jobs retry ID` remains the distinct command that
repeats the original brief and still refuses sysops.

Manual continuation uses the existing runner's `exec resume ORIGINAL_ID -`
with a fixed instruction to continue from recorded tool results and stop on an
unknown effect. It creates a new Omnilane job for the same Codex session, leaving
the original cut's receipt/history intact. The new job has its own per-call and
whole-job budgets; automatic retry is disabled there too. The original route,
model, effort, mode, working directory and policy ancestry are retained, with
current caller validation and the existing workdir lock checked again.

As with retry, `--caller-context FILE` is available for a model caller and
`--operator-asserted-human` is for an actual operator's explicit assertion only.
With neither, resume attempts the existing verified CLI caller discovery; it
never borrows the old caller's identity. A missing identity or changed policy
must be resolved through the existing authorization flow. `--background` is
supported. No current-caller flag is printed as an invented grant of authority.

`codex-resume.json` is private bookkeeping: process marker, session provenance,
original vendor exit and timing. Public fields include `attempt`, `attempts`,
`reason_class`, `transport_reason`, `elapsed_seconds`, `completion_observed`,
`outcome`, `auto_resume_eligible` (always false) and
`continuation_blocked_reason`. The same fields reach status and completion
receipts. The manual command does not run the retired stable scan.
