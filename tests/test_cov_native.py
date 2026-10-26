import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import native  # noqa: E402

POLICY = str(ROOT / "config/aa-model-policy.json")
JOB_ID = "20261001-120000-1-2"


class NativeFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-cov-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / "home"
        self.workdir = self.base / "work"
        self.workdir.mkdir()
        self.ctx = {"schema_version": 1, "harness": "fixture-harness", "vendor": "codex",
                    "requirements": {"tools": ["shell"], "isolation": "shared-inherited",
                                     "lifecycle": "single-shot"},
                    "capabilities": [self.cap()]}

    def cap(self, **changes):
        value = {"model": "fixture-model-a", "efforts": ["high"], "modes": ["advise"],
                 "workdirs": [str(self.workdir)], "tools": ["shell"],
                 "isolations": ["shared-inherited"], "lifecycles": ["single-shot"]}
        value.update(changes)
        return value

    def args(self, **changes):
        value = dict(home=str(self.home), lane="native-unit", vendor="codex",
                     model="fixture-model-a", effort="high", workdir=str(self.workdir),
                     task="fixture task", executor="auto", mode="advise", context=None,
                     session="auto", thread="", timeout=600, job_timeout="", idle_timeout="",
                     background=False, dry_run=False, policy=POLICY, caller_context=None,
                     operator_asserted_human=True, expected_registry_sha256=None,
                     expected_caller_sha256=None, target_config=None)
        value.update(changes)
        return SimpleNamespace(**value)

    def reuse_ctx(self):
        ctx = copy.deepcopy(self.ctx)
        ctx.update(agent_strategy="reuse", preserve_existing_context=True,
                   current_model="fixture-model-a", current_effort="high",
                   existing_agent={"agent_id": "/root/agent-1", "vendor": "codex",
                                   "model": "fixture-model-a", "effort": "high",
                                   "harness": "fixture-harness", "state": "idle",
                                   "observed_by": "caller", "evidence": ["list_agents"]})
        ctx["capabilities"] = [self.cap(agent_strategy="reuse", existing_agent_id="/root/agent-1")]
        return ctx

    def write_json(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value))
        return path

    def run_route(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native.route(args)
        return code, stdout.getvalue(), stderr.getvalue()


class ChooseTests(NativeFixture):
    def reason(self, ctx="default", **changes):
        ctx = self.ctx if ctx == "default" else ctx
        executor, reason, _model = native.choose(self.args(**changes), ctx)
        return executor, reason

    def test_routing_and_lifecycle_refusals_come_before_capabilities(self):
        cases = [
            (dict(executor="cli"), "forced-cli"),
            (dict(vendor="vote"), "cli-only-routing-kind"),
            (dict(vendor="off"), "cli-only-routing-kind"),
            (dict(background=True), "cli-session-lifecycle"),
            (dict(session="new"), "cli-session-lifecycle"),
            (dict(thread="t1"), "cli-session-lifecycle"),
            (dict(job_timeout="30"), "cli-watchdog-required"),
            (dict(idle_timeout="30"), "cli-watchdog-required"),
            (dict(mode="sysops"), "sysops-requires-cli"),
            (dict(vendor="claude"), "vendor-mismatch"),
            (dict(model="other-model"), "model-mismatch"),
            (dict(model="-"), "unknown-current-model"),
            (dict(effort="-"), "unknown-effort"),
            (dict(effort=""), "unknown-effort"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.reason(**changes), ("cli", expected))
        self.assertEqual(self.reason(ctx=None), ("cli", "no-native-context"))

    def test_unknown_model_inherits_the_host_current_model(self):
        ctx = dict(self.ctx, current_model="fixture-model-a")
        self.assertEqual(native.choose(self.args(model=""), ctx),
                         ("native", "exact-capability-match", "fixture-model-a"))
        self.assertEqual(native.choose(self.args(model="-", effort="-"), ctx),
                         ("cli", "unknown-effort", "fixture-model-a"))

    def test_isolation_and_new_agent_refusals(self):
        for key, value in (("isolation", "sandboxed"), ("lifecycle", "persistent")):
            ctx = copy.deepcopy(self.ctx)
            ctx["requirements"][key] = value
            self.assertEqual(self.reason(ctx), ("cli", "unsupported-isolation-or-lifecycle"))
        for extra in ({"preserve_existing_context": True},
                      {"existing_agent": self.reuse_ctx()["existing_agent"]}):
            self.assertEqual(self.reason(dict(self.ctx, **extra)), ("cli", "reuse-strategy-required"))
        self.assertEqual(self.reason(dict(self.ctx, new_agent_capacity="exhausted")),
                         ("cli", "new-agent-capacity-exhausted"))
        ctx = dict(self.ctx, capabilities=[self.cap(existing_agent_id="/root/agent-1")])
        self.assertEqual(self.reason(ctx), ("cli", "agent-strategy-capability-mismatch"))

    def test_capability_checks_narrow_one_row_and_never_union_rows(self):
        cases = [
            (self.cap(efforts=["low"]), "effort-mismatch"),
            (self.cap(modes=["work"]), "mode-mismatch"),
            (self.cap(workdirs=[str(self.base)]), "workdir-mismatch"),
            (self.cap(tools=[]), "tools-mismatch"),
            (self.cap(isolations=["os-sandbox"]), "isolation-mismatch"),
            (self.cap(lifecycles=["persistent"]), "lifecycle-mismatch"),
        ]
        for cap, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.reason(dict(self.ctx, capabilities=[cap])), ("cli", expected))
        # One row has the effort, another the mode: no single row has both.
        split = [self.cap(modes=["work"]), self.cap(efforts=["low"])]
        self.assertEqual(self.reason(dict(self.ctx, capabilities=split)), ("cli", "mode-mismatch"))
        self.assertEqual(self.reason(), ("native", "exact-capability-match"))

    def test_reuse_requires_an_exact_idle_caller_observed_agent(self):
        self.assertEqual(self.reason(self.reuse_ctx()), ("native", "exact-idle-reuse-match"))

        def mutated(change):
            ctx = self.reuse_ctx()
            change(ctx)
            return ctx

        cases = [
            (lambda c: c.pop("preserve_existing_context"), {}, "reuse-preserve-context-required"),
            (lambda c: c.pop("existing_agent"), {}, "reuse-existing-agent-required"),
            (lambda c: c["existing_agent"].update(state="busy"), {}, "reuse-agent-not-idle"),
            (lambda c: c.update(vendor="claude"), {"vendor": "claude"}, "reuse-backend-unsupported"),
            (lambda c: c.update(current_effort="low"), {}, "reuse-current-runtime-mismatch"),
            (lambda c: c.update(current_model="other"), {}, "reuse-current-runtime-mismatch"),
            (lambda c: c["existing_agent"].update(harness="other"), {}, "reuse-agent-runtime-mismatch"),
            (lambda c: c["capabilities"][0].update(existing_agent_id="/root/agent-2"), {},
             "agent-strategy-capability-mismatch"),
            (lambda c: c["capabilities"][0].pop("agent_strategy"), {},
             "agent-strategy-capability-mismatch"),
        ]
        for change, arg_changes, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.reason(mutated(change), **arg_changes), ("cli", expected))


class ChooseInheritedTests(NativeFixture):
    IDENTITY = {"vendor": "codex", "model": "fixture-model-a"}

    def inherited_ctx(self, **changes):
        ctx = copy.deepcopy(self.ctx)
        ctx.update(inherits_caller_runtime=True, **changes)
        return ctx

    def test_effort_is_not_matched_and_every_refusal_has_a_reason(self):
        ctx = self.inherited_ctx(capabilities=[self.cap(efforts=["low"])])
        self.assertEqual(native.choose_inherited(self.args(), ctx, self.IDENTITY),
                         ("native", "inherited-caller-runtime"))
        cases = [
            (None, {}, "no-native-context"),
            (dict(self.ctx), {}, "host-does-not-assert-runtime-inheritance"),
            (self.inherited_ctx(), dict(background=True), "cli-session-lifecycle"),
            (self.inherited_ctx(), dict(job_timeout="9"), "cli-watchdog-required"),
            (self.inherited_ctx(), dict(mode="sysops"), "sysops-requires-cli"),
            (self.inherited_ctx(vendor="claude"), {}, "vendor-mismatch"),
            (self.inherited_ctx(current_model="other"), {}, "current-model-mismatch"),
            (self.inherited_ctx(agent_strategy="reuse"), {}, "inherit-spawns-a-new-agent"),
            (self.inherited_ctx(existing_agent={}), {}, "inherit-spawns-a-new-agent"),
            (self.inherited_ctx(new_agent_capacity="exhausted"), {}, "new-agent-capacity-exhausted"),
            (self.inherited_ctx(requirements={"tools": [], "isolation": "x", "lifecycle": "single-shot"}),
             {}, "unsupported-isolation-or-lifecycle"),
            (self.inherited_ctx(capabilities=[self.cap(modes=["work"])]), {},
             "no-capability-row-for-caller-model-mode-and-workdir"),
            (self.inherited_ctx(capabilities=[self.cap(agent_strategy="reuse")]), {},
             "no-capability-row-for-caller-model-mode-and-workdir"),
        ]
        for ctx, changes, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(native.choose_inherited(self.args(**changes), ctx, self.IDENTITY),
                                 (None, expected))


class ContextValidationTests(NativeFixture):
    def reject(self, ctx, message):
        with self.assertRaises(ValueError) as caught:
            native.capability_context(None, ctx)
        self.assertEqual(str(caught.exception), message)

    def mutated(self, change):
        ctx = copy.deepcopy(self.ctx)
        change(ctx)
        return ctx

    def test_valid_context_canonicalizes_workdirs(self):
        link = self.base / "link"
        link.symlink_to(self.workdir)
        ctx = self.mutated(lambda c: c["capabilities"][0].update(workdirs=[str(link)]))
        self.assertEqual(native.capability_context(None, ctx)["capabilities"][0]["workdirs"],
                         [str(self.workdir)])
        path = self.write_json("ctx.json", self.reuse_ctx())
        self.assertEqual(native.capability_context(str(path))["agent_strategy"], "reuse")

    def test_top_level_refusals(self):
        cases = [
            (lambda c: c.update(unknown=1), "missing or unknown JSON fields"),
            (lambda c: c.pop("harness"), "missing or unknown JSON fields"),
            (lambda c: c.update(inherits_caller_runtime=1), "invalid inherits_caller_runtime"),
            (lambda c: c.update(caller_identity_verified="yes"), "invalid caller_identity_verified"),
            (lambda c: c.update(schema_version=2), "unsupported context version"),
            (lambda c: c.update(schema_version=True), "unsupported context version"),
            (lambda c: c.update(harness="-bad"), "invalid harness"),
            (lambda c: c.update(vendor=" "), "invalid vendor"),
            (lambda c: c.update(current_model="a\x1bb"), "control character in current_model"),
            (lambda c: c.update(current_effort="x" * 257), "invalid current_effort"),
            (lambda c: c.update(agent_strategy="fork"), "invalid agent strategy"),
            (lambda c: c.update(new_agent_capacity="lots"), "invalid new agent capacity"),
            (lambda c: c.update(preserve_existing_context="true"), "invalid preserve_existing_context"),
            (lambda c: c["requirements"].update(extra=1), "missing or unknown JSON fields"),
            (lambda c: c["requirements"].update(tools=["a", "a"]), "duplicate required tools"),
            (lambda c: c["requirements"].update(isolation="a b"), "invalid isolation"),
            (lambda c: c.update(capabilities=[]), "invalid capabilities"),
            (lambda c: c.update(capabilities=[c["capabilities"][0]] * 129), "invalid capabilities"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                self.reject(self.mutated(change), message)

    def test_existing_agent_and_capability_row_refusals(self):
        def agent(**changes):
            return lambda c: c["existing_agent"].update(**changes)

        cases = [
            (agent(agent_id="-x"), "invalid existing agent id"),
            (agent(agent_id="a\x7fb"), "control character in existing agent id"),
            (agent(model=""), "invalid existing agent model"),
            (agent(state="running"), "invalid existing agent state"),
            (agent(observed_by="host"), "existing agent must be caller-observed"),
            (agent(evidence=[]), "invalid existing agent evidence"),
            (lambda c: c["existing_agent"].pop("evidence"), "missing or unknown JSON fields"),
            (lambda c: c["capabilities"][0].update(agent_strategy="fork"), "invalid capability strategy"),
            (lambda c: c["capabilities"][0].update(existing_agent_id=""),
             "invalid capability existing agent id"),
            (lambda c: c["capabilities"][0].update(efforts=[]), "invalid efforts"),
            (lambda c: c["capabilities"][0].update(modes=["a", "a"]), "duplicate modes"),
            (lambda c: c["capabilities"][0].update(tools="shell"), "invalid tools"),
            (lambda c: c["capabilities"][0].update(workdirs=["relative"]),
             "workdir must be an existing absolute directory"),
            (lambda c: c["capabilities"][0].update(workdirs=[str(self.base / "absent")]),
             "workdir must be an existing absolute directory"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                ctx = self.reuse_ctx()
                change(ctx)
                self.reject(ctx, message)

    def test_text_value_and_string_list_bounds(self):
        self.assertEqual(native.text_value("a\n\tb", "task", multiline=True), "a\n\tb")
        for value, message in (("a\nb", "control character in x"), ("", "invalid x"),
                               (" \n", "invalid x"), ("y" * 5, "invalid x"), (None, "invalid x")):
            with self.assertRaisesRegex(ValueError, message):
                native.text_value(value, "x", limit=4)
        self.assertEqual(native.string_list([], "x", nonempty=False), [])
        with self.assertRaisesRegex(ValueError, "invalid x"):
            native.string_list(["a"] * 129, "x")

    def test_read_json_refuses_duplicates_constants_links_and_special_files(self):
        dup = self.base / "dup.json"
        dup.write_text('{"a": 1, "a": 2}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            native.read_json(dup)
        for constant in ("NaN", "Infinity", "-Infinity"):
            path = self.base / "constant.json"
            path.write_text('{"a": %s}' % constant)
            with self.assertRaisesRegex(ValueError, "invalid JSON number"):
                native.read_json(path)
        link = self.base / "link.json"
        link.symlink_to(dup)
        with self.assertRaises(OSError):
            native.read_json(link)
        os.mkfifo(self.base / "fifo.json")
        with self.assertRaisesRegex(ValueError, "invalid JSON file"):
            native.read_json(self.base / "fifo.json")
        big = self.base / "big.json"
        big.write_text('"' + "x" * native.MAX_BYTES + '"')
        with self.assertRaisesRegex(ValueError, "invalid JSON file"):
            native.read_json(big)


class StoreTests(NativeFixture):
    def test_jobs_root_refuses_links_and_missing_stores(self):
        with self.assertRaisesRegex(ValueError, "missing jobs store"):
            native.jobs_root(self.home)
        root = native.jobs_root(self.home, create=True)
        self.assertEqual(root, self.home / "jobs")
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        alias = self.base / "alias"
        alias.symlink_to(self.home)
        with self.assertRaisesRegex(ValueError, "unsafe jobs store"):
            native.jobs_root(alias)
        root.rmdir()
        root.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, "unsafe jobs store"):
            native.jobs_root(self.home)

    def test_cleanup_staging_dir_removes_only_flat_files(self):
        native.cleanup_staging_dir(self.base / "absent")
        stage = self.base / "stage"
        stage.mkdir()
        (stage / "file").write_text("x")
        (stage / "link").symlink_to(self.workdir)
        native.cleanup_staging_dir(stage)
        self.assertFalse(stage.exists())
        self.assertTrue(self.workdir.is_dir())
        stage.mkdir()
        (stage / "nested").mkdir()
        native.cleanup_staging_dir(stage)
        self.assertTrue((stage / "nested").is_dir())

    def test_publish_job_refuses_a_collision_and_leaves_no_staging(self):
        root = native.jobs_root(self.home, create=True)
        (root / JOB_ID).mkdir()
        plan = {"task": "t"}
        with self.assertRaisesRegex(ValueError, "job ID collision"):
            native.publish_job(root, JOB_ID, plan, {})
        self.assertEqual(sorted(p.name for p in root.iterdir()), [".native-publish.lock", JOB_ID])
        self.assertEqual(list((root / JOB_ID).iterdir()), [])

    def test_atomic_json_bounds_the_public_record(self):
        path = self.base / "state.json"
        native.atomic_json(path, {"a": 1})
        with self.assertRaisesRegex(ValueError, "public record too large"):
            native.atomic_json(path, {"a": "x" * native.MAX_BYTES})
        self.assertEqual(json.loads(path.read_text()), {"a": 1})
        self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(".state")], [])


class RouteTests(NativeFixture):
    def context_args(self, ctx=None, **changes):
        path = self.write_json("ctx.json", self.ctx if ctx is None else ctx)
        return self.args(context=str(path), **changes)

    def test_mutually_exclusive_inputs_are_refused_before_reading_anything(self):
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            native.route(self.args(caller_context="/absent"))
        with self.assertRaisesRegex(ValueError, "--context-from-host is for --inherit only"):
            native.route(self.args(context_from_host=True, inherit=False))

    def test_context_from_host_failure_is_a_refusal_not_a_crash(self):
        with mock.patch.object(native.host_context, "build_context",
                               side_effect=native.host_context.HostContextError("no host file")), \
                self.assertRaisesRegex(ValueError, "host capability file unusable: no host file"):
            native.route(self.args(context_from_host=True, inherit=True))

    def test_cli_choice_returns_10_with_reason_and_native_executor_refuses(self):
        code, out, _ = self.run_route(self.args(executor="cli", dry_run=True))
        self.assertEqual((code, json.loads(out)), (10, {"reason": "forced-cli", "model": "fixture-model-a"}))
        with self.assertRaisesRegex(ValueError, "native capability rejected: model-mismatch"):
            self.run_route(self.context_args(executor="native", model="other-model"))
        self.assertFalse(self.home.exists())

    def test_dry_run_plans_without_job_state(self):
        code, out, _ = self.run_route(self.context_args(dry_run=True, quota_decision='{"ok": true}'))
        plan = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((plan["state"], plan["job_id"], plan["job_state_created"]),
                         ("planned", None, False))
        self.assertEqual(plan["quota_preflight"], {"ok": True})
        self.assertFalse(self.home.exists())

    def test_reuse_plan_targets_the_observed_agent(self):
        code, out, _ = self.run_route(self.context_args(self.reuse_ctx(), dry_run=True))
        plan = json.loads(out)
        self.assertEqual((code, plan["agent_id"], plan["existing_agent_id"]),
                         (0, "/root/agent-1", "/root/agent-1"))
        self.assertEqual(plan["worker_contract"]["backend"], "collaboration.followup_task")
        self.assertEqual(plan["reuse_observation"]["evidence"], ["list_agents"])
        self.assertIs(plan["preserve_existing_context"], True)

    def test_pending_job_reads_task_from_stdin_and_records_quota(self):
        args = self.context_args(task="-", quota_decision='{"decision": "allow"}')
        with mock.patch.object(sys, "stdin", io.StringIO("task from stdin\n")):
            code, out, _ = self.run_route(args)
        plan = json.loads(out)
        job = self.home / "jobs" / plan["job_id"]
        self.assertEqual(code, 0)
        self.assertEqual((job / "task.txt").read_text(), "task from stdin\n")
        self.assertEqual(json.loads((job / "quota-preflight.json").read_text()),
                         {"decision": "allow", "job_id": plan["job_id"]})
        meta = json.loads((job / "meta.json").read_text())
        self.assertEqual((meta["session_mode"], meta["executor"]), ("native", "native"))
        self.assertEqual(json.loads((job / "native.json").read_text())["state"], "pending")

    def test_invalid_task_or_oversized_metadata_creates_no_job_state(self):
        for args, message in ((self.context_args(task="bad\x00task"), "control character in task"),
                              (self.context_args(lane="l" * 5000), "metadata too large")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.run_route(args)
        self.assertFalse(self.home.exists())

    def test_inherited_worker_on_the_hosts_word(self):
        ctx = dict(self.ctx, inherits_caller_runtime=True, current_model="fixture-model-a")
        args = self.context_args(ctx, inherit=True, vendor="", model="", effort="",
                                 operator_asserted_human=False, dry_run=True,
                                 fallback_from_lane=True, fallback_refusal="",
                                 fallback_host_file="/etc/host.toml")
        code, out, err = self.run_route(args)
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual((plan["inherit"], plan["effort"], plan["caller_identity_verified"]),
                         (True, "inherited", False))
        self.assertIsNone(plan["fallback"]["lane_refusal_evidence"])
        self.assertEqual(plan["fallback"]["host_context"]["file"], "/etc/host.toml")
        self.assertIs(plan["worker_contract"]["satisfies_lane_target"], False)

    def test_inherited_worker_refusal_names_the_capability_gate(self):
        ctx = dict(self.ctx, current_model="fixture-model-a")
        args = self.context_args(ctx, inherit=True, vendor="", model="", effort="",
                                 operator_asserted_human=False, dry_run=True)
        code, out, err = self.run_route(args)
        self.assertEqual((code, out), (3, ""))
        refusal = json.loads(err)
        self.assertEqual((refusal["allowed"], refusal["failed_gate"], refusal["code"], refusal["reason"]),
                         (False, "native-capability", "native-inherit-unavailable",
                          "host-does-not-assert-runtime-inheritance"))
        self.assertIsNone(refusal["child_context"])
        self.assertEqual(refusal["next_command"], "omnilane native-context --workdir " + str(self.workdir))


class JobCommandTests(NativeFixture):
    def setUp(self):
        super().setUp()
        path = self.write_json("ctx.json", self.ctx)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(native.route(self.args(context=str(path))), 0)
        self.plan = json.loads(stdout.getvalue())
        self.job_id = self.plan["job_id"]
        self.job = self.home / "jobs" / self.job_id

    def job_args(self, action, input=None, json_mode=False, job_id=None):
        return SimpleNamespace(home=str(self.home), job_id=job_id or self.job_id, action=action,
                               input=input, json=json_mode)

    def run_job(self, action, **kwargs):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = native.job_command(self.job_args(action, **kwargs))
        return code, stdout.getvalue()

    def completion(self, **changes):
        value = {"schema_version": 1, "job_id": self.job_id, "agent_id": "/root/worker-1",
                 "runtime": {"vendor": "codex", "model": "fixture-model-a", "effort": "high",
                             "harness": "fixture-harness", "backend": "collaboration.spawn_agent"},
                 "outcome": "success", "result": "done\nwell", "evidence": ["tests passed"]}
        value.update(changes)
        return value

    def edit_state(self, **changes):
        state = json.loads((self.job / "native.json").read_text())
        state.update(changes)
        for key in [k for k, v in changes.items() if v is None and k in ("created", "timeout")]:
            del state[key]
        (self.job / "native.json").write_text(json.dumps(state))

    def test_pending_job_states(self):
        self.assertEqual(self.run_job("read-state"), (0, "pending\tnull\t\n"))
        self.assertEqual(self.run_job("list-state"), (0, "pending\n"))
        code, out = self.run_job("status")
        summary = json.loads(out)
        self.assertEqual((code, summary["state"], summary["agent_strategy"]), (0, "pending", "new"))
        with self.assertRaisesRegex(ValueError, "native job pending; ingest caller result first"):
            self.run_job("result")
        code, out = self.run_job("status", json_mode=True)
        self.assertEqual(json.loads(out)["job"]["native_state"], "pending")

    def test_completion_is_validated_then_recorded_once(self):
        with self.assertRaisesRegex(ValueError, "completion input file required"):
            self.run_job("complete-native")
        path = self.write_json("done.json", self.completion(outcome="failure"))
        self.assertEqual(self.run_job("complete-native", input=str(path))[0], 0)
        self.assertEqual(self.run_job("read-state"), (0, "done\t1\t\n"))
        self.assertEqual(self.run_job("result"), (1, "done\nwell\n"))
        code, out = self.run_job("result", json_mode=True)
        self.assertEqual(json.loads(out)["job"]["completion"]["agent_id"], "/root/worker-1")
        with self.assertRaisesRegex(ValueError, "native job is already terminal"):
            self.run_job("complete-native", input=str(path))

    def test_cancel_is_terminal_and_takes_no_input(self):
        with self.assertRaisesRegex(ValueError, "unexpected cancel input"):
            self.run_job("cancel", input="/dev/null")
        self.assertEqual(self.run_job("cancel")[0], 0)
        self.assertEqual(self.run_job("list-state"), (0, "cancelled\n"))
        self.assertEqual(self.run_job("result"), (143, "cancelled by caller\n"))
        with self.assertRaisesRegex(ValueError, "native job is already terminal"):
            self.run_job("cancel")

    def test_expired_deadline_is_observed_without_publishing_a_terminal_state(self):
        self.edit_state(created="2020-01-01T00:00:00Z", timeout=1)
        code, out = self.run_job("read-state")
        state, exit_code, reason = out.rstrip("\n").split("\t")
        self.assertEqual((code, state, exit_code), (0, "expired", "null"))
        self.assertEqual(reason, "native handoff deadline 2020-01-01T00:05:01Z exceeded "
                                 "(timeout 1s + grace 300s); no completion recorded")
        self.assertEqual(self.run_job("status"), (0, f"expired ({reason})\n"))
        with self.assertRaisesRegex(ValueError, r"native job expired \(native handoff deadline"):
            self.run_job("result")
        self.assertEqual(json.loads((self.job / "native.json").read_text())["state"], "pending")
        path = self.write_json("late.json", self.completion())
        self.assertEqual(self.run_job("complete-native", input=str(path))[0], 0)
        self.assertEqual(self.run_job("list-state"), (0, "done\n"))

    def test_deadline_inputs_fall_back_to_metadata_and_refuse_bad_values(self):
        self.edit_state(created=None, timeout=None)
        self.assertEqual(self.run_job("list-state"), (0, "pending\n"))
        cases = [({"timeout": 0}, "invalid native timeout"),
                 ({"timeout": True}, "invalid native timeout"),
                 ({"created": "2020-01-01 00:00:00", "timeout": 1}, "invalid native creation time"),
                 ({"created": "2020-13-01T00:00:00Z", "timeout": 1}, "invalid native deadline"),
                 ({"created": "9999-12-31T23:59:59Z", "timeout": 1}, "invalid native deadline")]
        for changes, message in cases:
            with self.subTest(message=message):
                self.edit_state(**changes)
                with self.assertRaisesRegex(ValueError, message):
                    self.run_job("list-state")

    def test_job_reference_and_state_are_validated(self):
        with self.assertRaisesRegex(ValueError, "invalid job ID"):
            self.run_job("status", job_id="../x")
        with self.assertRaisesRegex(ValueError, "invalid job directory"):
            self.run_job("status", job_id=JOB_ID)
        self.edit_state(executor="cli")
        with self.assertRaisesRegex(ValueError, "invalid native state"):
            self.run_job("status")

    def test_inherited_summary_never_satisfies_a_lane(self):
        self.edit_state(inherit=True, caller_identity_verified=False)
        summary = json.loads(self.run_job("status")[1])
        self.assertEqual((summary["inherit"], summary["caller_identity_verified"],
                          summary["satisfies_lane_target"]), (True, False, False))


class CompletionValidationTests(unittest.TestCase):
    STATE = {"job_id": JOB_ID, "vendor": "codex", "model": "m", "effort": "high", "harness": "h"}

    def value(self, **changes):
        value = {"schema_version": 1, "job_id": JOB_ID, "agent_id": "agent_1",
                 "runtime": {"vendor": "codex", "model": "m", "effort": "high", "harness": "h",
                             "backend": "collaboration.spawn_agent"},
                 "outcome": "success", "result": "r", "evidence": ["e"]}
        value.update(changes)
        return value

    def reject(self, value, state, message):
        with self.assertRaises(ValueError) as caught:
            native.validate_completion(value, state)
        self.assertEqual(str(caught.exception), message)

    def test_new_agent_completion_matrix(self):
        native.validate_completion(self.value(), self.STATE)
        runtime = dict(self.value()["runtime"], effort="low")
        cases = [
            (self.value(schema_version=2), "unsupported completion version"),
            (self.value(job_id="other"), "completion job ID mismatch"),
            (self.value(agent_id=".hidden"), "invalid agent_id"),
            (self.value(runtime=runtime), "runtime effort mismatch"),
            (self.value(runtime=dict(runtime, extra=1)), "missing or unknown JSON fields"),
            (self.value(agent_strategy="reuse"), "new-agent completion strategy mismatch"),
            (self.value(runtime=dict(self.value()["runtime"], backend="collaboration.followup_task")),
             "reuse backend on new-agent job"),
            (self.value(outcome="partial"), "invalid outcome"),
            (self.value(result=""), "invalid result"),
            (self.value(evidence=[]), "invalid evidence"),
        ]
        for value, message in cases:
            with self.subTest(message=message):
                self.reject(value, self.STATE, message)

    def test_inherited_job_skips_only_the_effort_check(self):
        state = dict(self.STATE, inherit=True)
        runtime = dict(self.value()["runtime"], effort="whatever-ran")
        native.validate_completion(self.value(runtime=runtime), state)
        self.reject(self.value(runtime=dict(runtime, model="other")), state, "runtime model mismatch")

    def test_reuse_completion_must_come_from_the_same_agent_via_followup(self):
        state = dict(self.STATE, agent_strategy="reuse", existing_agent_id="agent_1")
        runtime = dict(self.value()["runtime"], backend="collaboration.followup_task")
        native.validate_completion(self.value(runtime=runtime, agent_strategy="reuse"), state)
        self.reject(self.value(runtime=runtime), state, "reuse completion strategy required")
        self.reject(self.value(runtime=runtime, agent_strategy="reuse", agent_id="agent_2"), state,
                    "reuse agent ID mismatch")
        self.reject(self.value(agent_strategy="reuse"), state, "reuse backend mismatch")


class MainTests(NativeFixture):
    def run_main(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["native.py", *argv]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_vendor_is_required_without_inherit(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main("route", "--home", str(self.home), "--lane", "l", "--workdir",
                          str(self.workdir), "--task", "t", "--executor", "auto", "--mode",
                          "advise", "--policy", POLICY)
        self.assertEqual(caught.exception.code, 2)

    def test_errors_are_reported_without_echoing_private_input(self):
        code, out, err = self.run_main("job", "--home", str(self.home), "--json", "status", "bad")
        self.assertEqual((code, err), (2, ""))
        self.assertEqual(json.loads(out), {"schema_version": 1, "command": "status", "ok": False,
                                           "error": "invalid job ID"})
        (self.home / "jobs" / JOB_ID).mkdir(parents=True)
        code, out, err = self.run_main("job", "--home", str(self.home), "status", JOB_ID)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "omnilane: invalid or inaccessible native protocol input/state\n")
        secret = self.base / "secret.json"
        secret.write_text('{"capabilities": "SECRET-VALUE",')
        code, out, err = self.run_main(
            "route", "--home", str(self.home), "--lane", "l", "--workdir", str(self.workdir),
            "--task", "t", "--executor", "auto", "--mode", "advise", "--policy", POLICY,
            "--vendor", "codex", "--context", str(secret), "--operator-asserted-human")
        self.assertEqual(code, 2)
        self.assertNotIn("SECRET-VALUE", out + err)
        self.assertEqual(err, "omnilane: invalid or inaccessible native protocol input/state\n")


if __name__ == "__main__":
    unittest.main()
