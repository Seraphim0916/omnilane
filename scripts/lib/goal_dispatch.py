#!/usr/bin/env python3
"""Private goal-dispatch journal. Reconciliation never starts or retries work.

The goal lock serializes prepare/reconcile. Claim and abort deliberately do not
use it: an immutable, no-overwrite outcome slot fences a delayed dispatcher.
A claim alone is NOT evidence that its background worker started.
"""
import contextlib
import datetime
import json
import os
import re
import stat
import sys
import time
import uuid

from goal_budget import validate_budget

ID = r"[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+"
INTENT = r"intent-([0-9]{8,})-([0-9a-f]{32})"
RECORD = r"job-([0-9]{8,})\.json"
MAX_RECORD_BYTES = 262144
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def directory(parent, name, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    fd = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent)
    if os.fstat(fd).st_uid != os.geteuid():
        os.close(fd)
        raise ValueError("goal journal directory has a different owner")
    return fd


def read_bytes(fd, name, limit=MAX_RECORD_BYTES, single_link=False):
    file_fd = os.open(name, FLAGS, dir_fd=fd)
    try:
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or (single_link and info.st_nlink != 1)):
            raise ValueError("unsafe goal journal evidence: " + name)
        if info.st_size > limit:
            raise ValueError("oversized goal journal evidence: " + name)
        data = os.read(file_fd, limit + 1)
        if len(data) > limit:
            raise ValueError("oversized goal journal evidence: " + name)
        return data
    finally:
        os.close(file_fd)


def read_json(fd, name):
    value = json.loads(read_bytes(fd, name))
    if not isinstance(value, dict):
        raise ValueError("invalid goal journal object: " + name)
    return value


def exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def publish(fd, name, value):
    """Fully write/fsync before exposing a name; never overwrite an outcome."""
    temporary = ".journal-" + uuid.uuid4().hex + ".tmp"
    file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
    try:
        with os.fdopen(file_fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            # Match the reader's byte bound before exposing any canonical name.
            # JSON escaping can grow otherwise valid argv beyond that limit.
            if os.fstat(handle.fileno()).st_size > MAX_RECORD_BYTES:
                raise ValueError("oversized goal journal record")
            os.fsync(handle.fileno())
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=fd)
        # If this fails, leave the published slot occupied, but do NOT launch.
        os.fsync(fd)
    finally:
        if exists(fd, temporary):
            os.unlink(temporary, dir_fd=fd)


class Store(contextlib.ExitStack):
    def __init__(self, home, goal_id, create=False):
        super().__init__()
        if not re.fullmatch(ID, goal_id):
            raise ValueError("invalid goal journal goal id")
        self.goal_id = goal_id
        try:
            self.home = self.keep(directory(None, home))
            goals = self.keep(directory(self.home, "goals"))
            self.goal = self.keep(directory(goals, goal_id))
            self.jobs = self.keep(directory(self.goal, "jobs"))
            self.journal = None
            if create or exists(self.goal, "dispatch"):
                self.journal = self.keep(directory(self.goal, "dispatch", create=create))
        except BaseException:
            self.close()
            raise

    def keep(self, fd):
        self.callback(os.close, fd)
        return fd

    def intent(self, name):
        match = re.fullmatch(INTENT, name)
        if not match or self.journal is None:
            raise ValueError("invalid goal dispatch intent reference")
        value = read_json(self.journal, name + ".json")
        required = {"schema_version", "intent_id", "goal_id", "ordinal", "lane", "task",
                    "fingerprint", "workdir", "submitted_epoch", "submitted"}
        if (set(value) != required or value["schema_version"] != 1
                or value["intent_id"] != name or value["goal_id"] != self.goal_id
                or type(value["ordinal"]) is not int or value["ordinal"] != int(match[1])
                or name != f"intent-{value['ordinal']:08d}-{match[2]}"
                or value["ordinal"] < 1 or type(value["submitted_epoch"]) is not int
                or value["submitted_epoch"] < 0
                or not all(isinstance(value[key], str) for key in
                           ("lane", "task", "fingerprint", "workdir", "submitted"))
                or not re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"])):
            raise ValueError("invalid goal dispatch intent: " + name)
        return value

    def outcome(self, intent):
        name = intent["intent_id"] + ".outcome.json"
        if not exists(self.journal, name):
            return None
        value = read_json(self.journal, name)
        expected = {"schema_version", "intent_id", "goal_id", "kind"}
        if value.get("kind") == "claim":
            expected.add("job_id")
        if (set(value) != expected or value.get("schema_version") != 1
                or value.get("intent_id") != intent["intent_id"]
                or value.get("goal_id") != self.goal_id
                or value.get("kind") not in {"claim", "no-launch"}
                or (value["kind"] == "claim"
                    and not re.fullmatch(ID, str(value.get("job_id", ""))))):
            raise ValueError("invalid goal dispatch outcome: " + name)
        return value

    def started(self, job_id):
        # Directories and meta.json are allocated before spawn. Only a valid
        # PID written by run_job, or a terminal exit written by finish_job, is
        # positive evidence. Missing/pruned evidence leaves the reservation.
        if not exists(self.home, "jobs"):
            return False
        with contextlib.ExitStack() as stack:
            jobs = directory(self.home, "jobs")
            stack.callback(os.close, jobs)
            if not exists(jobs, job_id):
                return False
            job = directory(jobs, job_id)
            stack.callback(os.close, job)
            found = False
            for name, pattern in (("pid", rb"[1-9][0-9]{0,9}\n?"),
                                  ("exit", rb"(0|[1-9][0-9]{0,2})\n?")):
                if exists(job, name):
                    data = read_bytes(job, name, 32, single_link=True)
                    if not re.fullmatch(pattern, data) or (name == "exit" and int(data) > 255):
                        raise ValueError("invalid goal dispatch start evidence: " + name)
                    found = True
            return found

    def reconcile(self):
        records = {}
        job_ids = set()
        for name in sorted(os.listdir(self.jobs)):
            match = re.fullmatch(RECORD, name)
            if not match:
                if not name.startswith(".") and not re.fullmatch(RECORD + r"\.tmp\.[0-9]+", name):
                    raise ValueError("unexpected goal job record: " + name)
                continue
            record = read_json(self.jobs, name)
            ordinal = int(match[1])
            job_id = record.get("job_id", "")
            if (not isinstance(job_id, str) or not re.fullmatch(ID, job_id)
                    or type(record.get("ordinal")) is not int or record["ordinal"] != ordinal or ordinal < 1
                    or name != f"job-{ordinal:08d}.json" or ordinal in records
                    or job_id in job_ids):
                raise ValueError("conflicting goal job record: " + name)
            records[ordinal] = record
            job_ids.add(job_id)
        intents = {}
        outcomes = {}
        if self.journal is not None:
            names = os.listdir(self.journal)
            for name in sorted(names):
                if name.startswith(".journal-") and name.endswith(".tmp"):
                    continue  # Interrupted temporary writes are never evidence.
                if not re.fullmatch(INTENT + r"(?:\.outcome)?\.json", name):
                    raise ValueError("unexpected goal dispatch evidence: " + name)
                stem = name.removesuffix(".json").removesuffix(".outcome")
                intent = self.intent(stem)
                ordinal = intent["ordinal"]
                if ordinal in intents and intents[ordinal] != intent:
                    raise ValueError("conflicting goal dispatch ordinal")
                intents[ordinal] = intent
            claimed_jobs = set()
            for ordinal, intent in intents.items():
                outcome = self.outcome(intent)
                outcomes[ordinal] = outcome
                if outcome and outcome["kind"] == "claim":
                    if outcome["job_id"] in claimed_jobs:
                        raise ValueError("conflicting goal dispatch job id")
                    claimed_jobs.add(outcome["job_id"])
                record = records.get(ordinal)
                if record is not None:
                    if (record.get("intent_id") != intent["intent_id"] or not outcome
                            or outcome["kind"] != "claim" or record["job_id"] != outcome["job_id"]
                            or any(record.get(key) != intent[key] for key in
                                   ("fingerprint", "task", "submitted_epoch", "submitted"))):
                        raise ValueError("goal record conflicts with dispatch intent")
                elif outcome and outcome["kind"] == "claim" and outcome["job_id"] in job_ids:
                    raise ValueError("goal dispatch job id already recorded elsewhere")
        for ordinal, record in records.items():
            if "intent_id" in record and ordinal not in intents:
                raise ValueError("goal job record is missing its dispatch intent")
        reserved = 0
        for ordinal, intent in sorted(intents.items()):
            if ordinal in records:
                continue
            outcome = outcomes[ordinal]
            if outcome and outcome["kind"] == "no-launch":
                continue
            if not outcome or not self.started(outcome["job_id"]):
                reserved += 1
                continue
            record = {key: intent[key] for key in
                      ("ordinal", "intent_id", "lane", "task", "fingerprint", "workdir",
                       "submitted_epoch", "submitted")}
            record.update(job_id=outcome["job_id"], vendor="unknown", model="", mode="unknown",
                          state="unknown", exit=None, seconds=0, failure_counted=False)
            publish(self.jobs, f"job-{ordinal:08d}.json", record)
            records[ordinal] = record
        return len(records), reserved, max([0, *records, *intents])


def reference(ref):
    parts = ref.split("/")
    if len(parts) != 3 or parts[1] != "dispatch" or not re.fullmatch(INTENT, parts[2]):
        raise ValueError("invalid goal dispatch intent reference")
    return parts[0], parts[2]


def reconcile(home, goal_id):
    with Store(home, goal_id) as store:
        return store.reconcile()


def main(args):
    command, home, target, *rest = args
    if command == "prepare":
        lane, fingerprint, task = rest
        with Store(home, target) as store:
            budget = validate_budget(read_json(store.goal, "budget.json"))
            spent, reserved, ordinal = store.reconcile()
            if budget.get("status") != "open" or reserved:
                raise ValueError("goal has unresolved dispatch reservations or is closed")
            limit = budget.get("budget_jobs")
            if limit is not None and spent + reserved >= limit:
                raise ValueError("goal jobs budget exhausted")
            name = f"intent-{ordinal + 1:08d}-" + uuid.uuid4().hex
            now = int(time.time())
            value = dict(schema_version=1, intent_id=name, goal_id=target,
                         ordinal=ordinal + 1, lane=lane, task=task[:2000], fingerprint=fingerprint,
                         workdir=budget["workdir"], submitted_epoch=now,
                         submitted=datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"))
            if store.journal is None:
                store.journal = store.keep(directory(store.goal, "dispatch", create=True))
            publish(store.journal, name + ".json", value)
            print(target + "/dispatch/" + name)
    elif command in {"claim", "abort", "verify"}:
        goal_id, name = reference(target)
        with Store(home, goal_id) as store:
            intent = store.intent(name)
            outcome = dict(schema_version=1, intent_id=name, goal_id=goal_id,
                           kind="claim" if command == "claim" else "no-launch")
            if command == "verify":
                job_id, = rest
                existing = store.outcome(intent)
                if not existing or existing["kind"] != "claim" or existing["job_id"] != job_id:
                    raise ValueError("dispatch output does not match its claimed job id")
                return 0
            if command == "claim":
                job_id, lane, fingerprint = rest
                if lane != intent["lane"] or fingerprint != intent["fingerprint"]:
                    raise ValueError("dispatch task does not match its intent")
                if not re.fullmatch(ID, job_id):
                    raise ValueError("invalid claimed job id")
                outcome["job_id"] = job_id
                # A repeated claim, even for this same ID, can NEVER launch.
                publish(store.journal, name + ".outcome.json", outcome)
            else:
                if rest:
                    raise ValueError("unexpected abort arguments")
                try:
                    publish(store.journal, name + ".outcome.json", outcome)
                except FileExistsError:
                    existing = store.outcome(intent)
                    if existing["kind"] == "claim":
                        return 75  # Launch may have happened; retain reservation.
    else:
        raise ValueError("unknown private goal journal operation")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("omnilane goal journal: " + str(error), file=sys.stderr)
        sys.exit(1)
