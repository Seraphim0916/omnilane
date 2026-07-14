# Natural-Language Dispatch Design

Date: 2026-07-14  
Status: approved direction; implementation pending

## Goal

Let a user ask an Agent-Skill-capable main loop to consult a named model or
route an unnamed task using ordinary language, while keeping omnilane's current
job, timeout, safety, and audit behavior.

Examples:

- `Ask Opus to challenge this architecture.`
- `Have Grok check the latest public information about this claim.`
- `Let Codex review this repository.`
- `Which model should inspect a very large codebase?`
- `Review this flaky test and find the root cause.`

The main loop remains the natural-language interpreter. No extra classifier
model or local natural-language parser is introduced.

## Scope Split

This is feature 1 of two deliberately separate changes:

1. Natural-language dispatch: this design.
2. Live UI: a later stacked branch after feature 1 is complete.

The Live UI, persistent conversation history, local HTTP server, SSE stream,
and browser control are out of scope here.

## User-Facing Semantics

### Explicit target

When the user names a vendor or model, that target wins over automatic lane
selection. The skill classifies the request as a read-only consultation unless
the user explicitly requests file changes.

Examples:

- `Ask Opus ...` selects the Claude candidate and may override its model with
  the canonical Opus model identifier.
- `Ask Claude ...` selects the configured Claude candidate.
- `Ask Grok ...` selects the configured Grok candidate.
- `Ask Codex ...` selects the configured Codex candidate.
- `Ask Gemini ...` selects the configured Gemini candidate.

An explicitly requested vendor must never silently fall back to another
vendor. Missing configuration, a missing CLI, or an unavailable target returns
a clear error.

### Automatic target

When no vendor or model is named, the main loop classifies the task into the
existing lane table and dispatches normally. Existing fallback chains remain
unchanged.

### Capability question

A question such as `Which model is best for this task?` is informational. The
main loop reads the effective routing table, answers with the recommended lane
and model, and does not dispatch unless the user also asks it to run the task.

### Permission mode

Consultation and review default to `advise`. The skill may use `work` only when
the user explicitly requests edits and supplies or clearly establishes the
working directory. Existing `work` safety rules remain authoritative.

## CLI Contract

Add an optional selector to `scripts/dispatch.sh`:

```text
--vendor codex|claude|grok|gemini
```

The selector means: choose this vendor's candidate from the requested lane
instead of choosing the first available candidate. It does not add a fallback
to another vendor.

Resolution behavior:

1. Parse the lane's existing fallback chain with the current safe tokenizer.
2. Find the first candidate whose vendor exactly matches `--vendor`.
3. Fail with exit 4 if that vendor has no candidate in the lane or its CLI is
   unavailable.
4. Apply the existing `--model` and `--effort` overrides after vendor
   selection.
5. Continue through the existing job directory, metadata, timeout, depth guard,
   runner, foreground/background, and output paths.

Missing or invalid `--vendor` values return exit 2. Vendor values are an
allowlist, never shell-evaluated text.

## Direct Consultation Lane

Add a `consult` lane containing one candidate for each supported model vendor:

```text
consult: codex ... | claude ... | grok ... | gemini ... | off
```

The published defaults use the same canonical model identifiers already used
by the existing lane table. Users may override the whole chain in
`~/.omnilane/routing.local.yaml`.

The natural-language skill uses `consult --vendor <vendor>` for explicit model
requests. This keeps direct consultation separate from task taxonomy: asking
Opus an architecture question no longer requires pretending the task belongs
to `taste-final` merely to reach Claude.

If the user names a specific known model, the skill also supplies `--model` and
the appropriate effort when needed. A generic vendor name uses that vendor's
configured `consult` candidate.

## Skill Contract

Update the omnilane skill and `/route` command with this decision order:

1. If the user asks only which model or lane is appropriate, answer without
   dispatching.
2. If the user explicitly names a vendor or model, dispatch through `consult`
   with `--vendor`; use `--model` only for an explicit model name.
3. Otherwise classify the task into an existing lane and dispatch normally.
4. Default to `advise`; require explicit edit intent and workdir for `work`.
5. Relay the result and independently judge important claims.

Natural-language examples belong in the skill description and documentation so
Agent-Skill-capable harnesses can trigger it without a special shell command.
The shell command itself remains structured; `omnilane ask "free text"` is not
added because Bash cannot reliably interpret open-ended language without an
extra model call.

## Compatibility

- `--vendor` is optional. Existing dispatch calls behave exactly as before.
- Existing fallback behavior remains unchanged when `--vendor` is absent.
- Existing `--model`, `--effort`, `--timeout`, and background behavior remain
  available.
- The new `consult` lane appears in `--list` and can be locally overridden like
  every other lane.
- The whole-job timeout draft PR remains independent and can be rebased or
  merged separately.

## Security and Failure Handling

- Reuse `parse_lane_segment`; do not use `eval` or shell token expansion.
- Accept only `codex`, `claude`, `grok`, or `gemini` for `--vendor`.
- Keep user task text in the existing task-file boundary.
- Never silently substitute a different vendor after an explicit request.
- Preserve the nested-dispatch guard and read-only default.
- Keep exact provider errors in existing stderr logs; show a concise routing
  error to the caller.

## Tests

Add shell tests covering:

1. `--vendor` selects a non-first candidate from a multi-vendor chain.
2. An explicit vendor never falls back to another installed vendor.
3. Missing and invalid `--vendor` values exit 2 with readable messages.
4. A vendor absent from the lane exits 4.
5. A configured vendor with no installed CLI exits 4.
6. `--model` and `--effort` still override the selected vendor candidate.
7. Dispatch without `--vendor` retains current fallback behavior.
8. The `consult` lane appears in the effective routing table.

Run:

```bash
bash -n bin/omnilane scripts/*.sh scripts/runners/*.sh tests/run.sh
shellcheck -S warning bin/omnilane install.sh scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh tests/run.sh
bash tests/run.sh
bash scripts/dispatch.sh --list
git diff --check
```

## Acceptance Criteria

- Natural-language skill instructions cover explicit model consultation,
  automatic routing, capability-only questions, and permission mode.
- Explicit vendor requests deterministically select that vendor or fail clearly.
- No extra classifier model, server, database, browser, or UI dependency exists.
- Existing callers remain compatible and the complete test suite passes.
