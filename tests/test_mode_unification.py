#!/usr/bin/env python3
"""Exact offline contracts for omnilane's advise/work/sysops controls."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_codex_live() -> types.ModuleType:
    path = ROOT / "scripts/runners/run-codex-live.py"
    spec = importlib.util.spec_from_file_location("omnilane_codex_live", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_grok_live() -> types.ModuleType:
    path = ROOT / "scripts/runners/run-grok-live.py"
    spec = importlib.util.spec_from_file_location("omnilane_grok_live", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config_values(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "-c"]


class CodexModeContracts(unittest.TestCase):
    maxDiff = None

    EXPECTED = {
        "advise": {
            "sandbox": "read-only",
            "config": {
                'approval_policy="never"',
                'sandbox_mode="read-only"',
                'web_search="live"',
            },
        },
        "work": {
            "sandbox": "workspace-write",
            "config": {
                'approval_policy="never"',
                'sandbox_mode="workspace-write"',
                'web_search="disabled"',
                "sandbox_workspace_write.network_access=false",
                "sandbox_workspace_write.exclude_slash_tmp=true",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "sandbox_workspace_write.writable_roots=[]",
            },
        },
        "sysops": {
            "sandbox": "danger-full-access",
            "config": {
                'approval_policy="never"',
                'sandbox_mode="danger-full-access"',
                'web_search="live"',
            },
        },
    }

    def test_single_shot_argv_sets_all_codex_policy_dimensions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omnilane-mode-codex-") as td:
            tmp = Path(td)
            fake = tmp / "codex"
            fake.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
python3 - "$FAKE_CODEX_ARGV" "$@" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))
PY
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then shift; out="$1"; fi
  shift
done
printf 'codex mode fixture\n' > "$out"
printf '{"type":"thread.started","thread_id":"mode-fixture"}\n'
"""
            )
            fake.chmod(0o755)
            prompt = tmp / "prompt.txt"
            prompt.write_text("mode fixture\n")
            workdir = tmp / "workdir"
            workdir.mkdir()
            for mode, expected in self.EXPECTED.items():
                with self.subTest(mode=mode):
                    argv_file = tmp / f"{mode}.argv.json"
                    output = tmp / f"{mode}.out"
                    env = os.environ.copy()
                    env.update(
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
                        CODEX_BIN=str(fake),
                        FAKE_CODEX_ARGV=str(argv_file),
                        OMNILANE_REPO=str(ROOT),
                        OMNILANE_TIMEOUT="5",
                    )
                    env.pop("OMNILANE_INBOX", None)
                    result = subprocess.run(
                        [
                            "/bin/bash",
                            str(ROOT / "scripts/runners/run-codex.sh"),
                            mode,
                            str(workdir),
                            "gpt-6-astra",
                            "high",
                            str(prompt),
                            str(output),
                        ],
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    argv = json.loads(argv_file.read_text())
                    self.assertIn("-s", argv)
                    self.assertEqual(argv[argv.index("-s") + 1], expected["sandbox"])
                    self.assertTrue(expected["config"].issubset(set(config_values(argv))))

    def test_live_app_server_uses_same_codex_policy_dimensions(self) -> None:
        module = load_codex_live()
        for mode, expected in self.EXPECTED.items():
            with self.subTest(mode=mode):
                actual = module.codex_app_server_argv("codex", expected["sandbox"])
                self.assertEqual(actual[-1], "app-server")
                self.assertEqual(actual[0], "codex")
                self.assertTrue(
                    expected["config"].issubset(set(config_values(actual)))
                )


class GrokModeContracts(unittest.TestCase):
    def run_single_shot(self, mode: str, platform: str) -> tuple[subprocess.CompletedProcess[str], list[str] | None]:
        temp = tempfile.TemporaryDirectory(prefix="omnilane-mode-grok-")
        self.addCleanup(temp.cleanup)
        tmp = Path(temp.name)
        fake = tmp / "grok"
        argv_file = tmp / "argv.json"
        fake.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
python3 - "$FAKE_GROK_ARGV" "$@" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))
PY
printf 'grok mode fixture\n'
"""
        )
        fake.chmod(0o755)
        fake_uname = tmp / "uname"
        fake_uname.write_text(f"#!/bin/sh\nprintf '%s\\n' {platform}\n")
        fake_uname.chmod(0o755)
        workdir = tmp / "workdir"
        workdir.mkdir()
        prompt = tmp / "prompt.txt"
        prompt.write_text("mode fixture\n")
        output = tmp / "out.txt"
        env = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_AA_")}
        env.update(
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
            GROK_BIN=str(fake),
            FAKE_GROK_ARGV=str(argv_file),
            OMNILANE_REPO=str(ROOT),
            OMNILANE_TIMEOUT="5",
            OMNILANE_GROK_MAX_ATTEMPTS="1",
            PATH=f"{tmp}{os.pathsep}{env['PATH']}",
        )
        env.pop("OMNILANE_INBOX", None)
        result = subprocess.run(
            [
                "/bin/bash",
                str(ROOT / "scripts/runners/run-grok.sh"),
                mode,
                str(workdir),
                "grok-4.6",
                "-",
                str(prompt),
                str(output),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        argv = json.loads(argv_file.read_text()) if argv_file.exists() else None
        return result, argv

    def test_advise_is_read_only_and_keeps_search(self) -> None:
        result, argv = self.run_single_shot("advise", "Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        assert argv is not None
        self.assertIn("--permission-mode", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--sandbox", argv)
        tools = argv[argv.index("--tools") + 1].split(",")
        self.assertEqual(
            tools,
            ["Bash", "Read", "Glob", "Grep", "web_search", "web_fetch"],
        )
        allows = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--allow"]
        denies = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--deny"]
        self.assertEqual(allows, ["Read", "Glob", "Grep", "WebSearch", "WebFetch"])
        self.assertEqual(denies, ["Bash", "Edit", "MCPTool"])
        self.assertNotIn("--disable-web-search", argv)

    def test_work_fails_closed_on_macos(self) -> None:
        result, argv = self.run_single_shot("work", "Darwin")
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(argv)
        self.assertIn("network isolation", result.stderr)

    def test_work_uses_strict_profile_on_linux(self) -> None:
        result, argv = self.run_single_shot("work", "Linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        assert argv is not None
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(argv[argv.index("--sandbox") + 1], "strict")
        self.assertIn("--disable-web-search", argv)

    def test_sysops_is_explicit_full_access_with_search(self) -> None:
        result, argv = self.run_single_shot("sysops", "Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        assert argv is not None
        self.assertIn("--always-approve", argv)
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "off")
        self.assertNotIn("--disable-web-search", argv)

    def test_acp_is_sysops_only(self) -> None:
        module = load_grok_live()
        self.assertEqual(
            module.grok_acp_argv("grok", "grok-4.6", "sysops"),
            [
                "grok", "--no-memory", "--no-subagents", "--no-plan", "--verbatim",
                "--sandbox", "off", "agent", "--always-approve", "--model", "grok-4.6", "stdio",
            ],
        )
        for mode in ("advise", "work"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                module.grok_acp_argv("grok", "grok-4.6", mode)


class ClaudeModeContracts(unittest.TestCase):
    def run_claude(self, mode: str, live: bool = False) -> list[str]:
        temp = tempfile.TemporaryDirectory(prefix="omnilane-mode-claude-")
        self.addCleanup(temp.cleanup)
        tmp = Path(temp.name)
        fake = tmp / "claude"
        argv_file = tmp / "argv.json"
        fake.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
python3 - "$FAKE_CLAUDE_ARGV" "$@" <<'PY'
import json, os, pathlib, sys
args = sys.argv[2:]
args.append("__CLAUDE_CODE_TMPDIR__=" + os.environ.get("CLAUDE_CODE_TMPDIR", ""))
pathlib.Path(sys.argv[1]).write_text(json.dumps(args))
PY
if [[ " $* " == *" --input-format stream-json "* ]]; then
  printf '{"type":"result","subtype":"success","result":"claude mode fixture"}\n'
else
  printf 'claude mode fixture\n'
fi
"""
        )
        fake.chmod(0o755)
        workdir = tmp / "workdir"
        workdir.mkdir()
        prompt = tmp / "prompt.txt"
        prompt.write_text("mode fixture\n")
        output = tmp / "out.txt"
        env = os.environ.copy()
        env.update(
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
            CLAUDE_BIN=str(fake),
            FAKE_CLAUDE_ARGV=str(argv_file),
            OMNILANE_REPO=str(ROOT),
            OMNILANE_TIMEOUT="5",
        )
        fifo_fd = None
        if live:
            fifo = tmp / "inbox.fifo"
            os.mkfifo(fifo, 0o600)
            fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
            env["OMNILANE_INBOX"] = str(fifo)
        else:
            env.pop("OMNILANE_INBOX", None)
        try:
            result = subprocess.run(
                [
                    "/bin/bash",
                    str(ROOT / "scripts/runners/run-claude.sh"),
                    mode,
                    str(workdir),
                    "claude-fable-5-1",
                    "xhigh",
                    str(prompt),
                    str(output),
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        finally:
            if fifo_fd is not None:
                os.close(fifo_fd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(argv_file.read_text())

    def assert_restricted(self, argv: list[str], mode: str) -> None:
        self.assertIn("--safe-mode", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--setting-sources", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], mode)
        settings = json.loads(argv[argv.index("--settings") + 1])
        sandbox = settings["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["network"]["allowedDomains"], [])

    def test_advise_and_work_have_layered_single_shot_controls(self) -> None:
        advise = self.run_claude("advise")
        self.assert_restricted(advise, "plan")
        advise_tools = advise[advise.index("--tools") + 1].split(",")
        self.assertIn("WebSearch", advise_tools)
        self.assertIn("WebFetch", advise_tools)
        self.assertNotIn("Edit", advise_tools)
        self.assertNotIn("Write", advise_tools)

        work = self.run_claude("work")
        self.assert_restricted(work, "acceptEdits")
        work_tools = work[work.index("--tools") + 1].split(",")
        self.assertIn("Edit", work_tools)
        self.assertIn("Write", work_tools)
        self.assertNotIn("WebSearch", work_tools)
        self.assertNotIn("WebFetch", work_tools)
        tmp_entry = next(item for item in work if item.startswith("__CLAUDE_CODE_TMPDIR__="))
        tmp_path = Path(tmp_entry.split("=", 1)[1]).resolve()
        self.assertEqual(tmp_path.name, ".omnilane-claude-tmp")
        self.assertTrue(tmp_path.is_dir())
        self.assertEqual(tmp_path.stat().st_mode & 0o777, 0o700)

    def test_live_reuses_same_restricted_controls(self) -> None:
        self.assert_restricted(self.run_claude("advise", live=True), "plan")
        self.assert_restricted(self.run_claude("work", live=True), "acceptEdits")

    def test_sysops_disables_sandbox_and_prompts(self) -> None:
        for live in (False, True):
            with self.subTest(live=live):
                argv = self.run_claude("sysops", live=live)
                self.assertIn("--dangerously-skip-permissions", argv)
                self.assertEqual(
                    argv[argv.index("--permission-mode") + 1], "bypassPermissions"
                )
                settings = json.loads(argv[argv.index("--settings") + 1])
                self.assertFalse(settings["sandbox"]["enabled"])
                self.assertNotIn("--safe-mode", argv)




class CodexLiveDescendantCleanup(unittest.TestCase):
    def test_normal_eof_reaps_detached_app_server_descendant(self) -> None:
        module = load_codex_live()
        with tempfile.TemporaryDirectory(prefix="omnilane-codex-descendant-") as td:
            tmp = Path(td)
            fake = tmp / "codex"
            pid_file = tmp / "sidecar.pid"
            fake.write_text(
                """#!/usr/bin/env python3
import os, pathlib, subprocess, sys
args = sys.argv[1:]
while args[:1] == ["-c"] and len(args) >= 2:
    args = args[2:]
if args != ["app-server"]:
    raise SystemExit(2)
sidecar = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    start_new_session=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
pathlib.Path(os.environ["FAKE_CODEX_SIDECAR_PID"]).write_text(str(sidecar.pid))
sys.stdin.read()
raise SystemExit(0)
"""
            )
            fake.chmod(0o755)
            args = types.SimpleNamespace(
                codex_bin=str(fake),
                cwd=str(tmp),
                sandbox="workspace-write",
                app_server_eof_grace=0.5,
                app_server_term_grace=0.2,
                app_server_kill_grace=0.2,
            )
            old = os.environ.get("FAKE_CODEX_SIDECAR_PID")
            os.environ["FAKE_CODEX_SIDECAR_PID"] = str(pid_file)
            unrelated = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            client = module.CodexLiveClient(args)
            try:
                client.start_server()
                deadline = __import__("time").monotonic() + 2
                while __import__("time").monotonic() < deadline and not pid_file.exists():
                    __import__("time").sleep(0.02)
                self.assertTrue(pid_file.exists(), "sidecar did not start")
                sidecar_pid = int(pid_file.read_text())
                tracked = client.snapshot_descendants(client.process.pid)
                self.assertIn(sidecar_pid, tracked, f"sidecar missing from snapshot: {tracked}")
                client.close()
                deadline = __import__("time").monotonic() + 1
                while __import__("time").monotonic() < deadline:
                    if module.CodexLiveClient.process_identity(sidecar_pid) is None:
                        break
                    __import__("time").sleep(0.02)
                else:
                    self.fail("detached app-server descendant survived normal EOF close")
                self.assertIsNone(unrelated.poll(), "unrelated same-command process was terminated")
            finally:
                if unrelated.poll() is None:
                    unrelated.terminate()
                    unrelated.wait(timeout=2)
                if old is None:
                    os.environ.pop("FAKE_CODEX_SIDECAR_PID", None)
                else:
                    os.environ["FAKE_CODEX_SIDECAR_PID"] = old
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), 9)
                    except ProcessLookupError:
                        pass


class GeminiModeContracts(unittest.TestCase):
    def run_gemini(
        self,
        mode: str,
        live: bool = False,
        *,
        fixture_root: Path | None = None,
        output_name: str = "out.txt",
        thread_name: str | None = None,
        empty_output: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[str], dict[str, object], Path]:
        if fixture_root is None:
            temp = tempfile.TemporaryDirectory(prefix="omnilane-mode-gemini-")
            self.addCleanup(temp.cleanup)
            tmp = Path(temp.name)
        else:
            tmp = fixture_root
            tmp.mkdir(parents=True, exist_ok=True)
        fake = tmp / "agy"
        argv_file = tmp / f"argv-{output_name}.json"
        fake.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
python3 - "$FAKE_AGY_ARGV" "$@" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))
PY
if [[ "${FAKE_AGY_EMPTY:-0}" == "1" ]]; then
  exit 0
elif [[ " $* " == *" --input-format stream-json "* ]]; then
  printf '{"event":"result","result":{"status":"SUCCESS","response":"gemini mode fixture"}}\\n'
elif [[ " $* " == *" --output-format json "* ]]; then
  printf '{"status":"SUCCESS","response":"gemini mode fixture"}\\n'
else
  printf 'gemini mode fixture\\n'
fi
"""
        )
        fake.chmod(0o755)
        workdir = tmp / "workdir"
        workdir.mkdir(exist_ok=True)
        gemini_dir = tmp / "user-home" / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        prompt = tmp / "prompt.txt"
        prompt.write_text("mode fixture\n")
        output = tmp / output_name
        env = os.environ.copy()
        env.update(
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
            AGY_BIN=str(fake),
            HOME=str(tmp / "user-home"),
            FAKE_AGY_ARGV=str(argv_file),
            OMNILANE_REPO=str(ROOT),
            OMNILANE_HOME=str(tmp / "home"),
            OMNILANE_TIMEOUT="5",
            FAKE_AGY_EMPTY="1" if empty_output else "0",
        )
        if thread_name is not None:
            env["OMNILANE_THREAD_NAME"] = thread_name
        fifo_fd = None
        if live:
            fifo = tmp / f"{output_name}.fifo"
            os.mkfifo(fifo, 0o600)
            fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
            env["OMNILANE_INBOX"] = str(fifo)
        else:
            env.pop("OMNILANE_INBOX", None)
        try:
            result = subprocess.run(
                [
                    "/bin/bash",
                    str(ROOT / "scripts/runners/run-gemini.sh"),
                    mode,
                    str(workdir),
                    "gemini-3.8-flash-high",
                    "-",
                    str(prompt),
                    str(output),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
        finally:
            if fifo_fd is not None:
                os.close(fifo_fd)
        if not argv_file.exists():
            return result, [], {}, Path()
        argv = json.loads(argv_file.read_text())
        app_arg = next(token.split("=", 1)[1] for token in argv if token.startswith("--app_data_dir="))
        app_root = (gemini_dir / app_arg).resolve()
        settings = json.loads((app_root / "settings.json").read_text())
        return result, argv, settings, app_root

    def test_advise_uses_private_enforced_policy(self) -> None:
        result, argv, settings, app_root = self.run_gemini("advise")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--sandbox", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(settings["toolPermission"], "proceed-in-sandbox")
        self.assertIs(settings["enableTerminalSandbox"], True)
        self.assertIs(settings["allowNonWorkspaceAccess"], False)
        self.assertEqual(
            settings["permissions"]["deny"],
            ["write_file(*)", "command(*)", "execute_url(*)", "mcp(*)", "unsandboxed(*)"],
        )
        allow = settings["permissions"]["allow"]
        self.assertTrue(any(item.startswith("read_file(") for item in allow))
        self.assertIn("read_url(*)", allow)
        self.assertFalse(any(item.startswith("write_file(") for item in allow))
        self.assertEqual(app_root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((app_root / "settings.json").stat().st_mode & 0o777, 0o600)

    def test_work_uses_verified_native_policy_and_cleans_owned_profile(self) -> None:
        result, argv, settings, app_root = self.run_gemini("work")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--sandbox", argv)
        self.assertIn("--agent", argv)
        self.assertTrue(argv[argv.index("--agent") + 1].startswith("omnilane-work-"))
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(settings["toolPermission"], "proceed-in-sandbox")
        self.assertIs(settings["enableTerminalSandbox"], True)
        self.assertIs(settings["allowNonWorkspaceAccess"], False)
        self.assertIn("command(*)", settings["permissions"]["allow"])
        self.assertNotIn("unsandboxed(*)", settings["permissions"]["allow"])
        self.assertIn("read_url(*)", settings["permissions"]["deny"])
        self.assertFalse((app_root / "workspace-agent.json").exists())
        self.assertIn("commandExecutionPolicy: sandbox", (app_root / "policy/agent.md").read_text())

    def test_sysops_explicitly_selects_unrestricted_private_policy(self) -> None:
        for live in (False, True):
            with self.subTest(live=live):
                result, argv, settings, _ = self.run_gemini("sysops", live=live)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--dangerously-skip-permissions", argv)
                self.assertEqual(argv[argv.index("--mode") + 1], "accept-edits")
                self.assertNotIn("--sandbox", argv)
                self.assertEqual(settings["toolPermission"], "always-proceed")
                self.assertIs(settings["enableTerminalSandbox"], False)
                self.assertIs(settings["allowNonWorkspaceAccess"], True)
                self.assertEqual(settings["permissions"]["deny"], [])
                if live:
                    self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")

    def test_work_rejects_external_permission_broadening_before_provider(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omnilane-agy-permissions-") as raw:
            tmp = Path(raw)
            gemini_dir = tmp / ".gemini"
            shared_dir = gemini_dir / "config"
            shared_dir.mkdir(parents=True)
            workdir = tmp / "work"
            workdir.mkdir()
            app_root = tmp / "app"
            helper = ROOT / "scripts/lib/prepare-agy-mode.py"
            (shared_dir / "config.json").write_text(json.dumps({
                "permissions": {"allow": ["write_file(*)"], "deny": [], "ask": []}
            }))
            result = subprocess.run(
                ["python3", str(helper), "--mode", "work", "--workdir", str(workdir),
                 "--app-root", str(app_root), "--gemini-dir", str(gemini_dir)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Shared permission grant conflicts with work mode", result.stderr)
            self.assertNotIn("write_file(*)", result.stderr)
            self.assertFalse((app_root / "settings.json").exists())

    def test_work_accepts_shared_write_grant_confined_to_workdir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omnilane-agy-permissions-") as raw:
            tmp = Path(raw)
            gemini_dir = tmp / ".gemini"
            shared_dir = gemini_dir / "config"
            shared_dir.mkdir(parents=True)
            workdir = tmp / "work"
            workdir.mkdir()
            app_root = tmp / "app"
            (shared_dir / "config.json").write_text(json.dumps({
                "permissions": {"allow": [f"write_file({workdir})"], "deny": [], "ask": []}
            }))
            result = subprocess.run(
                ["python3", str(ROOT / "scripts/lib/prepare-agy-mode.py"), "--mode", "work",
                 "--workdir", str(workdir), "--app-root", str(app_root),
                 "--gemini-dir", str(gemini_dir)], text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_success_is_reported_as_failure(self) -> None:
        result, _, _, _ = self.run_gemini("advise", empty_output=True)
        self.assertEqual(result.returncode, 1)

    def test_named_thread_reuses_one_private_app_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omnilane-mode-gemini-thread-") as raw:
            tmp = Path(raw)
            first = self.run_gemini("advise", fixture_root=tmp, output_name="first.txt", thread_name="stable-thread")
            second = self.run_gemini("advise", fixture_root=tmp, output_name="second.txt", thread_name="stable-thread")
            self.assertEqual(first[0].returncode, 0, first[0].stderr)
            self.assertEqual(second[0].returncode, 0, second[0].stderr)
            self.assertEqual(first[3], second[3])

class AgyOwnedAgentContracts(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "omnilane_agy_policy", ROOT / "scripts/lib/prepare-agy-mode.py"
        )
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-agy-agent-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_agent_is_private_stable_and_native_tool_allowlist(self) -> None:
        agent = self.module.ensure_work_agent(self.root)
        initial = agent.read_bytes()
        self.assertTrue(agent.is_absolute())
        self.assertEqual(agent, self.root / "policy/agent.md")
        self.assertEqual(agent.stat().st_mode & 0o777, 0o600)
        self.assertEqual(agent.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(b"mainAgent: true", initial)
        self.assertIn(b"inheritMcp: false", initial)
        self.assertIn(b"  - run_command\n", initial)
        for tool in ("search_web", "read_url_content", "subagent", "mcp"):
            self.assertNotIn(f"  - {tool}\n".encode(), initial)
        self.assertEqual(self.module.ensure_work_agent(self.root), agent)
        self.assertEqual(agent.read_bytes(), initial)

    def test_agent_conflict_preserves_existing_file(self) -> None:
        agent = self.module.ensure_work_agent(self.root)
        agent.write_text("caller-selected-agent\n")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.module.ensure_work_agent(self.root)
        self.assertEqual(agent.read_text(), "caller-selected-agent\n")

    def test_symlink_policy_or_agent_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "policy").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.module.ensure_work_agent(self.root)
        self.assertFalse((outside / "agent.md").exists())
        (self.root / "policy").unlink()
        (self.root / "policy").mkdir()
        (outside / "agent.md").write_text("untouched\n")
        (self.root / "policy/agent.md").symlink_to(outside / "agent.md")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.module.ensure_work_agent(self.root)
        self.assertEqual((outside / "agent.md").read_text(), "untouched\n")


class DispatchModeContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-mode-dispatch-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.workdir = self.tmp / "workdir"
        self.workdir.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in ("grok", "agy", "claude", "codex"):
            path = self.bin / name
            path.write_text("#!/bin/sh\nexit 91\n")
            path.chmod(0o755)
        uname = self.bin / "uname"
        uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n")
        uname.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
            OMNILANE_HOME=str(self.home),
            PATH=f"{self.bin}{os.pathsep}{self.env['PATH']}",
            GROK_BIN=str(self.bin / "grok"),
            AGY_BIN=str(self.bin / "agy"),
            GEMINI_BIN=str(self.bin / "agy"),
            CLAUDE_BIN=str(self.bin / "claude"),
            CODEX_BIN=str(self.bin / "codex"),
        )

    def dispatch(self, vendor: str, mode: str, *extra: str) -> subprocess.CompletedProcess[str]:
        lane = "live-search" if vendor == "grok" else "long-context"
        return subprocess.run(
            [
                "/bin/bash",
                str(ROOT / "scripts/dispatch.sh"),
                "--dry-run",
                "--background",
                "--mode",
                mode,
                "--workdir",
                str(self.workdir),
                "--vendor",
                vendor,
                *extra,
                lane,
                "mode fixture",
            ],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_grok_restricted_live_and_macos_work_fail_before_job_creation(self) -> None:
        advise_live = self.dispatch("grok", "advise", "--live")
        self.assertEqual(advise_live.returncode, 2)
        self.assertIn("only explicit --mode sysops", advise_live.stderr)
        mac_work = self.dispatch("grok", "work")
        self.assertEqual(mac_work.returncode, 2)
        self.assertIn("not enforced on macOS", mac_work.stderr)
        self.assertFalse((self.home / "jobs").exists())

    def test_grok_explicit_sysops_live_remains_available(self) -> None:
        result = self.dispatch("grok", "sysops", "--live")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.assertEqual(plan.get("session_mode"), "live")
        self.assertEqual(plan.get("mode"), "sysops")

    def test_gemini_advise_resolves_without_mode_downgrade(self) -> None:
        advise = self.dispatch("gemini", "advise", "--live")
        self.assertEqual(advise.returncode, 0, advise.stderr)
        plan = dict(line.split("=", 1) for line in advise.stdout.splitlines() if "=" in line)
        self.assertEqual(plan.get("mode"), "advise")
        self.assertEqual(plan.get("session_mode"), "live")

        self.assertFalse((self.home / "jobs").exists())

    def test_help_describes_sysops_for_all_supported_vendors(self) -> None:
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/dispatch.sh"), "--help"],
            cwd=ROOT, env=self.env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sysops (vendor sandbox disabled", result.stdout)
        self.assertIn("codex, claude, grok, and gemini", result.stdout)
        self.assertNotIn("codex only", result.stdout)
        self.assertNotIn("other vendors treat it as work", result.stdout)

    def test_gemini_work_routes_ordinary_and_live_without_downgrade(self) -> None:
        for session_flag, session_mode in (("--single-shot", "single-shot"), ("--live", "live")):
            with self.subTest(session_mode=session_mode):
                result = self.dispatch("gemini", "work", session_flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                self.assertEqual(plan.get("vendor"), "gemini")
                self.assertEqual(plan.get("mode"), "work")
                self.assertEqual(plan.get("session_mode"), session_mode)
        self.assertFalse((self.home / "jobs").exists())



if __name__ == "__main__":
    unittest.main()
