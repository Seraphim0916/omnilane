# Lossless task transport

Task bytes are never truncated and there is no truncation opt-in. "Byte for
byte" covers two places: the staged task file, which holds `task.txt` exactly,
and the request body sent to a pure API target. A stdin task keeps every byte
in both, trailing newlines included.

Argument-form dispatch keeps the existing convention of appending one newline
to `task.txt`. As on main, runners that read the task file (Codex, Grok, exec,
pure API) receive it with that newline; runners that pass the task as one CLI
argument (Claude, Gemini, OpenCode, Kimi, Qwen) and the first live message
drop trailing newlines. Use `-` and stdin when byte identity with an existing file matters.
UTF-8 is required where the provider protocol represents text as JSON.

| Target | Modes eligible for file delivery | Over transport limit |
| --- | --- | --- |
| cerebras, deepseek, groq, mistral, openrouter, zai | API inference: advise only | Tier 1: send the complete task |
| claude, codex, grok, gemini | advise, work, sysops, where that mode is already supported | Tier 2: workdir file pointer |
| vote | voters use advise runners in both rounds | Tier 2: workdir file pointer |
| native handoff, `--inherit` | modes admitted by the caller's capability contract | Tier 2, with byte validation before dry-run |
| opencode, kimi, qwen, exec, other targets | no proven file-read contract in the invoked runner | Tier 3: refuse above the transport limit |

This policy does not enable otherwise unsupported modes (for example, Grok
live still requires sysops; pure API work/sysops remain unsupported). The
OpenCode/Kimi/Qwen runner comments describe permission modes but do not prove
an explicit readable tool surface; their existing invocation is kept and an
over-limit task fails closed.

The named `MAX_TASK_BYTES = 1048576` in `scripts/lib/prompt_transport.py`
applies to all tiers. It bounds runaway input and per-job storage, **not** model
context. There is no local option override. Repository model research contains
dated context-window figures, but there is no authoritative per-request token
budget/tokenizer for arbitrary configured API slugs. The API provider remains
the context-length oracle: a context-length rejection returns exit 2 and one
clear line, without retrying with shortened text. A response counts as a
context rejection only when its error object has `code`
`context_length_exceeded` or a `type` containing `context`, or when the HTTP
status is 400 or 413 and the error text names a context or token limit. HTTP
429 is never a context rejection, whatever its wording. Other API errors
retain their existing exit behavior.

Transport ceilings preserve the existing runner budgets: Claude 102400,
Codex/Grok 140000, vote 100000; live first messages 102400 except Gemini.
Gemini is 180000 on non-Linux and 130000 on Linux, based on the existing agy
1.4.2 measurements. Linux single-argument transports also obey 131071 bytes
(131072 including NUL, the 4 KiB-page `MAX_ARG_STRLEN`). Worker rules count
inside the budget, including Gemini. Live follow-ups do not repeat the prefix.
`jobs send ID -` accepts stdin so large follow-ups need not occupy one argv.
A live job whose metadata lacks `vendor`, `mode` or `workdir` cannot stage: a
follow-up within the limit is sent inline as before, and a larger one is
refused with one `omnilane:` line (exit 2).

## Staging and lifecycle

Before job, thread or goal journal state, dispatch verifies a private file in
`<workdir>/.omnilane-tmp/job-<UUID>/task.txt`. Every job gets a different UUID;
directories are 0700 and files 0600. The pointer gives an absolute path, byte
count and SHA-256 and requires the worker to read and verify the entire brief
before acting, or stop and report inability to do so. No partial-read guessing.

A `.gitignore` containing `*` exists in every new per-job directory. When the
base itself is newly created, its own ignore rule also hides the ownership
marker. Existing `.omnilane-tmp` permissions, contents and visibility are
unchanged. Neither repository `.gitignore` nor `.git/info/exclude` is modified
by task staging. For a staged-task job, the existing Codex runtime-temp helper also uses its
own per-job ignore file and preserves the base permissions/exclude file.
Other Codex jobs keep their previous runtime-temp behavior.

Staging failures roll back only files/directories created by the attempted
stage, returning a tier 3 refusal. Goal dispatch first checks its stop
conditions (status, reservations, budgets, failure fuse) read-only and without
the lock; a goal that would stop skips staging and reports exit 75 as before.
Otherwise it prepares the stage before its lock/intent, passes a hash-verified
receipt to normal dispatch, and checks again under the lock. A normal
`--dry-run` never stages. It prints `task_tier`, `task_delivery`, `task_bytes`
and `task_sha256` only when the task would go by file; without those lines the
task goes inline and the preview output is unchanged from before. A native
plan likewise carries `task_transport` only for file delivery.

The job's `brief.json` (native: `task_transport` in `native.json`, file
delivery only) records tier, bytes, hash and path. Live follow-ups add unique
`brief-message-*.json` receipts. The original `task.txt` remains the historical
job record; `transport-task.txt` carries the pointer where needed.

* Single-shot and background files are removed on success, failure, timeout or
  cancellation when the controller finalizes the job. Cleanup checks inode
  ownership and preserves replacements/foreign files.
* A named thread keeps its file for that dispatch turn, then removes it even
  if the provider conversation persists. Follow-ups use already-read context;
  resend the full brief if a later turn needs to read it again. The pointer
  explicitly states this lifetime.
* Live first-task and follow-up files remain available throughout the resident
  job and are removed together at close/failure/timeout/cancel.
* Native files are removed when completion or cancellation is recorded. Native
  deadlines are caller-enforced: an observed expired state also cleans the file;
  there is no hidden background sweeper. The caller must enforce the deadline
  and record cancellation/completion. Whole-handoff JSON is still limited to
  262144 bytes, independently of the task-file ceiling.
* Only model opinions in vote round 2 may be shortened. If the original task
  plus framing/opinions exceeds the budget, the original becomes a file pointer.
  That file is staged only after round 1 has finished, so a workdir that cannot
  hold it fails the whole vote at that point, after round 1 has already run.
  This late failure is known and not fixed here; nothing is cut.

Normal cleanup removes the new empty per-job directory; the last job removes a
base created by this feature. A base that existed before staging is retained.
Creating and removing the base are serialized by an exclusive `flock` on the
workdir's own directory descriptor, so no lock file is created and removing
the base markers does not weaken the lock.

## Crash recovery and host acceptance

SIGKILL cannot execute an EXIT handler. Killing both dispatcher/controller and
worker can leave the staged file. Find it from `brief.json`, live message
receipts, native `task_transport`, or `.omnilane-tmp/job-*/receipt.json`.
The latter is a self-contained ownership receipt even if job publication was
interrupted. Inspect job/process state first; never clean a still-active job.
After confirming that the worker is terminal, the reusable command is:

```sh
python3 scripts/lib/prompt_transport.py cleanup /absolute/workdir/.omnilane-tmp/job-UUID/receipt.json
```

No automatic global scavenging or changes to pre-existing files are performed.
Files replaced by another writer are retained and require operator inspection.
The receipt's original directory/inode identity is required for removal.

Changed runner bytes (`run-openai-compat.sh`, which the six pure API vendors
run, and `run-vote.sh`) require fresh signed transport evidence on **each host**.
Offline fixtures prove construction and bytes, not provider filesystem access
or context capacity. Before rollout, re-probe the changed transports, run the
full suites on a host that permits process inspection/local test servers, and
perform one real over-limit dispatch per vendor and relevant supported mode.
