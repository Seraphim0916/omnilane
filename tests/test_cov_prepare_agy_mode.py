import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/lib/prepare-agy-mode.py"
SPEC = importlib.util.spec_from_file_location("prepare_agy_mode", SCRIPT)
agy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agy)


class AgyFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agy-mode-")
        self.addCleanup(temp.cleanup)
        # macOS /var is a symlink; the script compares resolved paths.
        self.base = Path(temp.name).resolve()
        self.workdir = self.base / "work"
        self.workdir.mkdir()
        self.gemini = self.base / "gemini"
        self.gemini.mkdir()
        self.app = self.gemini / "app"

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def run_main(self, mode, *extra):
        argv = [str(SCRIPT), "--mode", mode, "--workdir", str(self.workdir),
                "--app-root", str(self.app), "--gemini-dir", str(self.gemini), *extra]
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
            code = agy.main()
        return code, stdout.getvalue()


class PolicyTests(unittest.TestCase):
    def test_advise_reads_everything_except_secret_roots(self):
        value = agy.policy("advise", "/w")
        self.assertEqual(value["toolPermission"], "proceed-in-sandbox")
        self.assertTrue(value["enableTerminalSandbox"])
        self.assertTrue(value["allowNonWorkspaceAccess"])
        self.assertEqual(value["permissions"]["allow"], ["read_file(*)", "read_url(*)"])
        deny = value["permissions"]["deny"]
        self.assertIn("write_file(*)", deny)
        self.assertIn("command(*)", deny)
        self.assertIn(f"read_file({Path.home() / '.ssh'})", deny)
        self.assertEqual(len(agy.advise_secret_roots()), 14)
        self.assertEqual(value["permissions"]["ask"], [])

    def test_work_confines_writes_and_denies_cache_roots_once(self):
        value = agy.policy("work", "/w", "/w")
        self.assertFalse(value["allowNonWorkspaceAccess"])
        self.assertEqual(value["permissions"]["allow"], ["read_file(/w)", "write_file(/w)", "command(*)"])
        deny = value["permissions"]["deny"]
        self.assertEqual(deny[:3], ["read_url(*)", "execute_url(*)", "mcp(*)"])
        self.assertIn("read_file(/tmp)", deny)
        self.assertIn("write_file(/w/.agents)", deny)
        self.assertIn("write_file(/w)", deny)
        self.assertNotIn("unsandboxed(*)", deny)
        self.assertEqual(len(deny), len(set(deny)))

    def test_work_without_app_root_omits_it(self):
        deny = agy.policy("work", "/w")["permissions"]["deny"]
        self.assertNotIn("write_file(/w)", deny)

    def test_sysops_is_unrestricted(self):
        value = agy.policy("sysops", "/w")
        self.assertEqual(value["toolPermission"], "always-proceed")
        self.assertFalse(value["enableTerminalSandbox"])
        self.assertIn("unsandboxed(*)", value["permissions"]["allow"])
        self.assertEqual(value["permissions"]["deny"], [])

    def test_unknown_mode_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unsupported agy mode: vote"):
            agy.policy("vote", "/w")


class RuleTests(unittest.TestCase):
    def test_rules_inside_workdir_or_read_only_are_accepted(self):
        workdir = Path("/w")
        for rule in ("read_file(*)", "command(make)", "write_file(/w)", "write_file(/w/sub)",
                     "write_file(sub/file)"):
            with self.subTest(rule=rule):
                self.assertIsNone(agy.rule_broadens_work(rule, workdir))

    def test_rules_that_broaden_work_are_named(self):
        workdir = Path("/w")
        cases = {
            "not a rule": "unknown allow-rule schema",
            "read_url(*)": "read_url bypasses work network or sandbox policy",
            "unsandboxed(x)": "unsandboxed bypasses",
            "write_file(*)": "write_file grant extends beyond workdir",
            "write_file()": "write_file grant extends beyond workdir",
            "write_file(/other)": "write_file grant extends beyond workdir",
            "write_file(../escape)": "write_file grant extends beyond workdir",
            "delete_file(/w)": "unknown allow-rule action may bypass work policy",
        }
        for rule, reason in cases.items():
            with self.subTest(rule=rule):
                self.assertIn(reason, agy.rule_broadens_work(rule, workdir))


class LoadJsonTests(AgyFixture):
    def test_missing_file_is_empty(self):
        self.assertEqual(agy.load_json(self.base / "absent.json", "X"), {})

    def test_bad_sources_are_refused(self):
        directory = self.base / "dir.json"
        directory.mkdir()
        cases = (
            (directory, "X permissions source is not a regular file"),
            (self.write_json(self.base / "bad.json", "{"), "unreadable or invalid JSON"),
            (self.write_json(self.base / "bin.json", ""), "unreadable or invalid JSON"),
            (self.write_json(self.base / "list.json", []), "unknown schema"),
        )
        (self.base / "bin.json").write_bytes(b"\xff\xfe")
        for path, message in cases:
            with self.subTest(path=path.name):
                with self.assertRaisesRegex(ValueError, message):
                    agy.load_json(path, "X")

    def test_permission_allow_list_shapes(self):
        self.assertEqual(agy.permission_allow_list(None, "X"), [])
        self.assertEqual(agy.permission_allow_list({}, "X"), [])
        self.assertEqual(agy.permission_allow_list({"allow": ["a"], "deny": [], "ask": []}, "X"), ["a"])
        for value in ([], {"allow": "a"}, {"deny": [1]}, {"ask": None}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "X permissions have an unknown schema"):
                    agy.permission_allow_list(value, "X")


class AuditTests(AgyFixture):
    def audit(self):
        self.app.mkdir(exist_ok=True)
        agy.audit_work_permissions(self.gemini, self.app, self.workdir)

    def test_no_sources_pass(self):
        self.audit()

    def test_shared_non_workspace_access_is_refused(self):
        self.write_json(self.gemini / "config/config.json", {"allowNonWorkspaceAccess": True})
        with self.assertRaisesRegex(ValueError, "Shared permissions allow non-workspace access"):
            self.audit()

    def test_each_source_grant_is_checked(self):
        self.write_json(self.gemini / "config/config.json",
                        {"permissions": {"allow": [f"write_file({self.workdir})"]}})
        self.audit()
        self.write_json(self.app / "settings.json", {"permissions": {"allow": ["mcp(*)"]}})
        with self.assertRaisesRegex(ValueError, "private app state permission grant conflicts with work mode: mcp"):
            self.audit()

    def test_project_grants_are_audited(self):
        self.write_json(self.app / "cache/default_project_id.txt", "proj-1\n")
        self.audit()  # A selected project with no file has no grants.
        project = self.gemini / "config/projects/proj-1.json"
        self.write_json(project, {"permissionGrants": {"permissionGrants": {"allow": ["read_file(*)"]}}})
        self.audit()
        self.write_json(project, {"permissionGrants": {"permissionGrants": {"allow": ["write_file(/)"]}}})
        with self.assertRaisesRegex(ValueError, "Project permission grant conflicts with work mode"):
            self.audit()
        self.write_json(project, {"permissionGrants": ["write_file(/)"]})
        with self.assertRaisesRegex(ValueError, "Project permissions have an unknown schema"):
            self.audit()

    def test_project_selector_must_be_a_plain_identifier(self):
        selector = self.app / "cache/default_project_id.txt"
        self.write_json(selector, "../escape")
        with self.assertRaisesRegex(ValueError, "Project permission selector has an unknown schema"):
            self.audit()
        selector.unlink()
        selector.mkdir()
        with self.assertRaisesRegex(ValueError, "Project permission selector has an unknown schema"):
            self.audit()


class PrivateRootTests(AgyFixture):
    def test_creates_private_root_and_accepts_same_request_again(self):
        agy.ensure_private_root(self.app, "work", self.workdir)
        self.assertEqual(self.app.stat().st_mode & 0o777, 0o700)
        marker = self.app / agy.POLICY_MARKER
        self.assertEqual(json.loads(marker.read_text()),
                         {"schema_version": 1, "mode": "work", "workdir": str(self.workdir)})
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        agy.ensure_private_root(self.app, "work", self.workdir)

    def test_mismatched_or_foreign_roots_are_refused(self):
        agy.ensure_private_root(self.app, "work", self.workdir)
        with self.assertRaisesRegex(ValueError, "does not match the requested mode and workdir"):
            agy.ensure_private_root(self.app, "advise", self.workdir)
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "user-file").write_text("x")
        with self.assertRaisesRegex(ValueError, "not an Omnilane-owned empty root"):
            agy.ensure_private_root(foreign, "work", self.workdir)

    def test_symlink_and_file_roots_are_refused(self):
        link = self.base / "link"
        link.symlink_to(self.workdir)
        with self.assertRaisesRegex(ValueError, "unsafe symlinked agy app root"):
            agy.ensure_private_root(link, "work", self.workdir)
        plain = self.base / "plain"
        plain.write_text("x")
        with self.assertRaisesRegex(ValueError, "unsafe non-directory agy app root"):
            agy.ensure_private_root(plain, "work", self.workdir)

    def test_atomic_write_replaces_and_refuses_unsafe_targets(self):
        target = self.base / "settings.json"
        agy.atomic_write_json(target, {"a": 1})
        agy.atomic_write_json(target, {"b": 2})
        self.assertEqual(target.read_text(), '{"b":2}\n')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["gemini", "settings.json", "work"])
        link = self.base / "link.json"
        link.symlink_to(target)
        directory = self.base / "dir.json"
        directory.mkdir()
        for path in (link, directory):
            with self.subTest(path=path.name):
                with self.assertRaisesRegex(ValueError, "unsafe agy settings path"):
                    agy.atomic_write_json(path, {})


class WorkAgentTests(AgyFixture):
    def setUp(self):
        super().setUp()
        self.app.mkdir(mode=0o700)

    def test_agent_policy_is_idempotent_and_tamper_evident(self):
        first = agy.ensure_work_agent(self.app, "omnilane-work")
        self.assertEqual(first.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn("name: omnilane-work\n", first.read_text())
        for tool in agy.WORK_TOOLS:
            self.assertIn(f"  - {tool}\n", first.read_text())
        self.assertEqual(agy.ensure_work_agent(self.app, "omnilane-work"), first)
        self.assertEqual(sorted(p.name for p in first.parent.iterdir()), ["agent.md"])
        first.write_text(first.read_text() + "extra\n")
        with self.assertRaisesRegex(ValueError, "existing agy primary agent conflicts with work mode"):
            agy.ensure_work_agent(self.app, "omnilane-work")

    def test_agent_policy_rejects_unsafe_locations(self):
        policy = self.app / "policy"
        policy.write_text("x")
        with self.assertRaisesRegex(ValueError, "unsafe agy agent policy directory"):
            agy.ensure_work_agent(self.app)
        policy.unlink()
        policy.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, "unsafe agy agent policy directory"):
            agy.ensure_work_agent(self.app)
        policy.unlink()
        (policy / "agent.md").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "unsafe agy primary agent path"):
            agy.ensure_work_agent(self.app)

    def test_concurrent_identical_agent_is_adopted_and_conflicting_one_refused(self):
        expected = agy.ensure_work_agent(self.app, "n").read_text()
        (self.app / "policy/agent.md").unlink()

        def other_wins(source, target):
            Path(target).write_text(expected)
            raise FileExistsError(target)

        with mock.patch.object(agy.os, "link", side_effect=other_wins):
            path = agy.ensure_work_agent(self.app, "n")
        self.assertEqual(path.read_text(), expected)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["agent.md"])
        (self.app / "policy/agent.md").unlink()

        def conflicting(source, target):
            Path(target).write_text("someone else\n")
            raise FileExistsError(target)

        with mock.patch.object(agy.os, "link", side_effect=conflicting):
            with self.assertRaisesRegex(ValueError, "conflicts with work mode"):
                agy.ensure_work_agent(self.app, "n")
        self.assertEqual(sorted(p.name for p in (self.app / "policy").iterdir()), ["agent.md"])

    def test_checked_directory_refuses_symlinks_and_files(self):
        link = self.base / "link"
        link.symlink_to(self.workdir)
        plain = self.base / "plain"
        plain.write_text("x")
        for path in (link, plain):
            with self.subTest(path=path.name):
                with self.assertRaisesRegex(ValueError, "unsafe agy workspace directory"):
                    agy.checked_directory(path)


class StageCleanupTests(AgyFixture):
    def setUp(self):
        super().setUp()
        self.app.mkdir(mode=0o700)

    def stage(self):
        agy.stage_work_agent(self.app, self.workdir)
        return json.loads((self.app / "workspace-agent.json").read_text())

    def test_stage_cleanup_and_restage_reuses_the_owned_leaf(self):
        state = self.stage()
        leaf = Path(state["leaf"])
        self.assertRegex(state["agent"], r"^omnilane-work-[a-f0-9]{16}$")
        self.assertEqual(leaf, self.workdir / ".agents/agents" / state["agent"])
        self.assertEqual(sorted(os.listdir(leaf)), [".omnilane-owned.json", "agent.md"])
        self.assertEqual(os.readlink(leaf / "agent.md"), state["policy"])
        for part in ("tmp", "cache", "clang", "swift"):
            self.assertTrue((Path(state["cache"]) / part).is_dir())

        agy.cleanup_work_agent(self.app, self.workdir)
        self.assertFalse((self.app / "workspace-agent.json").exists())
        self.assertEqual(os.listdir(leaf), [])
        cleaned = json.loads((self.app / "workspace-agent-cleaned.json").read_text())
        self.assertEqual(cleaned, state)

        again = self.stage()
        self.assertEqual((again["device"], again["inode"]), (state["device"], state["inode"]))
        agy.cleanup_work_agent(self.app, self.workdir)

    def test_cleanup_without_state_is_a_no_op(self):
        agy.cleanup_work_agent(self.app, self.workdir)

    def test_active_or_unowned_leaf_is_not_adopted(self):
        state = self.stage()
        with self.assertRaisesRegex(ValueError, "active or stale; inspect it before reuse"):
            agy.stage_work_agent(self.app, self.workdir)
        leaf = Path(state["leaf"])
        agy.cleanup_work_agent(self.app, self.workdir)
        (leaf / "user-file").write_text("x")
        with self.assertRaisesRegex(ValueError, "active or stale"):
            agy.stage_work_agent(self.app, self.workdir)
        (leaf / "user-file").unlink()
        leaf.rmdir()
        leaf.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, "unsafe existing agy workspace policy leaf"):
            agy.stage_work_agent(self.app, self.workdir)

    def test_leaf_replaced_after_creation_is_detected(self):
        real_open = os.open

        def swap_then_open(path, flags, *args, **kwargs):
            if isinstance(path, Path) and path.parent.name == "agents":
                # Keep the original alive so the replacement cannot reuse its inode.
                path.rename(self.base / "original-leaf")
                path.mkdir()
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(agy.os, "open", side_effect=swap_then_open):
            with self.assertRaisesRegex(ValueError, "leaf changed before staging"):
                agy.stage_work_agent(self.app, self.workdir)

    def test_cleanup_refuses_every_kind_of_tampering(self):
        def fresh():
            for path in (self.app / "workspace-agent.json", self.app / "workspace-agent-cleaned.json"):
                if path.exists():
                    path.unlink()
            agents = self.workdir / ".agents/agents"
            if agents.exists():
                for leaf in agents.iterdir():
                    for child in leaf.iterdir():
                        child.unlink()
                    leaf.rmdir()
            return self.stage()

        def rewrite(**changes):
            state = fresh()
            state.update(changes)
            (self.app / "workspace-agent.json").write_text(json.dumps(state))
            return state

        rewrite(agent="someone-else")
        with self.assertRaisesRegex(ValueError, "invalid agy workspace policy identity"):
            agy.cleanup_work_agent(self.app, self.workdir)

        rewrite(workdir="/elsewhere")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            agy.cleanup_work_agent(self.app, self.workdir)

        rewrite(inode=0)
        with self.assertRaisesRegex(ValueError, "inode changed"):
            agy.cleanup_work_agent(self.app, self.workdir)

        state = fresh()
        (Path(state["leaf"]) / "extra").write_text("x")
        with self.assertRaisesRegex(ValueError, "leaf contents changed"):
            agy.cleanup_work_agent(self.app, self.workdir)

        state = fresh()
        policy = self.app / "policy/agent.md"
        original = policy.read_text()
        policy.write_text("tampered\n")
        with self.assertRaisesRegex(ValueError, "policy content changed"):
            agy.cleanup_work_agent(self.app, self.workdir)
        policy.write_text(original)

        state = fresh()
        leaf = Path(state["leaf"])
        for child in leaf.iterdir():
            child.unlink()
        leaf.rmdir()
        leaf.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, "directory changed"):
            agy.cleanup_work_agent(self.app, self.workdir)
        leaf.unlink()

        (self.app / "workspace-agent.json").unlink()
        (self.app / "workspace-agent.json").symlink_to(self.base / "nowhere.json")
        with self.assertRaisesRegex(ValueError, "unsafe agy workspace policy state"):
            agy.cleanup_work_agent(self.app, self.workdir)


class MainTests(AgyFixture):
    def test_advise_writes_settings_and_prints_relative_root(self):
        code, stdout = self.run_main("advise")
        self.assertEqual((code, stdout), (0, "app\n"))
        settings = json.loads((self.app / "settings.json").read_text())
        self.assertEqual(settings, agy.policy("advise", str(self.workdir), str(self.app)))
        self.assertFalse((self.workdir / ".agents").exists())

    def test_work_audits_stages_and_cleans_up(self):
        code, stdout = self.run_main("work")
        self.assertEqual((code, stdout), (0, "app\n"))
        self.assertTrue((self.app / "workspace-agent.json").is_file())
        code, stdout = self.run_main("work", "--cleanup")
        self.assertEqual((code, stdout), (0, ""))
        self.assertFalse((self.app / "workspace-agent.json").exists())

    def test_work_refuses_broadening_grants_before_writing_settings(self):
        self.write_json(self.gemini / "config/config.json", {"permissions": {"allow": ["write_file(*)"]}})
        with self.assertRaisesRegex(ValueError, "Shared permission grant conflicts"):
            self.run_main("work")
        self.assertFalse((self.app / "settings.json").exists())

    def test_script_entry_reports_errors_with_exit_two(self):
        link = self.base / "link"
        link.symlink_to(self.workdir)
        argv = [str(SCRIPT), "--mode", "advise", "--workdir", str(self.workdir),
                "--app-root", str(link), "--gemini-dir", str(self.gemini)]
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("omnilane: unsafe symlinked agy app root", stderr.getvalue())

    def test_script_entry_success_exits_zero(self):
        argv = [str(SCRIPT), "--mode", "sysops", "--workdir", str(self.workdir),
                "--app-root", str(self.app), "--gemini-dir", str(self.gemini)]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
