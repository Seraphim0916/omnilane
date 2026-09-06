#!/usr/bin/env python3
"""Offline protocol/CLI tests. Fake model names and fake CLIs are NOT live smoke."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-executor-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.bins = self.base / "bin"
        self.bins.mkdir()
        self.marker = self.base / "provider-called"
        for vendor in ("codex", "claude"):
            path = self.bins / vendor
            path.write_text(f"#!/bin/sh\necho called >> '{self.marker}'\nexit 91\n")
            path.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("OMNILANE_") and k not in ("CODEX_BIN", "CLAUDE_BIN")}
        self.env.update(OMNILANE_HOME=str(self.home), PATH=str(self.bins) + os.pathsep + self.env["PATH"],
                        CODEX_BIN=str(self.bins / "codex"), CLAUDE_BIN=str(self.bins / "claude"),
                        OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1")
        (self.home / "routing.local.yaml").write_text(
            "native-unit: codex fixture-model-a high | claude fixture-model-b low\n"
            "native-unknown: codex - high\n"
            "native-no-effort: codex fixture-model-a -\n"
            "native-off: off\n"
            "native-vote: vote codex,claude 2\n")
        self.ctx = {"schema_version": 1, "harness": "fixture-harness", "vendor": "codex",
                    "requirements": {"tools": [], "isolation": "shared-inherited", "lifecycle": "single-shot"},
                    "capabilities": [{"model": "fixture-model-a", "efforts": ["high"],
                                      "modes": ["advise"], "workdirs": [str(ROOT)], "tools": [],
                                      "isolations": ["shared-inherited"], "lifecycles": ["single-shot"]}]}
        self.context = self.base / "capability.json"
        self.save_context()

    def save_context(self):
        self.context.write_text(json.dumps(self.ctx))

    def load_native(self):
        spec = importlib.util.spec_from_file_location(
            "native_under_test", ROOT / "scripts/lib/native.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def native_args(self):
        return SimpleNamespace(
            home=str(self.home), lane="native-unit", vendor="codex",
            model="fixture-model-a", effort="high", workdir=str(ROOT),
            task="fixture task", executor="native", mode="advise",
            context=str(self.context), session="auto", thread="", timeout=600,
            job_timeout="", idle_timeout="", background=False, dry_run=False,
            policy=str(ROOT / "config/aa-model-policy.json"), caller_context=None,
            operator_asserted_human=True, expected_registry_sha256=None,
            expected_caller_sha256=None, target_config=None,
        )

    def run_cli(self, script, args, expected=0, input=None):
        result = subprocess.run(["bash", str(ROOT / script), *args], cwd=ROOT, env=self.env,
                                input=input, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def route(self, *flags, context=True, expected=0, lane="native-unit", task="fixture task"):
        args = ["--workdir", str(ROOT)]
        if context:
            args += ["--native-context", str(self.context)]
        return self.run_cli("scripts/dispatch.sh", [*args, *flags, lane, task], expected)

    def job(self, action, job_id, *rest, expected=0, json_mode=True):
        flags = ["--json"] if json_mode else []
        return self.run_cli("scripts/jobs.sh", [*flags, action, job_id, *rest], expected)

    def pending(self):
        result = json.loads(self.route().stdout)
        self.assertEqual(result["state"], "pending")
        self.assertFalse(self.marker.exists())
        return result

    def completion(self, plan):
        value = {"schema_version": 1, "job_id": plan["job_id"], "agent_id": "/root/fixture-agent-1",
                 "runtime": {k: plan[k] for k in ("vendor", "model", "effort", "harness")},
                 "outcome": "success", "result": "fixture result", "evidence": ["fixture:test passed"]}
        value["runtime"]["backend"] = "fixture-backend"
        return value

    def ingest(self, plan, value=None, expected=0):
        path = self.base / "completion.json"
        path.write_text(json.dumps(value or self.completion(plan)))
        return self.job("complete-native", plan["job_id"], str(path), expected=expected)

    def assert_no_jobs(self):
        self.assertFalse(any((self.home / "jobs").glob("*")))
        self.assertFalse(self.marker.exists())

    def test_native_eligible_and_pending(self):
        plan = self.pending()
        self.assertEqual(plan["model"], "fixture-model-a")
        self.assertEqual(plan["executor_reason"], "exact-capability-match")
        self.assertEqual(plan["requirements"]["isolation"], "shared-inherited")
        self.assertEqual(plan["mode"], "advise")
        self.assertIsNone(plan["agent_id"])
        status = json.loads(self.job("status", plan["job_id"]).stdout)
        self.assertEqual(status["job"]["state"], "pending")
        self.job("result", plan["job_id"], expected=2)

    def test_hard_isolation_is_not_silently_downgraded(self):
        original = copy.deepcopy(self.ctx)
        for mode, isolation in (("advise", "read-only"),
                                ("work", "workspace-write")):
            with self.subTest(mode=mode, isolation=isolation):
                self.ctx = copy.deepcopy(original)
                self.ctx["requirements"]["isolation"] = isolation
                self.ctx["capabilities"][0]["isolations"] = [isolation]
                self.ctx["capabilities"][0]["modes"] = [mode]
                self.save_context()
                mode_flags = () if mode == "advise" else ("--mode", mode)
                auto = self.route(*mode_flags, "--dry-run")
                self.assertIn("executor=cli", auto.stdout)
                self.assertIn("unsupported-isolation-or-lifecycle", auto.stdout)
                forced = self.route(*mode_flags, "--executor", "native", expected=2)
                self.assertIn("unsupported-isolation-or-lifecycle", forced.stderr)
                self.assert_no_jobs()
        self.ctx = original
        self.save_context()

    def test_terminal_auto_legacy_cli(self):
        result = self.route("--dry-run", context=False)
        self.assertIn("executor=cli", result.stdout)
        self.assertIn("no-native-context", result.stdout)
        self.assert_no_jobs()

    def test_forced_cli(self):
        result = self.route("--executor", "cli", "--dry-run")
        self.assertIn("executor=cli", result.stdout)
        self.assertIn("forced-cli", result.stdout)
        self.assert_no_jobs()

    def test_forced_native_missing_context(self):
        result = self.route("--executor", "native", context=False, expected=2)
        self.assertIn("no-native-context", result.stderr)
        self.assert_no_jobs()

    def test_exact_mismatches(self):
        original = copy.deepcopy(self.ctx)
        for key, value, reason in (("model", "different-model", "model"),
                                   ("efforts", ["low"], "effort"),
                                   ("modes", ["work"], "mode"),
                                   ("workdirs", [str(self.base)], "workdir"),
                                   ("isolations", ["none"], "isolation"),
                                   ("lifecycles", ["durable"], "lifecycle")):
            with self.subTest(key=key):
                self.ctx = copy.deepcopy(original)
                self.ctx["capabilities"][0][key] = value
                self.save_context()
                result = self.route("--executor", "native", expected=2)
                self.assertIn(reason + "-mismatch", result.stderr)
                auto = self.route("--dry-run")
                self.assertIn("vendor=codex", auto.stdout)
                self.assertIn("model=fixture-model-a", auto.stdout)
                self.assertIn(reason + "-mismatch", auto.stdout)
        self.assert_no_jobs()

    def test_vendor_mismatch_and_explicit_vendor_preserved(self):
        self.ctx["vendor"] = "claude"
        self.save_context()
        self.assertIn("vendor-mismatch", self.route("--executor", "native", expected=2).stderr)
        result = self.route("--vendor", "codex", "--dry-run")
        self.assertIn("vendor=codex", result.stdout)
        self.assert_no_jobs()

    def test_explicit_supported_model_and_effort_preserved(self):
        self.ctx["capabilities"][0].update(model="explicit-model", efforts=["max"])
        self.save_context()
        plan = json.loads(self.route("--model", "explicit-model", "--effort", "max").stdout)
        self.assertEqual((plan["model"], plan["effort"]), ("explicit-model", "max"))

    def test_current_model_inherited_only_when_known(self):
        self.route("--executor", "native", lane="native-unknown", expected=2)
        self.ctx["current_model"] = "fixture-model-a"
        self.save_context()
        plan = json.loads(self.route(lane="native-unknown").stdout)
        self.assertEqual(plan["model"], "fixture-model-a")

    def test_unspecified_effort_is_not_native_capability(self):
        self.ctx["capabilities"][0]["efforts"] = ["-"]
        self.save_context()
        result = self.route("--executor", "native", lane="native-no-effort", expected=2)
        self.assertIn("unknown-effort", result.stderr)
        result = self.route("--dry-run", lane="native-no-effort")
        self.assertIn("executor=cli", result.stdout)
        self.assertIn("effort=-", result.stdout)
        self.assert_no_jobs()

    def test_work_mode_explicit_workdir(self):
        self.ctx["capabilities"][0].update(modes=["work"])
        self.save_context()
        plan = json.loads(self.route("--mode", "work").stdout)
        self.assertEqual(plan["mode"], "work")
        self.assertEqual(plan["workdir"], str(ROOT.resolve()))

    def test_cli_only_lifecycles(self):
        for flags in (("--background",), ("--background", "--live"),
                      ("--thread", "fixture-thread"), ("--background", "--single-shot"),
                      ("--mode", "sysops"), ("--job-timeout", "60"), ("--idle-timeout", "60")):
            with self.subTest(flags=flags):
                self.route("--executor", "native", *flags, expected=2)
                result = self.route("--dry-run", *flags)
                self.assertIn("executor=cli", result.stdout)
        self.assert_no_jobs()

    def test_vote_rejects_forced_native(self):
        result = self.route("--executor", "native", lane="native-vote", expected=2)
        self.assertIn("cli-only-routing-kind", result.stderr)
        self.assert_no_jobs()

    def test_off_route_never_becomes_native(self):
        self.route("--executor", "native", lane="native-off", expected=2)
        legacy = self.route("--dry-run", lane="native-off", context=False, expected=3)
        aware = self.route("--dry-run", lane="native-off", expected=3)
        self.assertIn("disabled", legacy.stderr)
        self.assertIn("disabled", aware.stderr)
        self.assert_no_jobs()

    def test_requirements_reject_unsupported_lifecycle_isolation(self):
        for key, value in (("isolation", "unrestricted"), ("lifecycle", "durable")):
            with self.subTest(key=key):
                original = self.ctx["requirements"][key]
                self.ctx["requirements"][key] = value
                self.save_context()
                self.route("--executor", "native", expected=2)
                self.ctx["requirements"][key] = original
        self.assert_no_jobs()

    def test_malformed_context_fails_closed(self):
        for content in ("{", "[]", '{"schema_version":1,"schema_version":1}',
                        json.dumps({**self.ctx, "secret_field": "not retained"}),
                        json.dumps({**self.ctx, "schema_version": True})):
            with self.subTest(content=content[:30]):
                self.context.write_text(content)
                self.route(expected=2)
        self.assert_no_jobs()

    def test_native_dry_run_no_state_or_stdin_consumption(self):
        plan = json.loads(self.route("--dry-run", task="-").stdout)
        self.assertEqual(plan["state"], "planned")
        self.assertIsNone(plan["job_id"])
        self.assertEqual(plan["task"], "-")
        self.assert_no_jobs()

    def test_valid_completion_and_duplicate(self):
        plan = self.pending()
        self.ingest(plan)
        result = json.loads(self.job("result", plan["job_id"]).stdout)
        self.assertEqual(result["job"]["completion"]["result"], "fixture result")
        self.assertEqual(result["job"]["agent_id"], "/root/fixture-agent-1")
        self.ingest(plan, expected=2)
        self.job("cancel", plan["job_id"], expected=2)

    def test_invalid_completion_does_not_finish(self):
        plan = self.pending()
        original = self.completion(plan)
        for key in ("model", "effort", "vendor", "harness"):
            value = copy.deepcopy(original)
            value["runtime"][key] = "wrong"
            self.ingest(plan, value, expected=2)
        for key, value in (("job_id", "wrong"), ("agent_id", ""), ("evidence", []),
                           ("outcome", "pending"), ("schema_version", True)):
            changed = copy.deepcopy(original)
            changed[key] = value
            self.ingest(plan, changed, expected=2)
        original["raw_logs"] = "not accepted"
        self.ingest(plan, original, expected=2)
        state = json.loads(self.job("status", plan["job_id"]).stdout)
        self.assertEqual(state["job"]["state"], "pending")

    def test_failed_completion(self):
        plan = self.pending()
        value = self.completion(plan)
        value["outcome"] = "failure"
        self.ingest(plan, value)
        result = json.loads(self.job("result", plan["job_id"], expected=1).stdout)
        self.assertEqual(result["job"]["exit_code"], 1)

    def test_pending_cancel_ignores_pid(self):
        plan = self.pending()
        (self.home / "jobs" / plan["job_id"] / "pid").write_text(str(os.getpid()))
        self.job("cancel", plan["job_id"])
        state = json.loads(self.job("status", plan["job_id"]).stdout)
        self.assertEqual(state["job"]["state"], "cancelled")
        self.ingest(plan, expected=2)
        self.job("result", plan["job_id"], expected=143)

    def test_jobs_list_includes_pending_done_cancelled(self):
        plans = [self.pending() for _ in range(3)]
        self.ingest(plans[1])
        self.job("cancel", plans[2]["job_id"])
        result = self.run_cli("scripts/jobs.sh", ["--json", "list"])
        states = {v["id"]: v["state"] for v in json.loads(result.stdout)["jobs"]}
        self.assertEqual([states[p["job_id"]] for p in plans], ["pending", "done", "cancelled"])
        result = self.run_cli("scripts/jobs.sh", ["--json", "list", "--status", "pending"])
        self.assertEqual(len(json.loads(result.stdout)["jobs"]), 1)

    def test_pending_rejects_cli_lifecycle_commands(self):
        plan = self.pending()
        for action in ("retry", "rm", "send", "close", "wait", "watch"):
            self.job(action, plan["job_id"], expected=2, json_mode=False)

    def test_cli_prune_does_not_remove_native_record(self):
        plan = self.pending()
        directory = self.home / "jobs" / plan["job_id"]
        (directory / "exit").write_text("0\n")
        self.run_cli("scripts/jobs.sh", ["prune", "--keep", "0", "--apply"])
        self.assertTrue(directory.is_dir())
        self.assertEqual(json.loads(self.job("status", plan["job_id"]).stdout)["job"]["state"], "pending")

    def test_unsafe_input_paths(self):
        original = self.base / "real-context.json"
        self.context.rename(original)
        self.context.symlink_to(original)
        self.route(expected=2)
        self.assert_no_jobs()

    def test_no_cross_row_capability_union(self):
        other = copy.deepcopy(self.ctx["capabilities"][0])
        other["efforts"] = ["low"]
        other["tools"] = ["read"]
        self.ctx["requirements"]["tools"] = ["read"]
        self.ctx["capabilities"][0]["tools"] = []
        self.ctx["capabilities"].append(other)
        self.save_context()
        self.route("--executor", "native", expected=2)
        self.assert_no_jobs()

    def test_model_not_substituted_on_cli_fallback(self):
        self.ctx["capabilities"][0]["efforts"] = ["low"]
        self.save_context()
        self.env["CODEX_BIN"] = str(self.base / "missing-codex")
        result = self.route("--dry-run", "--model", "explicit-model", expected=4)
        self.assertIn("codex", result.stderr)
        self.assert_no_jobs()

    def test_native_does_not_require_cli_executable(self):
        self.env["CODEX_BIN"] = str(self.base / "missing-codex")
        self.pending()

    def test_cli_exec_regression(self):
        script = self.base / "fixture-runner.sh"
        script.write_text("#!/bin/sh\nprintf 'fixture-cli-result\\n'\n")
        script.chmod(0o755)
        with (self.home / "routing.local.yaml").open("a") as out:
            out.write(f'native-exec: exec "{script}" -\n')
        result = self.route("--executor", "cli", context=False, lane="native-exec")
        self.assertIn("fixture-cli-result", result.stdout)

    def test_concurrent_completion_has_one_winner(self):
        plan = self.pending()
        path = self.base / "completion.json"
        path.write_text(json.dumps(self.completion(plan)))
        args = ["bash", str(ROOT / "scripts/jobs.sh"), "--json", "complete-native", plan["job_id"], str(path)]
        processes = [subprocess.Popen(args, env=self.env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            process.communicate(timeout=10)
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2])
        self.job("result", plan["job_id"])

    def test_job_is_invisible_to_list_until_fully_initialized(self):
        native = self.load_native()
        initialized_lock = threading.Event()
        release_creation = threading.Event()
        errors = []
        original_write_new = native.write_new

        def blocking_write(path, content):
            original_write_new(path, content)
            if path.name == "native.lock":
                initialized_lock.set()
                if not release_creation.wait(5):
                    raise TimeoutError("test timed out waiting to resume publication")

        native.write_new = blocking_write

        def create_job():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    native.route(self.native_args())
            except BaseException as error:
                errors.append(error)

        creator = threading.Thread(target=create_job)
        creator.start()
        try:
            self.assertTrue(initialized_lock.wait(2), "creation never reached first file")
            listed = self.run_cli("scripts/jobs.sh", ["--json", "list"])
            self.assertEqual(json.loads(listed.stdout)["jobs"], [])
        finally:
            release_creation.set()
            creator.join(5)
        self.assertFalse(creator.is_alive())
        self.assertEqual(errors, [])
        jobs = [path for path in (self.home / "jobs").glob("*")
                if path.is_dir() and not path.name.startswith(".")]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(self.job("status", jobs[0].name).stdout)["job"]["state"],
                         "pending")

    def test_interrupted_initialization_leaves_no_public_or_staging_job(self):
        native = self.load_native()
        original_write_new = native.write_new

        def interrupted_write(path, content):
            if path.name == "meta.json":
                raise OSError("fixture interruption")
            original_write_new(path, content)

        native.write_new = interrupted_write
        with self.assertRaises((OSError, ValueError)):
            with contextlib.redirect_stdout(io.StringIO()):
                native.route(self.native_args())
        jobs = self.home / "jobs"
        self.assertEqual(list(jobs.glob("*")), [])
        self.assertEqual(list(jobs.glob(".native-stage-*")), [])
        listed = self.run_cli("scripts/jobs.sh", ["--json", "list"])
        self.assertEqual(json.loads(listed.stdout)["jobs"], [])

    def test_publication_collision_preserves_unrelated_final_directory(self):
        native = self.load_native()
        real_datetime = native.datetime.datetime

        class FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 7, 1, 2, 3, tzinfo=tz)

        native.datetime.datetime = FixedDateTime
        native.secrets.randbelow = lambda _limit: 7
        job_id = f"20260907-010203-{os.getpid()}-7"
        final = self.home / "jobs" / job_id
        final.parent.mkdir()
        final.mkdir(mode=0o700)
        marker = final / "unrelated"
        marker.write_text("preserve me")

        with self.assertRaises((OSError, ValueError)):
            with contextlib.redirect_stdout(io.StringIO()):
                native.route(self.native_args())
        self.assertEqual(marker.read_text(), "preserve me")
        self.assertEqual(list(final.parent.glob(".native-stage-*")), [])

    def test_input_and_state_permissions(self):
        plan = self.pending()
        directory = self.home / "jobs" / plan["job_id"]
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(p.name for p in directory.iterdir()),
                         {"native.json", "meta.json", "task.txt", "native.lock", "aa-lineage.json", "aa-registry.json", "aa-decision.json"})
        self.assertNotIn("capabilities", json.loads((directory / "native.json").read_text()))

    def test_stdin_task_and_no_shell_interpolation(self):
        task = f"literal $(touch {self.marker}) `false`\nsecond line"
        args = ["--native-context", str(self.context), "--workdir", str(ROOT), "native-unit", "-"]
        plan = json.loads(self.run_cli("scripts/dispatch.sh", args, input=task).stdout)
        self.assertEqual(plan["task"], task)
        self.assertFalse(self.marker.exists())

    def test_malformed_completion_and_symlink_leave_pending(self):
        plan = self.pending()
        path = self.base / "bad-completion.json"
        for raw in ("{", "[]", '{"schema_version":1,"schema_version":1}', "x" * 262145):
            path.write_text(raw)
            self.job("complete-native", plan["job_id"], str(path), expected=2)
        path.unlink()
        path.symlink_to(self.context)
        self.job("complete-native", plan["job_id"], str(path), expected=2)
        self.assertEqual(json.loads(self.job("status", plan["job_id"]).stdout)["job"]["state"], "pending")

    def test_invalid_flags_do_not_create_state(self):
        self.route("--executor", "unknown", expected=2)
        self.route("--timeout", "no", expected=2)
        self.route("--native-context", "", expected=2)
        self.route("--workdir", str(self.base / "missing"), expected=2)
        self.route("--vendor", "gemini", expected=2)
        self.assert_no_jobs()

    def test_symlink_store_and_job_are_rejected(self):
        jobs = self.home / "jobs"
        jobs.symlink_to(self.base)
        self.route(expected=2)
        jobs.unlink()
        plan = self.pending()
        directory = jobs / plan["job_id"]
        directory.rename(self.base / "moved-job")
        directory.symlink_to(self.base / "moved-job")
        self.job("status", plan["job_id"], expected=2)

    def test_known_current_model_kept_on_cli_fallback(self):
        self.ctx["current_model"] = "fixture-model-a"
        self.ctx["capabilities"][0]["efforts"] = ["low"]
        self.save_context()
        result = self.route("--dry-run", lane="native-unknown")
        self.assertIn("model=fixture-model-a", result.stdout)
        self.assertIn("effort-mismatch", result.stdout)
        self.assert_no_jobs()


    def reuse_context(self):
        self.ctx.update(agent_strategy="reuse", current_model="fixture-model-a", current_effort="high",
                        preserve_existing_context=True, new_agent_capacity="exhausted",
                        existing_agent={"agent_id": "/root/fixture-existing", "vendor": "codex",
                                        "model": "fixture-model-a", "effort": "high",
                                        "harness": "fixture-harness", "state": "idle",
                                        "observed_by": "caller", "evidence": ["fixture:creation and idle observed"]})
        self.ctx["capabilities"][0].update(agent_strategy="reuse", existing_agent_id="/root/fixture-existing")
        self.save_context()

    def reuse_completion(self, plan):
        value = self.completion(plan)
        value.update(agent_strategy="reuse", agent_id="/root/fixture-existing")
        value["runtime"]["backend"] = "collaboration.followup_task"
        return value

    def test_explicit_idle_reuse_handoff_and_completion(self):
        import hashlib
        self.reuse_context()
        plan = self.pending()
        self.assertEqual(plan["agent_strategy"], "reuse")
        self.assertEqual(plan["agent_id"], "/root/fixture-existing")
        self.assertEqual(plan["existing_agent_id"], "/root/fixture-existing")
        self.assertTrue(plan["preserve_existing_context"])
        self.assertEqual(plan["worker_contract"]["backend"], "collaboration.followup_task")
        self.assertEqual(plan["worker_contract"]["isolation"], "shared-inherited")
        frozen = self.home / "jobs" / plan["job_id"] / "aa-registry.json"
        self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(),
                         hashlib.sha256((ROOT / "config/aa-model-policy.json").read_bytes()).hexdigest())
        value = self.reuse_completion(plan)
        self.ingest(plan, value)
        result = json.loads(self.job("result", plan["job_id"]).stdout)["job"]
        self.assertEqual(result["agent_strategy"], "reuse")
        self.assertEqual(result["completion"]["runtime"]["backend"], "collaboration.followup_task")
        self.ingest(plan, value, expected=2)

    def test_reuse_rejects_busy_unknown_and_missing_preservation(self):
        self.reuse_context()
        original = copy.deepcopy(self.ctx)
        for agent_state in ("busy", "unknown"):
            self.ctx = copy.deepcopy(original)
            self.ctx["existing_agent"]["state"] = agent_state
            self.save_context()
            self.route("--executor", "native", expected=2)
            result = self.route("--dry-run")
            self.assertIn("executor=cli", result.stdout)
            self.assertIn("model=fixture-model-a", result.stdout)
        for preserve in (False, None):
            self.ctx = copy.deepcopy(original)
            if preserve is None:
                self.ctx.pop("preserve_existing_context")
            else:
                self.ctx["preserve_existing_context"] = preserve
            self.save_context()
            self.route("--executor", "native", expected=2)
        self.assert_no_jobs()

    def test_reuse_exact_identity_and_capability_binding(self):
        self.reuse_context()
        original = copy.deepcopy(self.ctx)
        for key in ("vendor", "model", "effort", "harness"):
            self.ctx = copy.deepcopy(original)
            self.ctx["existing_agent"][key] = "different"
            self.save_context()
            self.route("--executor", "native", expected=2)
        for key in ("current_model", "current_effort"):
            self.ctx = copy.deepcopy(original)
            self.ctx[key] = "different"
            self.save_context()
            self.route("--executor", "native", expected=2)
        self.ctx = copy.deepcopy(original)
        self.ctx["capabilities"][0]["existing_agent_id"] = "/root/different"
        self.save_context()
        self.route("--executor", "native", expected=2)
        self.assert_no_jobs()

    def test_reuse_requires_explicit_strategy_and_evidence(self):
        self.reuse_context()
        original = copy.deepcopy(self.ctx)
        for remove in ("agent_strategy", "existing_agent"):
            self.ctx = copy.deepcopy(original); self.ctx.pop(remove)
            self.save_context()
            self.route("--executor", "native", expected=2)
        self.ctx = copy.deepcopy(original)
        self.ctx["existing_agent"]["evidence"] = []
        self.save_context()
        self.route("--executor", "native", expected=2)
        self.ctx = copy.deepcopy(original)
        self.ctx["capabilities"][0].pop("agent_strategy")
        self.save_context()
        self.route("--executor", "native", expected=2)
        self.assert_no_jobs()

    def test_reuse_completion_rejects_wrong_agent_strategy_and_backend(self):
        self.reuse_context(); plan = self.pending()
        for field, replacement in (("agent_id", "/root/other"), ("agent_strategy", "new")):
            value = self.reuse_completion(plan); value[field] = replacement
            self.ingest(plan, value, expected=2)
        value = self.reuse_completion(plan); value.pop("agent_strategy")
        self.ingest(plan, value, expected=2)
        value = self.reuse_completion(plan); value["runtime"]["backend"] = "collaboration.spawn_agent"
        self.ingest(plan, value, expected=2)
        self.assertEqual(json.loads(self.job("status", plan["job_id"]).stdout)["job"]["state"], "pending")

    def test_reuse_preserves_isolation_and_lifecycle_rejection(self):
        self.reuse_context()
        original = copy.deepcopy(self.ctx)
        for isolation in ("read-only", "workspace-write"):
            self.ctx = copy.deepcopy(original)
            self.ctx["requirements"]["isolation"] = isolation
            self.save_context(); self.route("--executor", "native", expected=2)
        self.ctx = original; self.save_context()
        self.route("--executor", "native", "--background", expected=2)
        self.route("--executor", "native", "--live", expected=2)
        self.assert_no_jobs()

    def test_known_exhausted_new_capacity_never_implies_reuse(self):
        self.ctx["new_agent_capacity"] = "exhausted"
        self.save_context()
        self.route("--executor", "native", expected=2)
        fallback = self.route("--dry-run")
        self.assertIn("new-agent-capacity-exhausted", fallback.stdout)
        self.assertIn("model=fixture-model-a", fallback.stdout)
        self.assert_no_jobs()

    def test_existing_new_agent_contract_remains_new(self):
        plan = self.pending()
        self.assertEqual(plan["agent_strategy"], "new")
        self.assertIsNone(plan["existing_agent_id"])
        self.assertIsNone(plan["agent_id"])
        self.ingest(plan)


if __name__ == "__main__":
    unittest.main()
