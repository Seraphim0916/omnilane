"""Hermetic crash/race matrix for goal dispatch's durable intent protocol."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest

import test_goal_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "scripts/lib/goal_dispatch.py"
JOB_ID = "20261001-000000-1234-99"


class GoalDispatchJournalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery.GoalRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.env = self.fixture.root, self.fixture.env
        self.home, self.goal = self.fixture.home, self.fixture.goal
        self.goal_id = self.fixture.goal_id
        self.calls = self.root / "calls"
        self.gate = self.root / "gate.sh"
        self.gate.write_text('#!/bin/sh\n'
                             'test -z "${OMNILANE_GOAL_INTENT+x}" || exit 93\n'
                             'printf "called\\n" >> ' + shlex.quote(str(self.calls)) + '\n'
                             'printf "fixture completed\\n" > "$5"\n')
        self.gate.chmod(0o755)
        (self.home / "routing.local.yaml").write_text(f'probe: exec "{self.gate}" -\n')

    def goal_command(self, *args, env=None):
        return self.fixture.run_goal(*args, env=env)

    def journal(self, command, *args, env=None):
        if command == "claim" and len(args) == 2:
            args = (*args, "probe", hashlib.sha256(b"probe\0offline journal").hexdigest())
        return subprocess.run([sys.executable, str(JOURNAL), command, str(self.home), *args],
                              env=env or self.env, text=True, capture_output=True, timeout=15)

    def prepare(self, task="offline journal"):
        result = self.journal("prepare", self.goal_id, "probe",
                              hashlib.sha256(("probe\0" + task).encode()).hexdigest(), task)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def dispatch(self, *, env=None):
        return self.goal_command("dispatch", self.goal_id, "--mode", "work", "probe",
                                 "offline journal", env=env)

    def direct_dispatch(self, ref, *, env=None):
        process = subprocess.Popen(["bash", str(ROOT / "scripts/dispatch.sh"), "--background",
                                 "--workdir", str(self.root), "--mode", "work", "probe",
                                 "offline journal"], env=dict(env or self.env, OMNILANE_GOAL_INTENT=ref),
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        def reap():
            (self.root / "release").touch()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=3)
        self.addCleanup(reap)
        return process

    def call_count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def wait_jobs(self, count):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            exits = list((self.home / "jobs").glob("*/exit"))
            if len(exits) >= count:
                return
            time.sleep(.02)
        self.fail("fake workers did not finish")

    def status(self):
        result = self.goal_command("status", self.goal_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.goal / "budget.json").read_text())

    def records(self):
        return [json.loads(path.read_text()) for path in sorted((self.goal / "jobs").glob("job-*.json"))]

    def fault_env(self, kind):
        bins = self.root / ("fault-bin-" + kind)
        bins.mkdir(exist_ok=True)
        hook = bins / "hook.py"
        hook.write_text('''import json, os, pathlib, runpy, sys, time
args = sys.argv[1:]
if args[0].startswith("-") and args[0] != "-":
    os.execv(sys.executable, [sys.executable, *args])
kind = os.environ["JOURNAL_FAULT"]
original_dump = json.dump
original_link = os.link
original_fsync = os.fsync
original_open = os.open
fd_paths = {}
claim_fds = set()
claim_published = False
def tracked_open(path, flags, *args, **kwargs):
    fd = original_open(path, flags, *args, **kwargs)
    parent = fd_paths.get(kwargs.get("dir_fd"), "")
    fd_paths[fd] = os.path.join(parent, str(path))
    return fd
os.open = tracked_open

def dump(value, handle, *args, **kwargs):
    if isinstance(value, dict) and value.get("kind") == "claim":
        claim_fds.add(handle.fileno())
    if kind == "partial-record" and isinstance(value, dict) and "job_id" in value and "ordinal" in value and "/goals/" in fd_paths.get(handle.fileno(), str(handle.name)) and "/jobs/" in fd_paths.get(handle.fileno(), str(handle.name)):
        handle.write('{"job_id":'); handle.flush()
        raise OSError("injected partial record write")
    return original_dump(value, handle, *args, **kwargs)

def link(source, destination, *args, **kwargs):
    global claim_published
    if str(destination).endswith(".outcome.json") and kind == "delay-claim" and '"claim"' in os.read(os.open(source, os.O_RDONLY, dir_fd=kwargs.get("src_dir_fd")), 4096).decode():
        open(os.environ["FAULT_READY"], "w").close()
        while not os.path.exists(os.environ["FAULT_RELEASE"]): time.sleep(.01)
    result = original_link(source, destination, *args, **kwargs)
    if str(destination).endswith(".outcome.json"):
        claim_published = True
        if kind == "after-link-exit": os._exit(88)
    if str(destination).endswith(".outcome.json") and kind == "after-claim":
        raise OSError("injected interruption after claim")
    return result
def fsync(fd):
    if kind == "claim-file-fsync" and fd in claim_fds:
        raise OSError("injected claim file fsync")
    if kind == "claim-dir-fsync" and claim_published and fd_paths.get(fd, "").endswith("/dispatch"):
        raise OSError("injected claim directory fsync")
    return original_fsync(fd)
json.dump = dump
os.link = link
os.fsync = fsync
sys.argv = args
if args[0] == "-":
    exec(compile(sys.stdin.read(), "<journal fixture>", "exec"), {"__name__": "__main__"})
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(args[0])))
    runpy.run_path(args[0], run_name="__main__")
''')
        wrapper = bins / "python3"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(hook)) + ' "$@"\n')
        wrapper.chmod(0o755)
        return dict(self.env, PATH=str(bins) + os.pathsep + self.env["PATH"], JOURNAL_FAULT=kind,
                    FAULT_READY=str(self.root / "ready"), FAULT_RELEASE=str(self.root / "release"))

    def test_partial_record_recovers_once_without_relaunch(self):
        result = self.dispatch(env=self.fault_env("partial-record"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("injected partial record", result.stderr)
        self.wait_jobs(1)
        for _ in range(3):
            budget = self.status()
            self.assertEqual((budget["spent_jobs"], budget.get("reserved_jobs")), (1, 0))
            self.assertEqual(len(self.records()), 1)
            self.assertEqual(self.call_count(), 1)
        self.assertIsNone(next(iter(self.records())).get("unexpected"))

    def test_output_loss_reconciles_and_identical_new_submission_launches_again(self):
        ref = self.prepare()
        first = self.direct_dispatch(ref)
        output, error = first.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, error)
        self.assertRegex(output.strip(), r"^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$")
        self.wait_jobs(1)
        self.assertEqual(self.status()["spent_jobs"], 1)
        self.assertEqual(self.call_count(), 1)
        second = self.dispatch()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.wait_jobs(2)
        self.assertEqual(self.status()["spent_jobs"], 2)
        self.assertEqual(self.call_count(), 2)

    def test_duplicate_claim_race_launches_exactly_one_worker(self):
        ref = self.prepare()
        processes = [self.direct_dispatch(ref), self.direct_dispatch(ref)]
        results = [(process.communicate(timeout=15), process.returncode) for process in processes]
        self.assertEqual(sorted(code == 0 for _, code in results), [False, True], results)
        self.wait_jobs(1)
        self.assertEqual(self.status()["spent_jobs"], 1)
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(len(list((self.home / "jobs").iterdir())), 1)

    def test_abort_receipt_fences_delayed_dispatcher(self):
        ref = self.prepare()
        process = self.direct_dispatch(ref, env=self.fault_env("delay-claim"))
        deadline = time.monotonic() + 10
        while not (self.root / "ready").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((self.root / "ready").exists())
        aborted = self.journal("abort", ref)
        self.assertEqual(aborted.returncode, 0, aborted.stderr)
        (self.root / "release").touch()
        _, error = process.communicate(timeout=15)
        self.assertNotEqual(process.returncode, 0, error)
        self.assertEqual(self.status()["reserved_jobs"], 0)
        self.assertEqual(self.call_count(), 0)
        next_ref = self.prepare()
        self.assertIn("intent-00000002-", next_ref)

    def test_claim_before_spawn_stays_reserved_and_blocks_close_and_dispatch(self):
        result = self.dispatch(env=self.fault_env("after-claim"))
        self.assertNotEqual(result.returncode, 0)
        for _ in range(2):
            budget = self.status()
            self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (0, 1))
            self.assertEqual(self.records(), [])
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(self.dispatch().returncode, 75)
        self.assertEqual(self.goal_command("close", self.goal_id).returncode, 75)
        self.assertEqual(self.call_count(), 0)

    def test_unclaimed_intent_reserves_one_slot_and_does_not_invent_failure(self):
        budget = json.loads((self.goal / "budget.json").read_text())
        budget["budget_jobs"] = 1
        (self.goal / "budget.json").write_text(json.dumps(budget))
        self.prepare()
        self.assertEqual(self.status()["reserved_jobs"], 1)
        self.assertEqual(self.dispatch().returncode, 75)
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(json.loads((self.goal / "failures.json").read_text()), {})

    def test_pre_reconcile_prune_remains_unresolved(self):
        ref = self.prepare()
        process = self.direct_dispatch(ref)
        _, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        self.wait_jobs(1)
        shutil.rmtree(self.home / "jobs")
        budget = self.status()
        self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (0, 1))
        self.assertEqual(self.dispatch().returncode, 75)
        self.assertEqual(self.call_count(), 1)

    def test_fsync_failure_does_not_launch_and_stays_reserved(self):
        for fault in ("claim-file-fsync", "claim-dir-fsync"):
            with self.subTest(fault=fault):
                # Each subcase is a separate goal; neither ambiguous reservation is reused.
                if fault == "claim-dir-fsync":
                    opened = self.goal_command("open", "fsync", "--workdir", str(self.root))
                    self.goal_id = opened.stdout.strip()
                    self.goal = self.home / "goals" / self.goal_id
                ref = self.prepare()
                process = self.direct_dispatch(ref, env=self.fault_env(fault))
                _, error = process.communicate(timeout=15)
                self.assertNotEqual(process.returncode, 0, error)
                self.assertIn("injected claim", error)
                budget = self.status()
                self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (0, 1))
                self.assertEqual(self.call_count(), 0)
                self.assertEqual(self.dispatch().returncode, 75)

    def test_process_death_after_link_retains_occupied_claim(self):
        ref = self.prepare()
        process = self.direct_dispatch(ref, env=self.fault_env("after-link-exit"))
        _, error = process.communicate(timeout=15)
        self.assertNotEqual(process.returncode, 0, error)
        path = self.home / "goals" / (ref + ".outcome.json")
        self.assertEqual(path.stat().st_nlink, 2)
        self.assertEqual(self.status()["reserved_jobs"], 1)
        retry = self.direct_dispatch(ref)
        retry.communicate(timeout=15)
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(self.journal("abort", ref).returncode, 75)
        self.assertEqual(self.call_count(), 0)

    def test_postspawn_nonzero_or_invalid_output_recovers_without_resubmission(self):
        # Intercept only the dispatch executable launch, preserving its real
        # fake worker, and lose/corrupt its receipt after it has returned.
        for index, fault in enumerate(("nonzero", "invalid-output", "wrong-id"), 1):
            with self.subTest(fault=fault):
                bins = self.root / ("bash-" + fault)
                bins.mkdir()
                wrapper = bins / "bash"
                real = shutil.which("bash", path=self.env["PATH"])
                wrapper.write_text("#!/bin/sh\n"
                    'case "$1" in */scripts/dispatch.sh)\n' +
                    shlex.quote(real) + ' "$@" >/dev/null\n' +
                    ('exit 7\n' if fault == "nonzero" else 'echo 20261001-000000-1234-42; exit 0\n' if fault == "wrong-id" else 'echo invalid-job-output; exit 0\n') +
                    ';; *) exec ' + shlex.quote(real) + ' "$@";; esac\n')
                wrapper.chmod(0o755)
                result = self.dispatch(env=dict(self.env, PATH=str(bins) + os.pathsep + self.env["PATH"]))
                self.assertNotEqual(result.returncode, 0)
                self.wait_jobs(index)
                budget = self.status()
                self.assertEqual(budget["spent_jobs"], index)
                self.assertEqual(budget["reserved_jobs"], 0)
                self.assertEqual(self.call_count(), budget["spent_jobs"])

    def test_committed_history_survives_prune(self):
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_jobs(1)
        self.assertEqual(self.status()["spent_jobs"], 1)
        shutil.rmtree(self.home / "jobs")
        self.assertEqual(self.status()["spent_jobs"], 1)
        self.assertEqual(self.status()["reserved_jobs"], 0)
        self.assertEqual(self.call_count(), 1)

    def test_oversized_intent_is_rejected_without_poisoning_goal(self):
        # This fits argv but JSON escaping exceeds the journal reader limit.
        rejected = self.goal_command("dispatch", self.goal_id, "--mode", "work",
                                     "\x01" * 50000, "oversized intent fixture")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(list((self.goal / "dispatch").glob("intent-*.json")), [])
        budget = self.status()
        self.assertEqual((budget["spent_jobs"], budget["reserved_jobs"]), (0, 0))
        normal = self.dispatch()
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.wait_jobs(1)
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(self.status()["spent_jobs"], 1)

    def test_legacy_goal_without_journal_remains_compatible(self):
        self.assertFalse((self.goal / "dispatch").exists())
        budget = self.status()
        self.assertEqual(budget["schema_version"], 3)
        self.assertEqual(budget["spent_jobs"], 0)
        self.assertEqual(self.dispatch().returncode, 0)
        self.wait_jobs(1)
        self.assertEqual(self.call_count(), 1)

    def test_corrupt_or_symlinked_evidence_fails_closed(self):
        ref = self.prepare()
        path = self.home / "goals" / (ref + ".json")
        original = path.read_text()
        path.write_text("{")
        self.assertNotEqual(self.goal_command("status", self.goal_id).returncode, 0)
        self.assertNotEqual(self.dispatch().returncode, 0)
        path.write_text(original)
        claimed = self.journal("claim", ref, JOB_ID)
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        job = self.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        other = self.root / "outside-exit"
        other.write_text("0\n")
        (job / "exit").symlink_to(other)
        self.assertNotEqual(self.goal_command("status", self.goal_id).returncode, 0)
        self.assertEqual(self.call_count(), 0)

    def test_unsafe_start_evidence_never_commits_or_launches(self):
        ref = self.prepare()
        self.assertEqual(self.journal("claim", ref, JOB_ID).returncode, 0)
        job = self.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        outside = self.root / "outside"
        for name in ("pid", "exit"):
            for kind in ("empty", "malformed", "fifo", "hardlink", "symlink"):
                with self.subTest(name=name, kind=kind):
                    path = job / name
                    outside.write_text("123\n" if name == "pid" else "0\n")
                    if kind == "empty": path.touch()
                    elif kind == "malformed": path.write_text("wrong\n")
                    elif kind == "fifo": os.mkfifo(path)
                    elif kind == "hardlink": os.link(outside, path)
                    else: path.symlink_to(outside)
                    try:
                        result = self.goal_command("status", self.goal_id)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertEqual(self.records(), [])
                        self.assertNotEqual(self.dispatch().returncode, 0)
                        self.assertEqual(self.call_count(), 0)
                    finally:
                        path.unlink()
        self.assertEqual(self.status()["reserved_jobs"], 1)

    def test_symlinked_journal_or_job_parent_fails_closed(self):
        ref = self.prepare()
        self.assertEqual(self.journal("claim", ref, JOB_ID).returncode, 0)
        jobs = self.home / "jobs"
        jobs.mkdir()
        for path in (self.goal / "dispatch", self.goal / "jobs", jobs):
            with self.subTest(path=path):
                moved = path.with_name(path.name + "-moved")
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=True)
                try:
                    self.assertNotEqual(self.goal_command("status", self.goal_id).returncode, 0)
                    self.assertNotEqual(self.dispatch().returncode, 0)
                finally:
                    path.unlink()
                    moved.rename(path)
        self.assertEqual(self.call_count(), 0)

    def test_conflicting_or_noncanonical_ledger_is_not_overwritten(self):
        ref = self.prepare()
        claimed = self.journal("claim", ref, JOB_ID)
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        job = self.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        (job / "exit").write_text("0\n")
        record = {"job_id": JOB_ID, "ordinal": 1, "submitted_epoch": 1}
        for name in ("job-00000001.json", "job-000000001.json"):
            with self.subTest(name=name):
                path = self.goal / "jobs" / name
                raw = json.dumps(record)
                path.write_text(raw)
                try:
                    self.assertNotEqual(self.goal_command("status", self.goal_id).returncode, 0)
                    self.assertEqual(path.read_text(), raw)
                    self.assertEqual(self.call_count(), 0)
                finally:
                    path.unlink()
        self.assertEqual(self.status()["spent_jobs"], 1)

    def test_legacy_spent_and_failed_history_and_aborted_ordinals(self):
        fingerprint = self.fixture.failed_jobs()
        self.assertEqual(self.status()["spent_jobs"], 2)
        self.assertEqual(json.loads((self.goal / "failures.json").read_text())[fingerprint], 2)
        ref = self.prepare()
        self.assertIn("intent-00000003-", ref)
        self.assertEqual(self.journal("abort", ref).returncode, 0)
        self.assertEqual(self.status()["spent_jobs"], 2)
        next_ref = self.prepare()
        self.assertIn("intent-00000004-", next_ref)
        process = self.direct_dispatch(next_ref)
        _, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        self.wait_jobs(3)
        self.assertEqual(self.status()["spent_jobs"], 3)
        self.assertEqual([record["ordinal"] for record in self.records()], [1, 2, 4])
        self.assertEqual(json.loads((self.goal / "failures.json").read_text())[fingerprint], 2)
        self.assertEqual(self.call_count(), 1)

    def test_stdin_task_bytes_and_fingerprint_match_without_reinterpretation(self):
        for index, task in enumerate(("-", "", "line one\nline two\n\n"), 1):
            with self.subTest(task=task):
                result = subprocess.run(["bash", str(ROOT / "scripts/lib/goal-loop.sh"),
                    "dispatch", self.goal_id, "--mode", "work", "probe", "-"],
                    input=task, env=self.env, text=True, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.wait_jobs(index)
                self.assertEqual(self.status()["spent_jobs"], index)
                job_id = result.stdout.strip()
                effective = task.rstrip("\n")  # Existing goal command substitution semantics.
                self.assertEqual((self.home / "jobs" / job_id / "task.txt").read_text(), effective)
                record = next(record for record in self.records() if record["job_id"] == job_id)
                self.assertEqual(record["fingerprint"], hashlib.sha256(("probe\0" + effective).encode()).hexdigest())
                self.assertEqual(self.call_count(), index)

    def test_noncanonical_terminal_codes_do_not_prove_start(self):
        ref = self.prepare()
        self.assertEqual(self.journal("claim", ref, JOB_ID).returncode, 0)
        job = self.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        for text in ("256\n", "9999999999\n", "00\n", "01\n"):
            with self.subTest(text=text):
                (job / "exit").write_text(text)
                self.assertNotEqual(self.goal_command("status", self.goal_id).returncode, 0)
                self.assertEqual(self.records(), [])
        (job / "exit").unlink()
        (job / "meta.json").write_text(json.dumps({"started": "2026-10-01T00:00:00Z"}))
        self.assertEqual(self.status()["reserved_jobs"], 1)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.call_count(), 0)

    def test_job_id_collision_never_claims_existing_evidence(self):
        bins = self.root / "collision-bin"
        bins.mkdir()
        real = shutil.which("mkdir", path=self.env["PATH"])
        wrapper = bins / "mkdir"
        wrapper.write_text("#!/bin/sh\n"
            'for arg in "$@"; do case "$arg" in */jobs/2*)\n' +
            shlex.quote(real) + ' -p "$arg"\n'
            'printf "123\\n" > "$arg/pid"\n'
            'printf "existing job\\n" > "$arg/sentinel"\n'
            ';; esac; done\nexec ' + shlex.quote(real) + ' "$@"\n')
        wrapper.chmod(0o755)
        result = self.dispatch(env=dict(self.env, PATH=str(bins) + os.pathsep + self.env["PATH"]))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.status()["spent_jobs"], 0)
        self.assertEqual(self.status()["reserved_jobs"], 0)
        job, = (self.home / "jobs").iterdir()
        self.assertEqual((job / "sentinel").read_text(), "existing job\n")
        self.assertEqual((job / "pid").read_text(), "123\n")
        outcome, = (self.goal / "dispatch").glob("*.outcome.json")
        self.assertEqual(json.loads(outcome.read_text())["kind"], "no-launch")

    def test_changed_task_cannot_consume_retained_intent(self):
        ref = self.prepare(task="different authorized task")
        process = self.direct_dispatch(ref)
        _, error = process.communicate(timeout=15)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("does not match its intent", error)
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(self.status()["reserved_jobs"], 1)
        self.assertEqual(list((self.home / "jobs").iterdir()), [])

    def test_two_intents_for_same_job_id_fail_closed(self):
        ref = self.prepare()
        self.assertEqual(self.journal("claim", ref, JOB_ID).returncode, 0)
        job = self.home / "jobs" / JOB_ID
        job.mkdir(parents=True)
        (job / "exit").write_text("0\n")
        self.assertEqual(self.status()["spent_jobs"], 1)
        second = self.prepare()
        self.assertEqual(self.journal("claim", second, JOB_ID).returncode, 0)
        result = self.goal_command("status", self.goal_id)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting goal dispatch job id", result.stderr)
        self.assertEqual(self.call_count(), 0)


if __name__ == "__main__":
    unittest.main()
