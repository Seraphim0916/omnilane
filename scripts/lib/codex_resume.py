"""Bounded Codex transport recovery bookkeeping; never launches a provider.

Only the final structured error can qualify. See docs/codex-transport-recovery.md
for the observed CLI envelopes and why generic stream errors are not retryable.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from refusal_evidence import sanitise_human_line
import job_marker

CONTINUE_PROMPT = (
    "Continue the interrupted turn in this original session using its recorded tool results. "
    "Do not repeat completed operations. If an operation's outcome is unknown, stop and "
    "report the uncertainty for an operator decision. Keep the original scope and permissions.\n"
)
INTERRUPTED_EXIT = 75
TRANSPORT = re.compile(r"Incomplete response returned, reason: (upstream_eof|upstream_error)")
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+")
STATE_NAME = "codex-resume.json"
PUBLIC_KEYS = ("attempt", "reason_class", "transport_reason", "elapsed_seconds")


def eligible_exit(code):
    # Usage/refusal, watchdog, setup and signal exits take precedence over logs.
    return type(code) is int and 0 < code < 124 and code != 2


def open_regular(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular artifact")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def progress_evidence(path, exit_code, output=None):
    """Keep definitive completion evidence distinct from provisional messages."""
    last, completed, final_reply, reliable = None, False, False, True
    open_turns = 0
    sessions = []
    try:
        with open_regular(path) as source:
            while True:
                line = source.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    # An unreadable line may be the next turn's activity.
                    last, reliable, completed = None, False, False
                    while line and not line.endswith(b"\n"):
                        line = source.readline(1024 * 1024 + 1)
                    continue
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, RecursionError):
                    last, reliable, completed = None, False, False
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "thread.started":
                    sessions.append(event.get("thread_id"))
                elif kind == "error":
                    last = event.get("message")
                elif kind == "turn.failed":
                    error = event.get("error")
                    last = error.get("message") if isinstance(error, dict) else None
                    open_turns = max(0, open_turns - 1)
                elif kind == "turn.started":
                    last, completed = None, False
                    open_turns += 1
                elif kind == "turn.completed":
                    # A late completion of an older turn, or a turn that was
                    # abandoned, leaves a turn open: that is not completion.
                    last = None
                    open_turns = max(0, open_turns - 1)
                    completed = open_turns == 0
                elif isinstance(kind, str) and kind.startswith("item."):
                    # Later item activity cannot safely be attributed to the
                    # completed turn. Require completion for this activity,
                    # even if its turn.started event is missing.
                    completed = False
                # An agent_message item may be progress before another tool or
                # turn. Its text/type alone cannot distinguish a final answer,
                # even when EOF hides the later activity. Only turn.completed
                # or the final reply artifact below establishes completion.
    except (OSError, ValueError):
        reliable = False
    if output is not None:
        try:
            with open_regular(output) as source:
                # A non-empty final artifact is sufficient to block continuation;
                # do not pull its private text into bookkeeping or notices.
                final_reply = final_reply or bool(source.read(1))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            reliable = False
    match = TRANSPORT.fullmatch(last) if isinstance(last, str) and eligible_exit(exit_code) else None
    session = None
    if reliable and len(sessions) == 1 and isinstance(sessions[0], str):
        try:
            if str(uuid.UUID(sessions[0])) == sessions[0]:
                session = sessions[0]
        except ValueError:
            pass
    reason = match.group(1) if match else None
    return dict(transport_reason=reason, completion_observed=bool(completed or final_reply),
                continuation_eligible=bool(reason and reliable and not completed and not final_reply),
                session_id=session)


def classify_progress(path, exit_code):
    return progress_evidence(path, exit_code)["transport_reason"]


def progress_identity(path):
    with open_regular(path) as source:
        info = os.fstat(source.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return dict(device=info.st_dev, inode=info.st_ino, mtime_ns=info.st_mtime_ns,
                sha256=digest.hexdigest())


def job_binding(job):
    metadata = read_json(job / "meta.json")
    selected = {key: metadata.get(key) for key in
                ("vendor", "mode", "workdir", "model", "effort", "started", "session_mode")}
    selected["job_directory"] = str(job.resolve())
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def read_json(path):
    with open_regular(path) as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise ValueError("oversized metadata")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid metadata")
    return value


def atomic_bytes(path, data):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("unsafe artifact path")
    fd, temporary = tempfile.mkstemp(prefix=".codex-resume-", dir=str(path.parent))
    # Keep a failed temporary for inspection; no cleanup of foreign paths.
    with os.fdopen(fd, "wb") as target:
        target.write(data)
    os.replace(temporary, str(path))


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, separators=(",", ":")) + "\n").encode())


def public_fields(state):
    attempts = state["attempts"]
    if not isinstance(attempts, list) or len(attempts) != 1:
        raise ValueError("invalid attempts")
    cleaned = []
    for index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, dict) or attempt.get("attempt") != index:
            raise ValueError("invalid attempt number")
        code = attempt.get("exit_code")
        if code is not None and (type(code) is not int or not 0 <= code <= 255):
            raise ValueError("invalid attempt exit")
        reason = attempt.get("transport_reason")
        if reason not in (None, "upstream_eof", "upstream_error"):
            raise ValueError("invalid transport reason")
        if reason and not eligible_exit(code):
            raise ValueError("invalid transport exit")
        elapsed = attempt.get("elapsed_seconds")
        if type(elapsed) is not int or elapsed < 0:
            raise ValueError("invalid elapsed time")
        cleaned.append(dict(attempt=index, exit_code=code,
                            reason_class="transport-interrupted" if reason else None,
                            transport_reason=reason, elapsed_seconds=elapsed,
                            completion_observed=bool(attempt.get("completion_observed")),
                            auto_resume_eligible=False))
    elapsed = state["elapsed_seconds"]
    if type(elapsed) is not int or elapsed < 0:
        raise ValueError("invalid job elapsed time")
    last = cleaned[-1]
    reason = None if state.get("terminal_override") else last["transport_reason"]
    outcome = None
    if reason:
        outcome = ("completed-with-transport-error" if last["completion_observed"]
                   else "interrupted-needs-decision")
    blocked = state.get("continuation_blocked_reason")
    if blocked not in (None, "mode-can-change-files", "automatic-resume-disabled", "completion-observed"):
        raise ValueError("invalid continuation decision")
    return dict(attempt=len(cleaned), reason_class="transport-interrupted" if reason else None,
                transport_reason=reason, elapsed_seconds=elapsed, attempts=cleaned,
                outcome=outcome, auto_resume_eligible=False,
                completion_observed=last["completion_observed"], continuation_blocked_reason=blocked)


def save(job, state):
    fields = public_fields(state)
    atomic_json(job / STATE_NAME, state)
    metadata = read_json(job / "meta.json")
    metadata.update({key: fields[key] for key in PUBLIC_KEYS})
    atomic_json(job / "meta.json", metadata)


def start(job, attempt):
    if attempt != 1 or (job / STATE_NAME).exists() or (job / STATE_NAME).is_symlink():
        raise ValueError("one invocation per dispatch; use manual continuation")
    now = time.monotonic()
    marker = os.environ.get("OMNILANE_JOB_TOKEN") or secrets.token_hex(16)
    if not re.fullmatch(r"[a-f0-9]{32}", marker):
        raise ValueError("invalid job marker")
    state = dict(started=now, started_ns=time.time_ns(), attempt_started=now,
                 attempts=[dict(attempt=1, exit_code=None, transport_reason=None, elapsed_seconds=0)],
                 elapsed_seconds=0, job_binding=job_binding(job), process_marker=marker,
                 persistent_session=bool(os.environ.get("OMNILANE_THREAD_MODE")) or
                     read_json(job / "meta.json").get("mode") in ("work", "sysops"))
    save(job, state)
    return marker


def end(job, code, final=False):
    state = read_json(job / STATE_NAME)
    public_fields(state)
    last = state["attempts"][-1]
    now = time.monotonic()
    if final:
        # The supervisor's timeout/cancellation or a recheck refusal outranks
        # an earlier transport error, even if the runner never returned.
        if last["exit_code"] is not None:
            if (last["exit_code"] != code and not (code == INTERRUPTED_EXIT and
                    public_fields(state)["outcome"] == "interrupted-needs-decision")) or not eligible_exit(code):
                state["terminal_override"] = True
            state["elapsed_seconds"] = max(0, int(now - state["started"]))
            save(job, state)
            return public_fields(state)
        reason = None
    else:
        evidence = progress_evidence(job / "out.txt.progress.log", code, job / "out.txt")
        reason = evidence.pop("transport_reason")
        last.update(evidence)
        try:
            last["progress_identity"] = progress_identity(job / "out.txt.progress.log")
        except (OSError, ValueError):
            last["session_id"] = None
        mode = read_json(job / "meta.json").get("mode")
        state["continuation_blocked_reason"] = (
            "completion-observed" if evidence["completion_observed"] else
            "mode-can-change-files" if mode in ("work", "sysops") else "automatic-resume-disabled"
        ) if reason else None
    last.update(exit_code=code, transport_reason=reason,
                elapsed_seconds=max(0, int(now - state["attempt_started"])))
    state["elapsed_seconds"] = max(0, int(now - state["started"]))
    save(job, state)
    return public_fields(state)


def manual_session(job):
    """The explicit command asserts safety; inspect this job's marker, not host churn."""
    state = read_json(job / STATE_NAME)
    fields = public_fields(state)
    first = state["attempts"][0]
    metadata = read_json(job / "meta.json")
    observed = progress_evidence(job / "out.txt.progress.log", first.get("exit_code"), job / "out.txt")
    identity = progress_identity(job / "out.txt.progress.log")
    with open_regular(job / "exit") as source:
        code = source.read(5)
    if (code not in (b"75", b"75\n") or fields["outcome"] != "interrupted-needs-decision"
            or not state.get("persistent_session") or not first.get("continuation_eligible")
            or not first.get("session_id") or job_binding(job) != state["job_binding"]
            or observed["session_id"] != first["session_id"] or not observed["continuation_eligible"]
            or metadata.get("vendor") != "codex" or metadata.get("session_mode") != "single-shot"
            or identity != first.get("progress_identity") or identity["mtime_ns"] < state["started_ns"]):
        raise ValueError("original session unavailable or changed")
    marker = state.get("process_marker")
    if not isinstance(marker, str) or not re.fullmatch(r"[a-f0-9]{32}", marker):
        raise ValueError("job marker unavailable")
    # Honour inspection denial before touching the platform process APIs.
    check = subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid="],
                           capture_output=True, text=True, timeout=2)
    if check.returncode or check.stdout.strip() != str(os.getpid()):
        raise job_marker.Unavailable("process inspection denied")
    remaining = job_marker.live_matches(marker)
    if remaining:
        for pid, started in remaining:
            print("omnilane: job marker still alive: PID=%d START_US=%d" % (pid, started), file=sys.stderr)
        raise ValueError("job marker still alive; nothing started")
    support = subprocess.run([os.environ.get("CODEX_BIN", "codex"), "exec", "resume", "--help"],
                             capture_output=True, text=True, timeout=3)
    if support.returncode or "codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]" not in support.stdout:
        raise ValueError("original-session continuation unsupported")
    return first["session_id"]


def human_line(job_id, reason, elapsed, mode="advise", outcome="interrupted-needs-decision"):
    job_id = (job_id if isinstance(job_id, str) and len(job_id) <= 64
              and JOB_ID.fullmatch(job_id) else "unknown")
    reason = reason if reason in ("upstream_eof", "upstream_error") else "transport-interrupted"
    elapsed = elapsed if type(elapsed) is int and elapsed >= 0 else 0
    action = "result" if outcome == "completed-with-transport-error" else "resume"
    if outcome == "completed-with-transport-error":
        decision = "completion evidence received with a contradictory exit; available output retained; no continuation attempted."
    elif mode in ("work", "sysops"):
        decision = "interrupted, needs decision; the task itself did not fail; nothing was restarted automatically because this mode can change files."
    else:
        decision = "interrupted, needs decision; the task itself did not fail; nothing was restarted automatically because automatic continuation is disabled."
    if action == "resume":
        decision += " Running resume is the operator's statement that the earlier writer has stopped and continuing after its partial effects is safe."
    line = sanitise_human_line(
        f"omnilane: job JOBID was cut in transit ({reason}) after {elapsed} s; "
        f"{decision} Next: omnilane jobs {action} JOBID")
    return line.replace("JOBID", job_id)


def job_line(job, fields):
    mode = read_json(job / "meta.json").get("mode")
    return human_line(job.name, fields["transport_reason"], fields["elapsed_seconds"], mode, fields["outcome"])


def fields_for_exit(fields, code):
    """A published cancellation/timeout outranks stale worker bookkeeping."""
    expected = code == INTERRUPTED_EXIT and fields["outcome"] == "interrupted-needs-decision"
    if not eligible_exit(code) or (fields["attempts"][-1]["exit_code"] != code and not expected):
        fields = dict(fields, reason_class=None, transport_reason=None, outcome=None, auto_resume_eligible=False)
    return fields


def main(argv):
    command = argv[0]
    if command == "continue-prompt":
        print(CONTINUE_PROMPT, end="")
        return 0
    job = Path(argv[1])
    if command == "start":
        print(start(job, int(argv[2])))
    elif command in ("end", "finalize"):
        if command == "finalize" and not (job / STATE_NAME).exists():
            return 0
        fields = end(job, int(argv[2]), final=command == "finalize")
        if fields["reason_class"]:
            print(fields["transport_reason"] if command == "end" else
                  job_line(job, fields))
    elif command == "manual-session":
        print(manual_session(job))
    elif command == "result-exit":
        fields = public_fields(read_json(job / STATE_NAME))
        print(INTERRUPTED_EXIT if fields["outcome"] == "interrupted-needs-decision" else int(argv[2]))
    elif command in ("fields", "receipt", "human"):
        if not (job / STATE_NAME).exists():
            if command == "fields":
                print("{}")
            return 0
        fields = public_fields(read_json(job / STATE_NAME))
        if command != "receipt":
            try:
                with open_regular(job / "exit") as source:
                    recorded_exit = source.read(5).decode("ascii")
                if re.fullmatch(r"(?:0|[1-9][0-9]{0,2})\n?", recorded_exit):
                    fields = fields_for_exit(fields, int(recorded_exit))
            except (OSError, ValueError):
                pass
        if command == "fields":
            print(json.dumps(fields, separators=(",", ":")))
        elif command == "human":
            if fields["reason_class"]:
                print(job_line(job, fields))
        else:
            path = Path(argv[2])
            receipt = read_json(path)
            fields = fields_for_exit(fields, receipt.get("exit"))
            receipt.update(fields)
            if fields["reason_class"]:
                receipt["tail"] = job_line(job, fields)
            atomic_json(path, receipt)
    else:
        raise ValueError("unknown command")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError,
            subprocess.TimeoutExpired, job_marker.Unavailable) as error:
        # Never echo artifact contents or paths into a public diagnostic.
        print("omnilane: Codex continuation unavailable; nothing started (" + type(error).__name__ + ")", file=sys.stderr)
        sys.exit(1)
