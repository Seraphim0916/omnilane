# Omnilane Live UI Design

**Date:** 2026-07-14
**Branch:** `codex/live-ui` (stacked on `codex/natural-language-dispatch`)
**Status:** Approved for implementation

## Outcome

Add an optional, local-only, read-only web UI that shows omnilane dispatch jobs
as they run and keeps their existing on-disk history browsable. Natural-language
interpretation and dispatch remain in the Agent Skill and CLI. The browser is an
observer, not a second control plane.

The page has one job: let an operator answer, at a glance, “what route was
chosen, is it still running, and what public result did it produce?”

## Evidence and clean-room boundary

The public Toolsai/Grok-Build-Connector repository was inspected at commit
`e9ee0527920300d49d9d360a3cc23028c715feb7`. Its Live UI is a standard local
web application: Python `ThreadingHTTPServer`, a loopback listener, a random
token, Server-Sent Events (SSE), SQLite history, and a static HTML/JavaScript
client.

That repository has no LICENSE or other grant file at the inspected commit.
Omnilane will therefore copy no source, markup, styling, names, or assets from
it. This design uses only general architecture ideas and is implemented from
omnilane's own data model and requirements.

## Approaches considered

### A. File-backed read-only job observer (recommended)

Read `~/.omnilane/jobs/*`, serve a local static page, and stream safe job
snapshots. Keep dispatch, deletion, cancellation, and configuration in the CLI.

- Smallest security surface.
- No duplicate database or event-emission changes in every runner.
- Existing job history remains the source of truth.
- “Live” means status and safe output-file changes, not hidden reasoning or raw
  vendor tool logs.

### B. Browser dispatch dashboard

Add a form that accepts natural language and launches `dispatch.sh`, plus
cancel/retry/delete actions.

- More convenient, but creates a network-facing command execution boundary.
- Requires permission prompts, argument construction, workdir authorization,
  CSRF defenses, cancellation semantics, and audit logging.
- Duplicates the approved Agent Skill routing path.

### C. Conversation/event platform

Modify every runner to emit token-level events and store conversations in a new
SQLite database.

- Closest to a chat transcript.
- Duplicates existing job history, complicates migrations, and risks exposing
  tool traces, hidden reasoning, credentials, or partial unsafe output.
- Highest maintenance cost for little benefit to omnilane's routing mission.

**Decision:** Build A. B and C are explicit non-goals for this branch.

## Product scope

### Included

- Start, inspect, get the URL for, and stop one optional Live UI server.
- Bind only to `127.0.0.1`; choose another local port when the default is busy.
- List the newest 50 jobs and search/filter them client-side.
- Show route facts from `meta.json`: lane, vendor, model, effort, mode,
  watchdog timeout, candidate position, workdir, and start time.
- Show state derived from `exit` and `pid`: starting, running, succeeded,
  failed, dead, or invalid.
- Show `task.txt` and the public `out.txt` result as plain text.
- Update the list, selected job, state, and public output while files change.
- Work on desktop and mobile; keyboard focus and reduced-motion are required.
- Empty, unauthorized, stale-server, malformed-job, truncated-output, and
  disconnected states have explicit messages.

### Excluded

- Dispatching, retrying, cancelling, killing, deleting, or editing jobs in the
  browser.
- Editing routing or local configuration in the browser.
- Automatic browser opening or automatic server startup on every dispatch.
- SQLite, a second history store, or migration of existing jobs.
- Displaying `worker.log`, `*.stderr.log`, `*.progress.log`, environment
  variables, credentials, raw tool events, or hidden reasoning.
- Rendering model output as HTML or executing links/scripts from model output.
- Binding to LAN addresses, adding remote access, TLS, accounts, or cloud sync.
- Copying Grok-Build-Connector code or its visual design.

## Canonical data model

The server reads but never modifies `$OMNILANE_HOME/jobs`.

| File | UI use | Rule |
|---|---|---|
| `meta.json` | route facts | Parse at most 64 KiB as an object; malformed or oversized data marks the job invalid without crashing the list. |
| `task.txt` | operator request | UTF-8 with replacement for invalid bytes; plain text only. |
| `pid` | liveness hint | Read at most 64 bytes; positive decimal in the platform PID range only. Use `os.kill(pid, 0)` and report `dead` if absent. PID reuse remains the same limitation as `jobs.sh`. |
| `exit` | terminal state | Read at most 64 bytes; signed platform integer only. Zero is succeeded, nonzero is failed. |
| `out.txt` | public result | Plain text only; it may appear incrementally for panel jobs. |

All other files are ignored. A job ID must match
`^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$`; API input never becomes an arbitrary
filesystem path.

The job directory and every allow-listed file are opened without following
symlinks (`O_NOFOLLOW` where available), and must be a directory or regular
file respectively. A symlink or resolved path outside the canonical jobs root
is invalid, never served. The current runners create one canonical job
directory per dispatch; vote-panel temporary files stay outside `jobs`, and
the panel's public aggregate is the canonical `out.txt`.

At most 512 KiB is returned for `task.txt` or `out.txt`. A larger file returns
the first 384 KiB, an explicit truncation marker, and as much of the final 128
KiB as fits inside that total cap. The response includes `truncated: true`.

## Architecture

### Components

1. `scripts/ui.py`
   - Python 3.9+ standard library only.
   - Public commands: `start`, `status`, `url`, `stop`.
   - Private `serve` command runs the HTTP server.
   - Owns state-file permissions, process lifecycle, token generation, startup
     health checks, and stale-state recovery.
2. `ui/index.html`, `ui/styles.css`, `ui/app.js`
   - No build step, package manager, CDN, remote font, analytics, or external
     network request.
3. `bin/omnilane`
   - Adds `omnilane ui start|status|url|stop` and help text.
4. `skills/omnilane/SKILL.md` and localized READMEs
   - Explain that Live UI is optional, local, read-only, and manually started.
5. `tests/test_ui.py` plus existing shell tests
   - Exercise API, lifecycle, authentication, filesystem parsing, and CLI
     wiring with a temporary `OMNILANE_HOME`.

### Optional dependency

Core routing remains pure Bash. Only `omnilane ui ...` requires `python3`
version 3.9 or newer. Missing or older Python produces a readable nonzero error
and leaves core dispatch unaffected.

### State and lifecycle

UI runtime state lives under `$OMNILANE_HOME/ui`:

- directory mode `0700`;
- `lifecycle.lock` mode `0600`, held with an exclusive cross-process lock for
  the full `start` decision/spawn/health-check sequence and for `stop`;
- `state.json` mode `0600`, containing PID, bound port, random token, API
  version, and start timestamp;
- `server.log` mode `0600`, containing server lifecycle errors only, never
  requests, task text, output, token, or headers.

`start` behavior:

1. If authenticated health succeeds for the recorded state, reuse it.
2. If state is stale, remove only the stale state file; do not signal an
   unverified PID.
3. Generate a 256-bit token with `secrets.token_urlsafe` and write initial state
   atomically.
4. Spawn `serve` in a new session with stdin closed and logs redirected.
5. Bind requested port (default 8765); if it is busy, bind port 0 and record the
   actual loopback port.
6. Wait up to six seconds for authenticated health. On failure, terminate only
   the child just spawned and report the log path.
7. Print a clickable URL and whether the server was reused.

A concurrent `start` waits on the lifecycle lock, then re-checks authenticated
health. It reuses the winner and never spawns a second server. Runtime files
must be regular, non-symlink files; unsafe pre-existing paths fail closed.

The token is read from the protected state file, not passed in the process
argument list. `start` and `url` are the only commands that print the
secret-bearing link; `status` reports lifecycle facts without the token or URL.

`stop` sends SIGTERM only when two immediately consecutive authenticated health
checks agree on the recorded server identity, PID, and port. A stale state file
is cleared without killing a possibly reused PID. The server removes state on
exit only when its own identity still matches the recorded state.

## HTTP contract

The server is a `ThreadingHTTPServer` bound to `127.0.0.1` only. It accepts only
`Host: 127.0.0.1:<bound-port>` and returns 421 for other Host values.

Static routes:

- `GET /` -> `index.html`
- `GET /styles.css`
- `GET /app.js`

Static assets contain no job data and may load without authentication. Every
`/api/` route requires the random token. Fetch requests use
`Authorization: Bearer <token>`. The SSE endpoint may also accept the token as
the `token` query parameter because browser `EventSource` cannot set headers.
Token comparison uses `hmac.compare_digest`.

API routes:

- `GET /api/health` -> `{ok, apiVersion, pid, port}`
- `GET /api/jobs` -> `{ok, jobs:[summary...]}` newest first, maximum 50. A
  summary contains only job ID, route metadata, state, exit code, and
  allow-listed file size/mtime signals; it contains no task or output text.
- `GET /api/jobs/<id>` -> `{ok, job:{summary, task, output, truncation...}}`
- `GET /api/events` -> SSE `snapshot` events when the canonical job snapshot
  changes, plus a keepalive comment at least every 15 seconds

There are no POST, PUT, PATCH, or DELETE routes. Unsupported methods return 405.
Unknown static/API paths return 404. Request bodies are never accepted.

Every response includes:

- `Cache-Control: no-store`
- `Content-Security-Policy: default-src 'none'; connect-src 'self'; script-src
  'self'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none';
  frame-ancestors 'none'`
- `Referrer-Policy: no-referrer`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`

The start URL carries the token in the fragment, not the query:
`http://127.0.0.1:<port>/#token=<token>`. JavaScript moves it into
`sessionStorage` and removes the fragment with `history.replaceState`. The
token appears in the SSE request query only; request logging is disabled and
the page loads no third-party resources.

## Live update behavior

One server-owned broadcaster computes a small signature once per second from
the newest 50 job directories and the size, nanosecond mtime/ctime, and inode of
only the allow-listed files. It publishes the cached snapshot to all SSE
clients; clients do not each rescan the filesystem. At most eight SSE clients
may be connected at once; excess connections receive 503. Each accepted client
receives:

1. one immediate `snapshot` event;
2. a new snapshot when the signature changes, checked once per second;
3. a keepalive comment when nothing changes for 15 seconds.

The selected job detail is fetched separately after a snapshot says its state
or output changed. This avoids sending every task and result on every tick.

SSE disconnects do not affect jobs. Browser `EventSource` reconnects
automatically; the page displays `Reconnecting` until a new event arrives.

## Visual design

### Subject and audience

- Subject: a model-routing signal board, not a chat application.
- Audience: a developer/operator monitoring which model received a task and
  whether the evidence came back.
- Single page job: inspect route, state, request, and public result.

### Direction: “dispatcher's signal board”

Use a quiet, light industrial surface rather than the reference project's dark
neon chat aesthetic. The UI should feel like a physical routing desk: typed
labels, clear signal colors, and a visible path from lane to vendor to model to
outcome.

Color tokens:

- `--ink: #142027` — primary text and track line
- `--board: #E8ECE9` — mineral-gray page
- `--panel: #F8F7F2` — raised information surface
- `--steel: #63727B` — metadata and inactive track
- `--route: #238F8A` — active route and success
- `--signal: #D99A2B` / `--fault: #C75B50` — running and failed state

Typography uses only local fonts:

- Display/route identity: `Avenir Next Condensed`, `Arial Narrow`, sans-serif.
- Reading: `Avenir Next`, `Segoe UI`, system-ui, sans-serif.
- IDs/data: `SFMono-Regular`, `Menlo`, `Consolas`, monospace.

### Signature element

The selected job begins with one horizontal route track:

`LANE -> VENDOR -> MODEL -> STATE`

Each station is real metadata, not decoration. While running, one amber signal
moves only along this track; when reduced motion is requested it becomes a
static amber marker. Success turns the final station teal; failure turns only
the final station red.

### Desktop wireframe

```text
+---------------------------------------------------------------+
| OMNILANE / LIVE BOARD               12 jobs       LIVE signal |
+----------------------+----------------------------------------+
| Search jobs          |  JOB 20260714-...        RUNNING       |
| [all][run][done]     |  lane -- vendor -- model -- state      |
|                      +----------------------------------------+
| recent job card      |  Request                               |
| recent job card      |  plain text                            |
| recent job card      +----------------------------------------+
| ...                  |  Public result                         |
|                      |  plain text / waiting state            |
+----------------------+----------------------------------------+
```

### Mobile wireframe

```text
+-----------------------------+
| OMNILANE       LIVE signal  |
| Search / status filter      |
| [selected job dropdown]     |
+-----------------------------+
| lane-vendor-model-state     |
| Request                     |
| Public result               |
+-----------------------------+
```

No gradient, glassmorphism, fake charts, fabricated progress percentage, or
decorative model avatars. Motion is limited to the one route signal and list
state transitions.

## Error and empty states

- No jobs: “No dispatches yet. Run an omnilane task; this board will update
  automatically.”
- Missing/expired token: “This Live UI link is not authorized. Run
  `omnilane ui url` for a fresh local link.”
- Server disconnected: preserve the last snapshot and show “Reconnecting to
  the local job board.”
- Malformed job: keep it in the list as `invalid` with the job ID; never render
  raw parse errors or skip the rest of the list.
- Truncated content: show a visible “Large content shortened to 512 KiB” marker.
- Dead job: explain that the worker is gone and no exit code was recorded.

## Security and privacy invariants

- Loopback-only listener; no `0.0.0.0`, IPv6 wildcard, or configurable bind
  address.
- No CORS headers and strict Host validation.
- High-entropy per-server token; protected state directory/file; constant-time
  comparison.
- Read-only HTTP contract; no shell execution or subprocess launch from a
  request handler.
- Job IDs are allow-listed; resolved paths must remain below the canonical jobs
  directory.
- File names are fixed; file sizes are capped; malformed UTF-8 and JSON cannot
  crash the server.
- Runtime state/log files and job files are opened without following symlinks;
  oversized metadata and integer control files fail closed before parsing.
- Browser rendering uses `textContent`/text nodes only. No `innerHTML`,
  `insertAdjacentHTML`, `eval`, inline script, or remote resource.
- Raw logs and environment data are never served.
- The UI does display the operator's task and public model answer. Documentation
  states this clearly; users should stop the server when they do not want a
  local browser surface.

## Tests and acceptance criteria

### Unit/integration tests

Using a temporary `OMNILANE_HOME` and synthetic jobs:

1. Valid running, succeeded, failed, dead, malformed, and oversized jobs parse
   without touching real user history.
2. Job order is newest first and capped at 50.
3. Traversal-like and malformed IDs return 404 and never read outside `jobs`.
4. Static assets load, but every API returns 401 without the token.
5. Wrong Host returns 421 even with a valid token.
6. Correct bearer token lists jobs and returns selected detail. A canary string
   present only in task/output bodies is absent from the `/api/jobs` response.
7. SSE sends an initial snapshot and a changed snapshot after a fixture file
   update; the stream never includes task/output bodies.
8. A ninth SSE connection receives 503, while one append causes only one
   server-side snapshot recomputation and all accepted clients see the update.
9. Unsupported methods return 405; no mutation endpoint exists. Static, API,
   SSE, 401, 404, 405, and 421 responses carry every required security header.
10. Start chooses a fallback port, writes `0700`/`0600` state, reports healthy,
   reuses a healthy server, and stops only the authenticated recorded process.
   Two concurrent starts produce one server identity and one live listener.
11. Stale PID state is cleared without signalling that PID.
12. After an authenticated SSE request, `server.log` contains neither the
   token, `?token=`, request lines, task text, nor output text.
13. Symlinked job directories/files are invalid and never return the target;
   oversized `meta.json`, `pid`, and `exit` files fail closed without large
   reads or uncaught exceptions.
14. HTML/JS use no external URLs, `innerHTML`, eval-like execution, or inline
   script; untrusted fixture strings appear only as JSON/text.
15. Existing `bash tests/run.sh` stays green.

### Manual smoke

1. Start with `OMNILANE_HOME` pointing to fixtures.
2. Open the printed URL in a browser.
3. Confirm empty and populated responsive layouts.
4. Add/update `out.txt` and `exit`; confirm the page changes without reload.
5. Test keyboard navigation and a narrow mobile viewport.
6. Enable reduced motion; confirm the route signal stops moving.
7. Stop the server; confirm the old URL retains no data and status reports
   stopped.

### Completion gate

- Python tests, existing shell tests, Bash syntax, ShellCheck, and branch diff
  checks pass.
- A real loopback listener, authenticated health request, API request, SSE
  update, browser screenshot, and stop/no-listener check are captured.
- No P0/P1 finding remains after independent adversarial review.

## Deferred work

If a later branch adds browser-side dispatch or job deletion, it requires a new
threat model and explicit user approval. It must not be smuggled into this
read-only observer.
