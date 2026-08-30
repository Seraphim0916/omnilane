# Omnilane Roadmap — Conversational Dispatch

Status: planning. Nothing in this document is implemented yet.

## 摘要（繁中）

- **R1 續談派工**：同一個 thread 可以續談，第二次派工只送新提示，不重送整份脈絡。
- **R2 活體信箱**：job 執行中可以收控制端訊息、同時把進度串流出來，不必砍掉重跑。這是省 token 的那一項——中途修正只花一句話。
- **證據狀態**：claude 兩項都已在本機實測通過；codex / grok / gemini 只查了 `--help`，行為未實測；kimi / qwen / opencode 本機未安裝，完全未驗；六家 openai-compat 走無狀態 HTTP，結構上沒有 session。
- **下一步**：先寫 R2（claude）任務書派工，R1 claude 段同批；codex / grok / gemini 要先補實測才排。

---

Today every dispatch is a dead end. `dispatch.sh` writes one prompt file, a runner
runs the vendor CLI once, the process exits, and the conversation is thrown away.
A follow-up question means re-sending the entire context as a new job.

Two capabilities close that gap:

- **R1 — Threaded dispatch**: a job can be continued later instead of restarted.
- **R2 — Live mailbox**: a job that is still running can receive a message from
  the controller and emit progress back, without being killed.

## Vendor capability matrix

Evidence status is deliberate. `VERIFIED` means it was executed on this machine.
`HELP-ONLY` means the flag appears in `--help` output but the behaviour was not
exercised. `UNVERIFIED` means the CLI is not installed locally and nothing was
checked.

| Vendor | Continuation primitives | Mid-run input | Evidence |
|---|---|---|---|
| claude | `--session-id <uuid>`, `-r/--resume`, `--fork-session`, `--no-session-persistence` | stdin NDJSON via `-p --input-format stream-json --output-format stream-json --verbose` | **VERIFIED** (below) |
| codex | `exec resume <id>` / `exec resume --last`, `exec fork`, interactive `-c/--continue`, `-s/--session-id`, `--restore-code`; servers: `mcp-server`, `app-server`, `exec-server`, `agents`, `remote-control` | unknown; `app-server`/`exec-server` are the candidate surface | HELP-ONLY |
| grok | session `list`/`search`/`delete`; `agent leader` shares one backend across multiple clients; `stdio`/`headless`/`serve` | unknown; `leader` is the candidate surface | HELP-ONLY |
| gemini (agy) | `-c/--continue`, `--conversation <id>`, `--prompt-interactive` | unknown | HELP-ONLY |
| kimi / qwen / opencode | — | — | UNVERIFIED: CLI not installed on this host |
| cerebras / deepseek / groq / mistral / openrouter / zai | none — stateless HTTP | none | **VERIFIED**: `run-openai-compat.sh:58` posts a single `{"role":"user"}` message per call; continuation can only be client-side replay |
| exec | caller-defined | caller-defined | N/A |

Two facts worth carrying forward:

- `run-grok.sh` passes `--no-memory`, actively disabling grok's own cross-session
  memory. Any grok threading work must decide whether that flag stays.
- `run-codex.sh` already extracts `thread_id` from the first progress event and
  locates `rollout-*-<thread_id>.jsonl`. Half of R1's codex plumbing exists.

### Verified claude evidence

Continuation across two separate processes: `claude -p --session-id <uuid>` was
told a passphrase; a second process with `claude -p --resume <same-uuid>`
recalled it.

Mid-run injection, one process, second message written **after** turn 1 had
already been answered: `docs/experiments/live-inbox-probe.sh` printed
`after-turn-1 results: 1` before writing the second message into the FIFO, and
the same process answered it. `rc=0`. This is the decisive result — it disproves
the earlier assumption that `claude -p` cannot accept input mid-run.

Gotcha: `-p` plus `--output-format stream-json` fails with
`Error: When using --print, --output-format=stream-json requires --verbose`.

## R1 — Threaded dispatch

Goal: `dispatch.sh --thread <name>` continues an earlier conversation with the
same vendor and model, sending only the new prompt.

Design constraints:

- The runner contract (`MODE WORKDIR MODEL EFFORT PROMPT_FILE OUTPUT_FILE`, with
  vendor resolved by `job-worker.sh`) is load-bearing. Thread state should arrive
  through environment variables (`OMNILANE_THREAD_ID`,
  `OMNILANE_THREAD_MODE=new|resume`) rather than positional arguments, so runners
  that ignore threading keep working unchanged.
- Thread metadata lives in a `thread.json` beside the job directory: vendor,
  model, vendor-native session id, turn count, last job id.
- Re-read the session id out of each run's own result event and write it back to
  `thread.json`. Do not assume the id supplied at creation survives — a vendor
  that forks a new id per resume would otherwise silently strand the thread after
  turn two.
- A thread is pinned to one vendor. Falling back to another vendor breaks the
  thread; the fallback must start a fresh thread and say so, never silently lose
  history.
- Stateless vendors (the six openai-compat ones) either refuse `--thread` or
  replay stored turns. Refusing is the honest default for the first cut.
- **Never degrade silently.** Whenever a thread cannot actually be continued —
  stateless vendor, fallback to a different vendor, expired session id — the user
  must see which mode the dispatch really ran in. A user who believes they are
  continuing a conversation while every turn restarts from zero burns tokens and
  cannot tell why the correction had no effect.

Phasing: claude first (verified), codex second (`exec resume` plus the existing
`thread_id` extraction), then grok/gemini once their help-only claims are
actually exercised.

## R2 — Live mailbox

Goal: a running job exposes an inbox; the controller writes a message into it and
the worker picks it up on its next turn. The same job streams progress out.

Shape:

- `<job_dir>/inbox.fifo` — NDJSON, one message per line, mode `0600`.
- `<job_dir>/events.jsonl` — the vendor's stream-json output, already partially
  precedented by `out.txt.progress.log` in the codex runner.
- `jobs.sh send <id> <text>` appends one message; `jobs.sh watch <id>` tails
  events; `jobs.sh close <id>` ends the thread.

**The write fd has a named holder.** A FIFO reader sees EOF the moment the last
writer closes, so a `send` that opens, writes and closes would terminate the
worker's session instead of continuing it. `job-worker.sh` therefore opens a
write descriptor on `inbox.fifo` when the job starts and holds it for the job's
whole lifetime; `jobs.sh send` only ever writes alongside that holder, and
`jobs.sh close` releasing the holder is the explicit end-of-conversation signal.
This is exactly the structure the probe verified (`exec 3>` held across both
messages, `exec 3>&-` to finish).

**Closing the inbox does not end the worker.** Verified: after the write end is
closed, `claude -p --input-format stream-json` keeps running rather than exiting
on stdin EOF — it stays alive and is even listed as a live session. So
`jobs.sh close` must terminate the worker explicitly, and job completion must be
read from result events in `events.jsonl`, never inferred from process exit. A
supervisor that waits on the process instead will hang forever.

Only vendors with a verified live-input surface get an inbox. Claude qualifies
today. Codex's `app-server`/`exec-server` and grok's `agent leader` are the next
candidates and must be tested before anything is built on them.

Open risks:

- Opening a FIFO for write blocks until a reader exists, and writes block when
  the pipe is full. `jobs.sh send` needs a timeout and a clear failure when the
  worker is gone.
- The holder descriptor must not outlive the supervised job or leak into any
  child process, or the worker will hang waiting for input that never comes.
- Injected text is controller-authored, but a mailbox is still a new input path
  into a running agent. It must never be fed from tool output automatically.

## Acceptance

R1 is done when, for each supported vendor, **three** consecutive dispatches on
one thread show turn 3 recalling a fact stated only in turn 1, with the job ids
and the vendor session id recorded at every turn. Two turns is not enough — it
cannot distinguish a stable session id from one that forks on each resume.

R2 is done when a running background job receives a message sent after its first
turn completed and demonstrably acts on it, with `events.jsonl` as evidence.

Both are additionally gated on the degradation notice being visible in normal
use: a dispatch that fell back to stateless re-send must say so in its own
output, not only in a log a user would have to know to open.

Anything below that bar is `PARTIAL`, per `docs/goal-1.0.md`.
