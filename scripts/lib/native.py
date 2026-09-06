#!/usr/bin/env python3
"""Caller-owned native handoff protocol. No provider discovery or agent spawning.

The shell resolves a routing row before invoking this module. Exit 10 from
route means auto chose CLI; all other errors fail closed. Native state is a
single atomically replaced JSON record, serialized by a per-job advisory lock.
"""

import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_policy  # noqa: E402


MAX_BYTES = 262144
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def text_value(value, label, limit=4096, multiline=False):
    check(isinstance(value, str) and bool(value.strip()) and len(value) <= limit,
          f"invalid {label}")
    check(all(ord(c) >= 32 or (multiline and c in "\n\t") for c in value)
          and "\x7f" not in value, f"control character in {label}")
    return value


def identifier(value, label):
    text_value(value, label, 256)
    check(IDENTIFIER.fullmatch(value), f"invalid {label}")
    return value


def fields(value, required, optional=()):
    check(isinstance(value, dict), "expected JSON object")
    check(set(required) <= value.keys() and value.keys() <= set(required) | set(optional),
          "missing or unknown JSON fields")


def string_list(value, label, nonempty=True):
    check(isinstance(value, list) and len(value) <= 128 and (value or not nonempty),
          f"invalid {label}")
    for item in value:
        text_value(item, label)
    check(len(set(value)) == len(value), f"duplicate {label}")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    # Never follow a submitted symlink (context, completion, or job state).
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        check(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES, "invalid JSON file")
        raw = stream.read(MAX_BYTES + 1)
    check(len(raw.encode("utf-8")) <= MAX_BYTES, "JSON file too large")
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON number")))


def canonical_dir(value):
    text_value(value, "workdir")
    path = Path(value)
    check(path.is_absolute() and path.is_dir(), "workdir must be an existing absolute directory")
    return str(path.resolve(strict=True))


def capability_context(path):
    ctx = read_json(path)
    fields(ctx, ("schema_version", "harness", "vendor", "capabilities", "requirements"),
           ("current_model", "current_effort", "agent_strategy", "existing_agent",
            "preserve_existing_context", "new_agent_capacity"))
    check(type(ctx["schema_version"]) is int and ctx["schema_version"] == 1, "unsupported context version")
    identifier(ctx["harness"], "harness")
    identifier(ctx["vendor"], "vendor")
    if "current_model" in ctx:
        text_value(ctx["current_model"], "current_model", 256)
    if "current_effort" in ctx:
        text_value(ctx["current_effort"], "current_effort", 256)
    check(ctx.get("agent_strategy", "new") in ("new", "reuse"), "invalid agent strategy")
    check(ctx.get("new_agent_capacity", "unknown") in ("available", "exhausted", "unknown"),
          "invalid new agent capacity")
    if "preserve_existing_context" in ctx:
        check(type(ctx["preserve_existing_context"]) is bool, "invalid preserve_existing_context")
    if "existing_agent" in ctx:
        agent = ctx["existing_agent"]
        fields(agent, ("agent_id", "vendor", "model", "effort", "harness", "state", "observed_by", "evidence"))
        text_value(agent["agent_id"], "existing agent id", 256)
        check(re.fullmatch(r"[A-Za-z0-9_/][A-Za-z0-9._:/-]{0,255}", agent["agent_id"]),
              "invalid existing agent id")
        for key in ("vendor", "model", "effort", "harness"):
            text_value(agent[key], "existing agent " + key, 256)
        check(agent["state"] in ("idle", "busy", "unknown"), "invalid existing agent state")
        check(agent["observed_by"] == "caller", "existing agent must be caller-observed")
        string_list(agent["evidence"], "existing agent evidence")
    req = ctx["requirements"]
    fields(req, ("tools", "isolation", "lifecycle"))
    string_list(req["tools"], "required tools", nonempty=False)
    identifier(req["isolation"], "isolation")
    identifier(req["lifecycle"], "lifecycle")
    caps = ctx["capabilities"]
    check(isinstance(caps, list) and 0 < len(caps) <= 128, "invalid capabilities")
    for cap in caps:
        fields(cap, ("model", "efforts", "modes", "workdirs", "tools", "isolations", "lifecycles"),
               ("agent_strategy", "existing_agent_id"))
        check(cap.get("agent_strategy", "new") in ("new", "reuse"), "invalid capability strategy")
        if "existing_agent_id" in cap:
            text_value(cap["existing_agent_id"], "capability existing agent id", 256)
        text_value(cap["model"], "model", 256)
        for key in ("efforts", "modes", "workdirs", "isolations", "lifecycles"):
            string_list(cap[key], key)
        string_list(cap["tools"], "tools", nonempty=False)
        cap["workdirs"] = [canonical_dir(d) for d in cap["workdirs"]]
    return ctx


def choose(args, ctx):
    """Exact match on one capability row, never unions across different rows."""
    if args.executor == "cli":
        return "cli", "forced-cli", args.model
    if ctx is None:
        return "cli", "no-native-context", args.model
    if args.vendor in ("vote", "exec", "off"):
        return "cli", "cli-only-routing-kind", args.model
    if args.background or args.session != "auto" or args.thread:
        return "cli", "cli-session-lifecycle", args.model
    if args.job_timeout or args.idle_timeout:
        return "cli", "cli-watchdog-required", args.model
    if args.mode == "sysops":
        return "cli", "sysops-requires-cli", args.model
    if ctx["vendor"] != args.vendor:
        return "cli", "vendor-mismatch", args.model
    model = args.model
    if model in ("", "-"):
        model = ctx.get("current_model", "")
        if not model:
            return "cli", "unknown-current-model", args.model
    caps = [cap for cap in ctx["capabilities"] if cap["model"] == model]
    if not caps:
        return "cli", "model-mismatch", model
    if args.effort in ("", "-"):
        return "cli", "unknown-effort", model
    req = ctx["requirements"]
    # collaboration.spawn_agent inherits the caller's tools and filesystem.
    # advise/work express task intent; neither creates an OS sandbox here.
    isolation = req["isolation"]
    if isolation != "shared-inherited" or req["lifecycle"] != "single-shot":
        return "cli", "unsupported-isolation-or-lifecycle", model
    strategy = ctx.get("agent_strategy", "new")
    if strategy == "reuse":
        if ctx.get("preserve_existing_context") is not True:
            return "cli", "reuse-preserve-context-required", model
        agent = ctx.get("existing_agent")
        if agent is None:
            return "cli", "reuse-existing-agent-required", model
        if agent["state"] != "idle":
            return "cli", "reuse-agent-not-idle", model
        if args.vendor != "codex":
            return "cli", "reuse-backend-unsupported", model
        if ctx.get("current_model") != model or ctx.get("current_effort") != args.effort:
            return "cli", "reuse-current-runtime-mismatch", model
        expected = {"vendor": args.vendor, "model": model, "effort": args.effort, "harness": ctx["harness"]}
        if any(agent[key] != value for key, value in expected.items()):
            return "cli", "reuse-agent-runtime-mismatch", model
        caps = [cap for cap in caps if cap.get("agent_strategy", "new") == "reuse"
                and cap.get("existing_agent_id") == agent["agent_id"]]
    else:
        if "existing_agent" in ctx or ctx.get("preserve_existing_context") is True:
            return "cli", "reuse-strategy-required", model
        if ctx.get("new_agent_capacity") == "exhausted":
            return "cli", "new-agent-capacity-exhausted", model
        caps = [cap for cap in caps if cap.get("agent_strategy", "new") == "new"
                and "existing_agent_id" not in cap]
    if not caps:
        return "cli", "agent-strategy-capability-mismatch", model
    checks = (
        ("effort-mismatch", lambda c: args.effort in c["efforts"]),
        ("mode-mismatch", lambda c: args.mode in c["modes"]),
        ("workdir-mismatch", lambda c: args.workdir in c["workdirs"]),
        ("tools-mismatch", lambda c: set(req["tools"]) <= set(c["tools"])),
        ("isolation-mismatch", lambda c: isolation in c["isolations"]),
        ("lifecycle-mismatch", lambda c: "single-shot" in c["lifecycles"]),
    )
    for reason, predicate in checks:
        caps = [cap for cap in caps if predicate(cap)]
        if not caps:
            return "cli", reason, model
    return "native", "exact-idle-reuse-match" if strategy == "reuse" else "exact-capability-match", model


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def atomic_json(path, value):
    content = json_text(value)
    check(len(content.encode("utf-8")) <= MAX_BYTES, "public record too large")
    temp = path.with_name("." + path.name + "." + secrets.token_hex(8))
    write_new(temp, content)
    try:
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def cleanup_staging_dir(path):
    """Remove only files from a staging directory created by this process."""
    try:
        children = list(path.iterdir())
    except FileNotFoundError:
        return
    for child in children:
        # Never recurse or follow links during cleanup. A surprising entry is
        # left hidden rather than risking deletion of unrelated state.
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            return
    path.rmdir()


def publish_job(root, job_id, plan, metadata, registry=None, caller=None, registry_source=None):
    """Initialize privately, then publish one complete job directory."""
    job = root / job_id
    staging = root / (".native-stage-" + job_id + "-" + secrets.token_hex(8))
    staging.mkdir(mode=0o700)
    try:
        if registry is not None:
            aa_policy.publish_context(staging, registry, caller, plan["aa_policy"], registry_source)
        write_new(staging / "native.lock", "")
        write_new(staging / "task.txt", plan["task"])
        write_new(staging / "meta.json", json_text(metadata))
        write_new(staging / "native.json", json_text(plan))

        publish_lock_path = root / ".native-publish.lock"
        publish_fd = os.open(
            publish_lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        with os.fdopen(publish_fd, "r+") as publish_lock:
            check(stat.S_ISREG(os.fstat(publish_lock.fileno()).st_mode),
                  "invalid native publication lock")
            fcntl.flock(publish_lock, fcntl.LOCK_EX)
            check(not job.exists() and not job.is_symlink(), "job ID collision")
            os.rename(staging, job)
    finally:
        cleanup_staging_dir(staging)


def jobs_root(home, create=False):
    home = Path(home).absolute()
    root = home / "jobs"
    check(not home.is_symlink() and not root.is_symlink(), "unsafe jobs store")
    if create:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.mkdir(mode=0o700, exist_ok=True)
    check(root.is_dir(), "missing jobs store")
    return root


def route(args):
    check(not (args.caller_context and args.operator_asserted_human),
          "caller context and operator assertion are mutually exclusive")
    ctx = capability_context(args.context) if args.context else None
    args.workdir = canonical_dir(args.workdir)
    registry, registry_sha = aa_policy.load_registry(
        args.policy, args.expected_registry_sha256
    )
    caller = None
    caller_sha = None
    if args.caller_context:
        caller, caller_sha = aa_policy.load_caller(
            args.caller_context, registry, args.expected_caller_sha256
        )
    policy_decision = aa_policy.decide(
        registry,
        registry_sha,
        vendor=args.vendor,
        model=args.model,
        effort=None if args.effort in ("", "-") else args.effort,
        caller=caller,
        caller_sha256=caller_sha,
        operator_asserted_human=args.operator_asserted_human,
        target_config=args.target_config,
    )
    if not policy_decision["allowed"]:
        print(aa_policy._json_line(policy_decision), end="", file=sys.stderr)
        return 3
    executor, reason, model = choose(args, ctx)
    if executor == "cli":
        check(args.executor != "native", "native capability rejected: " + reason)
        # Preserve an inherited exact model on CLI fallback too.
        print(json_text({"reason": reason, "model": model}).strip())
        return 10
    plan = {"schema_version": 1, "executor": executor, "executor_reason": reason,
            "vendor": args.vendor, "model": model, "effort": args.effort,
            "harness": ctx["harness"], "lane": args.lane, "mode": args.mode,
            "workdir": args.workdir, "task": args.task,
            "requirements": ctx["requirements"], "timeout": args.timeout,
            "worker_contract": {
                "no_nested_dispatch": True,
                "isolation": ctx["requirements"]["isolation"],
                "mode_is_task_intent": True,
                "caller_enforces_deadline": True,
            },
            "aa_policy": policy_decision,
            "state": "planned" if args.dry_run else "pending",
            "job_id": None, "task_id": None, "agent_id": None,
            "provider_invoked": False, "job_state_created": False}
    strategy = ctx.get("agent_strategy", "new")
    plan.update(agent_strategy=strategy, existing_agent_id=None,
                preserve_existing_context=strategy == "reuse",
                new_agent_capacity=ctx.get("new_agent_capacity", "unknown"))
    if strategy == "reuse":
        agent = ctx["existing_agent"]
        plan.update(existing_agent_id=agent["agent_id"], agent_id=agent["agent_id"],
                    reuse_observation={"state": agent["state"], "observed_by": "caller",
                                       "evidence": agent["evidence"]})
        plan["worker_contract"].update(backend="collaboration.followup_task",
                                       preserve_existing_context=True,
                                       caller_rechecks_idle_before_followup=True)
    if args.dry_run:
        print(json_text(plan), end="")
        return 0
    if args.task == "-":
        plan["task"] = sys.stdin.read(MAX_BYTES + 1)
    text_value(plan["task"], "task", 65536, multiline=True)
    job_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}-{secrets.randbelow(10**9)}"
    plan.update(job_id=job_id, task_id=job_id, created=stamp(), job_state_created=True)
    # Validate size before creating any job state.
    check(len(json_text(plan).encode("utf-8")) <= MAX_BYTES, "handoff too large")
    metadata = {k: plan[k] for k in ("lane", "vendor", "model", "effort", "mode", "workdir", "executor", "executor_reason")}
    metadata.update(aa_policy_code=policy_decision["code"],
                    aa_effective_ceiling=policy_decision["effective_ceiling"])
    metadata.update(started=plan["created"], session_mode="native")
    check(len(json_text(metadata).encode("utf-8")) <= 4096, "metadata too large")
    root = jobs_root(args.home, create=True)
    if policy_decision.get("child_context") is not None:
        plan["worker_contract"]["caller_context_path"] = str(root / job_id / "aa-child-context.json")
        plan["worker_contract"]["caller_context"] = policy_decision["child_context"]
    publish_job(root, job_id, plan, metadata, registry, caller, args.policy)
    print(json_text(plan), end="")
    return 0


def validate_completion(value, state):
    fields(value, ("schema_version", "job_id", "agent_id", "runtime", "outcome", "result", "evidence"),
           ("agent_strategy",))
    check(type(value["schema_version"]) is int and value["schema_version"] == 1, "unsupported completion version")
    check(value["job_id"] == state["job_id"], "completion job ID mismatch")
    # Hosts may expose a canonical task name such as /root/reviewer as the
    # agent identifier. It is public metadata only, never a path or a PID.
    text_value(value["agent_id"], "agent_id", 256)
    check(re.fullmatch(r"[A-Za-z0-9_/][A-Za-z0-9._:/-]{0,255}", value["agent_id"]), "invalid agent_id")
    runtime = value["runtime"]
    fields(runtime, ("vendor", "model", "effort", "harness", "backend"))
    for key in ("vendor", "model", "effort", "harness", "backend"):
        text_value(runtime[key], "runtime " + key, 256)
    for key in ("vendor", "model", "effort", "harness"):
        check(runtime[key] == state[key], "runtime " + key + " mismatch")
    strategy = state.get("agent_strategy", "new")
    if strategy == "reuse":
        check(value.get("agent_strategy") == "reuse", "reuse completion strategy required")
        check(value["agent_id"] == state["existing_agent_id"], "reuse agent ID mismatch")
        check(runtime["backend"] == "collaboration.followup_task", "reuse backend mismatch")
    else:
        check(value.get("agent_strategy", "new") == "new", "new-agent completion strategy mismatch")
        check(runtime["backend"] != "collaboration.followup_task", "reuse backend on new-agent job")
    check(value["outcome"] in ("success", "failure"), "invalid outcome")
    text_value(value["result"], "result", 65536, multiline=True)
    string_list(value["evidence"], "evidence")
    return value


def job_command(args):
    check(JOB_ID.fullmatch(args.job_id), "invalid job ID")
    job = jobs_root(args.home) / args.job_id
    check(job.is_dir() and not job.is_symlink(), "invalid job directory")
    lock_fd = os.open(job / "native.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(lock_fd, "r+") as lock:
        check(stat.S_ISREG(os.fstat(lock.fileno()).st_mode), "invalid native lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_json(job / "native.json")
        check(isinstance(state, dict) and state.get("job_id") == args.job_id
              and state.get("executor") == "native"
              and state.get("state") in ("pending", "completed", "cancelled"), "invalid native state")
        if args.action in ("complete-native", "cancel"):
            check(state["state"] == "pending", "native job is already terminal")
            if args.action == "complete-native":
                check(args.input is not None, "completion input file required")
                value = validate_completion(read_json(args.input), state)
                state.update(state="completed", agent_id=value["agent_id"], completion=value,
                             exit_code=0 if value["outcome"] == "success" else 1, finished=stamp())
            else:
                check(args.input is None, "unexpected cancel input")
                # There is deliberately no PID or signal here. The caller owns
                # any agent already spawned and must stop it with its own tool.
                state.update(state="cancelled", exit_code=143, finished=stamp())
            atomic_json(job / "native.json", state)
        pending = state["state"] == "pending"
        public_state = "pending" if pending else "cancelled" if state["state"] == "cancelled" else "done"
        if args.action == "list-state":
            print(public_state)
            return 0
        if args.action == "result":
            check(not pending, "native job pending; ingest caller result first")
        summary = {"id": args.job_id, "state": public_state, "native_state": state["state"],
                   "executor": "native", "executor_reason": state["executor_reason"],
                   "exit_code": state.get("exit_code"), "agent_id": state["agent_id"],
                   "vendor": state["vendor"], "model": state["model"], "effort": state["effort"],
                   "harness": state["harness"]}
        summary.update(agent_strategy=state.get("agent_strategy", "new"),
                       existing_agent_id=state.get("existing_agent_id"),
                       preserve_existing_context=state.get("preserve_existing_context", False))
        if args.action == "result":
            summary["completion"] = state.get("completion")
        if args.json:
            print(json_text({"schema_version": 1, "command": args.action, "ok": True, "job": summary}), end="")
        elif args.action == "result":
            print(state.get("completion", {}).get("result", "cancelled by caller"))
        else:
            print(json_text(summary), end="")
        return state.get("exit_code", 0) if args.action == "result" else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("route")
    for key in ("home", "lane", "vendor", "model", "effort", "workdir", "task"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--executor", choices=("auto", "native", "cli"), required=True)
    p.add_argument("--mode", choices=("advise", "work", "sysops"), required=True)
    p.add_argument("--context")
    p.add_argument("--policy", required=True)
    p.add_argument("--caller-context")
    p.add_argument("--operator-asserted-human", action="store_true")
    p.add_argument("--expected-registry-sha256")
    p.add_argument("--expected-caller-sha256")
    p.add_argument("--target-config")
    p.add_argument("--session", default="auto")
    p.add_argument("--thread", default="")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--job-timeout", default="")
    p.add_argument("--idle-timeout", default="")
    p.add_argument("--background", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("job")
    p.add_argument("--home", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("action", choices=("status", "result", "cancel", "complete-native", "list-state"))
    p.add_argument("job_id")
    p.add_argument("input", nargs="?")
    args = parser.parse_args()
    try:
        return route(args) if args.command == "route" else job_command(args)
    except (ValueError, OSError, RecursionError, TypeError, KeyError) as error:
        # Avoid echoing submitted JSON, task text, result text, or capability data.
        if isinstance(error, (OSError, UnicodeError, json.JSONDecodeError, RecursionError, TypeError, KeyError)):
            message = "invalid or inaccessible native protocol input/state"
        else:
            message = str(error)
        if getattr(args, "json", False):
            print(json_text({"schema_version": 1, "command": args.action, "ok": False, "error": message}), end="")
        else:
            print("omnilane: " + message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
