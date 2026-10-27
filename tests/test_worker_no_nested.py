#!/usr/bin/env python3
"""Offline contracts: workers do not delegate, and Codex gets a writable temp dir."""

from __future__ import annotations

from offline_env import fixture_environment_with_isolated_tools

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "[omnilane worker rules]"

FAKE_CODEX = """#!/usr/bin/env python3
import json, os, pathlib, stat, subprocess, sys
argv = sys.argv[1:]
# Model inherit=core, then apply the actual runner's forced shell settings.
shell_env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
for i, arg in enumerate(argv[:-1]):
    prefix = "shell_environment_policy.set."
    if arg == "-c" and argv[i + 1].startswith(prefix):
        key, value = argv[i + 1][len(prefix):].split("=", 1)
        shell_env[key] = json.loads(value)
nested = subprocess.run(
    ["/bin/bash", os.environ["OMNILANE_REPO"] + "/scripts/dispatch.sh", "fast", "nested test"],
    env=shell_env, text=True, capture_output=True) if shell_env.get("OMNILANE_DEPTH") else None
tmp = pathlib.Path(os.environ["TMPDIR"]) if os.environ.get("TMPDIR") else None
pathlib.Path(os.environ["FAKE_CODEX_RECORD"]).write_text(json.dumps({
    "argv": argv,
    "TMPDIR": os.environ.get("TMPDIR"),
    "TMPPREFIX": os.environ.get("TMPPREFIX"),
    "tmp_mode": stat.S_IMODE(tmp.stat().st_mode) if tmp else None,
    "nested_rc": nested.returncode if nested else None,
    "nested_stderr": nested.stderr if nested else "",
    "stdin": sys.stdin.read() if "app-server" not in argv else "",
}))
if "app-server" in argv:
    sys.exit(3)
if os.environ.get("FAKE_CODEX_BREAK_CLEANUP"):
    import shutil
    shutil.rmtree(tmp)
    tmp.symlink_to(os.environ["HOME"])
if os.environ.get("FAKE_CODEX_INTERRUPT"):
    import signal
    os.kill(int(os.environ["FAKE_CODEX_INTERRUPT"]), signal.SIGTERM)
pathlib.Path(argv[argv.index("-o") + 1]).write_text("fixture\\n")
print('{"type":"thread.started","thread_id":"fixture"}')
sys.exit(int(os.environ.get("FAKE_CODEX_RC", "0")))
"""


def config_values(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "-c"]


class CodexWorkerContracts(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory(prefix="omnilane-no-nested-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.fake = self.tmp / "codex"
        self.fake.write_text(FAKE_CODEX)
        self.fake.chmod(0o755)
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workdir)], check=True)
        self.workdir = self.workdir.resolve()

    def run_codex(self, mode: str, prompt: Path, **extra: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        record = self.tmp / f"{mode}.record.json"
        if record.exists():
            record.unlink()
        env = fixture_environment_with_isolated_tools(self)
        env.update(
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
            CODEX_BIN=str(self.fake),
            FAKE_CODEX_RECORD=str(record),
            OMNILANE_REPO=str(ROOT),
            OMNILANE_TIMEOUT="5",
            TMPDIR=str(self.tmp) + "/",
        )
        for key in ("OMNILANE_INBOX", "OMNILANE_CODEX_PROVIDER_BASE_URL", "OMNILANE_CODEX_HTTP_HEADERS",
                    "OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID"):
            env.pop(key, None)
        env.update(extra)
        if env.get("TMPDIR") == "":
            env.pop("TMPDIR")
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/runners/run-codex.sh"), mode, str(self.workdir),
             "gpt-6-astra", "medium", str(prompt), str(self.tmp / f"{mode}.out")],
            env=env, text=True, capture_output=True, check=False, timeout=60,
        )
        data = json.loads(record.read_text()) if record.exists() else {}
        return result, data

    def prompt(self, name: str = "prompt.txt") -> Path:
        path = self.tmp / name
        path.write_text("do the thing\n")
        return path

    def test_disables_multi_agent_in_every_mode(self) -> None:
        for mode in ("advise", "work", "sysops"):
            with self.subTest(mode=mode):
                result, data = self.run_codex(mode, self.prompt(f"{mode}.txt"))
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = data["argv"]
                self.assertIn("--disable", argv)
                self.assertEqual(argv[argv.index("--disable") + 1], "multi_agent")
                self.assertEqual(data["nested_rc"], 86, data["nested_stderr"])
                self.assertIn("refusing nested dispatch", data["nested_stderr"])

    def test_work_gets_temp_dir_inside_workdir(self) -> None:
        seen = set()
        for mode in ("work", "work"):
            with self.subTest(mode=mode):
                result, data = self.run_codex(mode, self.prompt(f"{mode}.txt"))
                self.assertEqual(result.returncode, 0, result.stderr)
                tmp_dir = Path(data["TMPDIR"])
                self.assertEqual(tmp_dir.parent, self.workdir / ".omnilane-tmp")
                self.assertNotIn(tmp_dir, seen)
                seen.add(tmp_dir)
                self.assertEqual(data["TMPDIR"], str(tmp_dir) + "/")
                self.assertEqual(data["TMPPREFIX"], str(tmp_dir / "zsh"))
                values = config_values(data["argv"])
                self.assertIn(f'shell_environment_policy.set.TMPDIR="{tmp_dir}/"', values)
                self.assertIn(f'shell_environment_policy.set.TMPPREFIX="{tmp_dir}/zsh"', values)
                self.assertFalse(tmp_dir.exists())
                self.assertEqual(data["tmp_mode"], 0o700)
        exclude = (self.workdir / ".git/info/exclude").read_text().splitlines()
        self.assertEqual(exclude.count("/.omnilane-tmp/"), 1)
        status = subprocess.run(["git", "-C", str(self.workdir), "status", "--porcelain"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_work_temp_is_cleaned_after_failure(self) -> None:
        result, data = self.run_codex("work", self.prompt(), FAKE_CODEX_RC="7")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertFalse(Path(data["TMPDIR"]).exists())

    def test_cleanup_failure_preserves_worker_status(self) -> None:
        for rc in (0, 7):
            result, data = self.run_codex("work", self.prompt(),
                                          FAKE_CODEX_BREAK_CLEANUP="1", FAKE_CODEX_RC=str(rc))
            self.assertEqual(result.returncode, rc, result.stderr)
            self.assertIn("cleanup failed", result.stderr)
            Path(data["TMPDIR"].rstrip("/")).unlink()

    def test_no_inherited_tmpdir(self) -> None:
        for mode in ("advise", "sysops"):
            result, data = self.run_codex(mode, self.prompt(), TMPDIR="")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(data["TMPDIR"])
            self.assertIsNone(data["tmp_mode"])

    def test_work_temp_is_cleaned_after_interruption(self) -> None:
        # The wrapper exports its own PID, then exec keeps that PID for the runner.
        wrapper = self.tmp / "interrupt-runner"
        wrapper.write_text('#!/bin/bash\nexport FAKE_CODEX_INTERRUPT=$$\nexec /bin/bash "$@"\n')
        wrapper.chmod(0o755)
        from unittest import mock
        run = subprocess.run

        def wrapped(argv, **kwargs):
            if len(argv) > 1 and argv[1].endswith("run-codex.sh"):
                argv = [str(wrapper)] + argv[1:]
            return run(argv, **kwargs)

        with mock.patch.object(subprocess, "run", wrapped):
            result, data = self.run_codex("work", self.prompt())
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertFalse(Path(data["TMPDIR"]).exists())

    def test_sysops_keeps_inherited_tmpdir(self) -> None:
        result, data = self.run_codex("sysops", self.prompt())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["TMPDIR"], str(self.tmp) + "/")
        self.assertFalse((self.workdir / ".omnilane-tmp").exists())
        self.assertFalse(any(v.startswith("shell_environment_policy.set.TMP")
                             for v in config_values(data["argv"])))

    def test_symlinked_temp_dir_is_refused(self) -> None:
        (self.workdir / ".omnilane-tmp").symlink_to(self.tmp)
        result, _ = self.run_codex("work", self.prompt())
        self.assertEqual(result.returncode, 125)
        self.assertIn("unsafe Codex work temp path", result.stderr)

    def test_provider_headers_are_off_by_default(self) -> None:
        result, data = self.run_codex("work", self.prompt())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(v.startswith("model_provider") for v in config_values(data["argv"])))

    def test_provider_headers_need_both_variables(self) -> None:
        for extra in ({"OMNILANE_CODEX_PROVIDER_BASE_URL": "http://127.0.0.1:1/backend-api/codex"},
                      {"OMNILANE_CODEX_HTTP_HEADERS": '{"x-a"="1"}'}):
            with self.subTest(extra=extra):
                result, _ = self.run_codex("work", self.prompt(), **extra)
                self.assertEqual(result.returncode, 2)
                self.assertIn("must be set together", result.stderr)

    def test_provider_headers_define_openai_auth_provider(self) -> None:
        result, data = self.run_codex(
            "work", self.prompt(),
            OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
            OMNILANE_CODEX_HTTP_HEADERS='{"x-headroom-bypass"="true"}',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = config_values(data["argv"])
        self.assertIn('model_provider="omnilane_codex"', values)
        provider = [v for v in values if v.startswith("model_providers.omnilane_codex=")]
        self.assertEqual(len(provider), 1)
        self.assertIn('base_url="http://127.0.0.1:8791/backend-api/codex"', provider[0])
        self.assertIn("requires_openai_auth=true", provider[0])
        self.assertIn('http_headers={"x-headroom-bypass"="true"}', provider[0])

    # --- R4 review (FIX-FIRST) regressions ---

    def test_advise_creates_no_temp_dir_or_exclude(self) -> None:
        result, data = self.run_codex("advise", self.prompt())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.workdir / ".omnilane-tmp").exists())
        self.assertNotIn("/.omnilane-tmp/", (self.workdir / ".git/info/exclude").read_text())
        self.assertEqual(data["TMPDIR"], str(self.tmp) + "/")
        self.assertFalse(any(v.startswith("shell_environment_policy.set.TMP")
                             for v in config_values(data["argv"])))

    def test_advise_runs_in_unwritable_workdir(self) -> None:
        self.workdir.chmod(0o555)
        self.addCleanup(self.workdir.chmod, 0o755)
        result, _ = self.run_codex("advise", self.prompt())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_predictable_worker_tmp_link_cannot_truncate_task(self) -> None:
        prompt = self.prompt("task.txt")
        Path(str(prompt) + ".worker.tmp").symlink_to(prompt)
        result, data = self.run_codex("work", prompt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(prompt.read_text(), "do the thing\n")
        self.assertTrue(data["stdin"].endswith("do the thing\n"))

    def test_header_value_cannot_inject_provider_fields(self) -> None:
        for bad in ('{}, env_http_headers={"X-Test"="PATH"}',
                    '{"x-a"={"nested"="1"}}',
                    '{"x-a"=1}',
                    '{"x a"="1"}',
                    '{"x-a"="1\\"}, base_url=\\"http://evil"}',
                    '{"x-a"="1"} # c'):
            with self.subTest(value=bad):
                result, data = self.run_codex(
                    "work", self.prompt(),
                    OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
                    OMNILANE_CODEX_HTTP_HEADERS=bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("OMNILANE_CODEX_HTTP_HEADERS", result.stderr)
                self.assertEqual(data, {})

    def test_header_table_is_reserialized(self) -> None:
        result, data = self.run_codex(
            "work", self.prompt(),
            OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
            OMNILANE_CODEX_HTTP_HEADERS='{ x-headroom-bypass = "true", "X-Two" = "a b" }')
        self.assertEqual(result.returncode, 0, result.stderr)
        provider = [v for v in config_values(data["argv"]) if v.startswith("model_providers.omnilane_codex=")]
        self.assertEqual(len(provider), 1)
        self.assertIn('http_headers={"x-headroom-bypass"="true", "X-Two"="a b"}', provider[0])

    def test_exclude_symlink_is_not_followed(self) -> None:
        outside = self.tmp / "outside-exclude"
        outside.write_text("keep\n")
        exclude = self.workdir / ".git/info/exclude"
        exclude.unlink()
        exclude.symlink_to(outside)
        result, data = self.run_codex("work", self.prompt())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), "keep\n")
        self.assertTrue(exclude.is_symlink())
        self.assertEqual(Path(data["TMPDIR"]).parent, self.workdir / ".omnilane-tmp")

    def test_symlinked_temp_dir_leaves_target_mode(self) -> None:
        target = self.tmp / "outside-dir"
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        (self.workdir / ".omnilane-tmp").symlink_to(target)
        result, _ = self.run_codex("work", self.prompt())
        self.assertEqual(result.returncode, 125)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    # --- R7 review (FIX-FIRST) regressions ---

    def test_header_value_may_contain_comma(self) -> None:
        result, data = self.run_codex(
            "work", self.prompt(),
            OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
            OMNILANE_CODEX_HTTP_HEADERS='{"Accept"="application/json, text/plain", x-b = "1"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        provider = [v for v in config_values(data["argv"]) if v.startswith("model_providers.omnilane_codex=")]
        self.assertIn('http_headers={"Accept"="application/json, text/plain", "x-b"="1"}', provider[0])

    def test_header_pairs_still_need_separators(self) -> None:
        for bad in ('{"a"="1" "b"="2"}', '{"a"="1",}', '{,"a"="1"}', '{"a"="1",, "b"="2"}'):
            with self.subTest(value=bad):
                result, data = self.run_codex(
                    "work", self.prompt(),
                    OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
                    OMNILANE_CODEX_HTTP_HEADERS=bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(data, {})

    def test_live_branch_gets_shared_codex_controls(self) -> None:
        inbox = self.tmp / "inbox.fifo"
        os.mkfifo(inbox)
        record = self.tmp / "live.record.json"
        fake = self.tmp / "codex-live-fake"
        fake.write_text(FAKE_CODEX)
        fake.chmod(0o755)
        env = fixture_environment_with_isolated_tools(self)
        env.update(OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1", CODEX_BIN=str(fake), OMNILANE_REPO=str(ROOT),
                   OMNILANE_TIMEOUT="20", OMNILANE_INBOX=str(inbox), FAKE_CODEX_RECORD=str(record),
                   OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
                   OMNILANE_CODEX_HTTP_HEADERS='{"x-headroom-bypass"="true"}')
        for key in ("OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID"):
            env.pop(key, None)
        subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/runners/run-codex.sh"), "work", str(self.workdir),
             "gpt-6-astra", "medium", str(self.prompt()), str(self.tmp / "live.out")],
            env=env, text=True, capture_output=True, check=False, timeout=60)
        data = json.loads(record.read_text())
        self.assertEqual(data["nested_rc"], 86, data["nested_stderr"])
        argv = data["argv"]
        self.assertEqual(argv[-1], "app-server")
        self.assertEqual(argv[argv.index("--disable") + 1], "multi_agent")
        values = config_values(argv)
        tmp_dir = Path(data["TMPDIR"])
        self.assertIn('model_provider="omnilane_codex"', values)
        self.assertTrue(any(v.startswith("model_providers.omnilane_codex=") and
                            'http_headers={"x-headroom-bypass"="true"}' in v for v in values))
        self.assertIn(f'shell_environment_policy.set.TMPPREFIX="{tmp_dir}/zsh"', values)
        self.assertEqual(data["TMPDIR"], str(tmp_dir) + "/")
        self.assertIn('shell_environment_policy.set.OMNILANE_DEPTH="1"', values)
        self.assertFalse(tmp_dir.exists())


    def test_codex_prompt_starts_with_worker_rules(self) -> None:
        result, data = self.run_codex("work", self.prompt())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data["stdin"].startswith(MARKER + "\n"))
        self.assertIn("omnilane route", data["stdin"])
        self.assertTrue(data["stdin"].endswith("do the thing\n"))


class EveryRunnerPrependsWorkerRules(unittest.TestCase):
    RUNNERS = {
        "codex": ("run-codex.sh", "CODEX_BIN"),
        "claude": ("run-claude.sh", "CLAUDE_BIN"),
        "gemini": ("run-gemini.sh", "AGY_BIN"),
        "grok": ("run-grok.sh", "GROK_BIN"),
    }

    def test_claude_modes_enforce_no_native_agents(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omnilane-claude-tools-") as td:
            tmp = Path(td)
            fake = tmp / "claude"
            fake.write_text('''#!/usr/bin/env python3
import json, os, subprocess, sys
nested = subprocess.run(["/bin/bash", os.environ["OMNILANE_REPO"] + "/scripts/dispatch.sh",
                         "fast", "nested test"], capture_output=True, text=True)
with open(os.environ["FAKE_CLAUDE_RECORD"], "w") as handle:
    json.dump({"argv": sys.argv[1:], "nested_rc": nested.returncode}, handle)
print("fixture")
''')
            fake.chmod(0o755)
            for mode in ("advise", "work", "sysops"):
                with self.subTest(mode=mode):
                    record = tmp / "argv"
                    prompt = tmp / "prompt"
                    prompt.write_text("task\n")
                    env = fixture_environment_with_isolated_tools(self)
                    env.update(CLAUDE_BIN=str(fake), FAKE_CLAUDE_RECORD=str(record),
                               OMNILANE_REPO=str(ROOT), OMNILANE_TIMEOUT="5")
                    for key in ("OMNILANE_INBOX", "OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID"):
                        env.pop(key, None)
                    result = subprocess.run(
                        ["/bin/bash", str(ROOT / "scripts/runners/run-claude.sh"), mode,
                         str(tmp), "model", "-", str(prompt), str(tmp / "out")],
                        env=env, text=True, capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    data = json.loads(record.read_text())
                    argv = data["argv"]
                    self.assertEqual(data["nested_rc"], 86)
                    expected = ["--disable-slash-commands", "--model", "model"]
                    if mode == "sysops":
                        expected += ["--settings", '{"sandbox":{"enabled":false}}',
                                     "--permission-mode", "bypassPermissions",
                                     "--dangerously-skip-permissions", "--disallowedTools", "Agent", "Task"]
                    else:
                        settings = {"sandbox": {"enabled": True, "failIfUnavailable": True,
                            "allowUnsandboxedCommands": False, "autoAllowBashIfSandboxed": True,
                            "excludedCommands": [], "filesystem": {"disabled": False,
                            "allowRead": [], "allowWrite": []}, "network": {"allowedDomains": []}}}
                        expected += ["--safe-mode", "--restricted", "--setting-sources", "",
                            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                            "--settings", json.dumps(settings, separators=(",", ":")),
                            "--permission-prompts", "none", "--permission-mode",
                            "plan" if mode == "advise" else "acceptEdits", "--tools",
                            "Bash,Read,Glob,Grep,WebSearch,WebFetch" if mode == "advise" else
                            "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit"]
                    expected += ["--output-format", "text", "-p", "--",
                                 Path(str(prompt) + ".worker").read_text().rstrip("\n")]
                    self.assertEqual(argv, expected)

    def test_prompt_file_gets_rules_once(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omnilane-rules-") as td:
            tmp = Path(td)
            fake = tmp / "fake-cli"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            workdir = tmp / "wd"
            workdir.mkdir()
            for vendor, (runner, bin_var) in self.RUNNERS.items():
                with self.subTest(vendor=vendor):
                    prompt = tmp / f"{vendor}.txt"
                    prompt.write_text("task body\n")
                    env = fixture_environment_with_isolated_tools(self)
                    env.update(OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1", OMNILANE_REPO=str(ROOT),
                               OMNILANE_TIMEOUT="5")
                    env[bin_var] = str(fake)
                    for key in ("OMNILANE_INBOX", "OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID"):
                        env.pop(key, None)
                    for _ in range(2):
                        subprocess.run(
                            ["/bin/bash", str(ROOT / "scripts/runners" / runner), "advise", str(workdir),
                             "model", "-", str(prompt), str(tmp / f"{vendor}.out")],
                            env=env, text=True, capture_output=True, check=False,
                        )
                    self.assertEqual(prompt.read_text(), "task body\n")
                    sent = Path(str(prompt) + ".worker")
                    text = sent.read_text()
                    self.assertTrue(text.startswith(MARKER + "\n"), text[:200])
                    self.assertEqual(text.count(MARKER), 1)
                    self.assertIn("dispatch.sh", text)
                    self.assertIn("omnilane route", text)
                    self.assertIn("sub-agent", text)
                    self.assertNotIn("'", text)
                    self.assertTrue(text.endswith("task body\n"))
                    self.assertEqual(stat.S_IMODE(sent.stat().st_mode) & 0o077, 0)


def load_setup():
    import importlib.util
    spec = importlib.util.spec_from_file_location("omnilane_codex_worker_setup",
                                                  ROOT / "scripts/lib/codex_worker_setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExcludeWriteContracts(unittest.TestCase):
    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory(prefix="omnilane-exclude-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.setup = load_setup()

    def make_job(self):
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.setup.tmpdir(str(self.repo))
        return Path(output.getvalue().strip())

    def test_readonly_cleanup_and_missing_are_safe(self) -> None:
        job = self.make_job()
        nested = job / "readonly"
        nested.mkdir()
        item = nested / "file"
        item.write_text("content")
        item.chmod(0o400)
        nested.chmod(0o500)
        before = os.getcwd()
        self.setup.cleanup(str(job))
        self.assertEqual(os.getcwd(), before)
        self.assertFalse(job.exists())
        self.setup.cleanup(str(job))

    def test_cleanup_refuses_unsafe_paths_and_links(self) -> None:
        job = self.make_job()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep").write_text("keep")
        job.rmdir()
        job.symlink_to(outside)
        for path in (str(job), str(job) + "/", str(job.parent / ".." / job.name),
                     ".omnilane-tmp/" + job.name, str(self.repo)):
            with self.subTest(path=path), self.assertRaises(SystemExit) as error:
                self.setup.cleanup(path)
            self.assertEqual(error.exception.code, 125)
        self.assertEqual((outside / "keep").read_text(), "keep")

    def test_cleanup_keeps_parent_opened_by_another_creator(self) -> None:
        from unittest import mock
        import threading
        opened = threading.Event()
        removed = threading.Event()
        real_mkdir = os.mkdir
        failures = []

        def remove_parent():
            try:
                if not opened.wait(5):
                    raise AssertionError("parent not opened")
                # A separate cleanup process runs exactly between opening
                # the shared base and mkdir(job). Old cleanup removes it.
                import sys
                missing = self.repo / ".omnilane-tmp" / ("job-" + "0" * 32)
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "scripts/lib/codex_worker_setup.py"),
                     "cleanup", str(missing)], capture_output=True, text=True, timeout=5)
                if result.returncode:
                    raise AssertionError(result.stderr)
                if not missing.parent.is_dir():
                    raise AssertionError("cleanup removed a creator's open parent")
            except BaseException as error:
                failures.append(error)
            finally:
                removed.set()

        def racing_mkdir(path, *args, **kwargs):
            if str(path).startswith("job-") and not opened.is_set():
                opened.set()
                self.assertTrue(removed.wait(5))
            return real_mkdir(path, *args, **kwargs)

        thread = threading.Thread(target=remove_parent)
        thread.start()
        try:
            with mock.patch.object(self.setup.os, "mkdir", racing_mkdir):
                job = self.make_job()
        finally:
            thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(job.is_dir())
        self.setup.cleanup(str(job))

    def test_two_processes_create_and_cleanup_shared_parent(self) -> None:
        import sys
        code = ("import importlib.util,sys,contextlib,io; "
                "s=importlib.util.spec_from_file_location('setup',sys.argv[1]); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                "for i in range(100):\n"
                " out=io.StringIO()\n"
                " with contextlib.redirect_stdout(out): m.tmpdir(sys.argv[2])\n"
                " m.cleanup(out.getvalue().strip())\n")
        argv = [sys.executable, "-B", "-c", code,
                str(ROOT / "scripts/lib/codex_worker_setup.py"), str(self.repo)]
        processes = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for _ in range(2)]
        results = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=60)
                results.append((process.returncode, stdout + stderr))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        for rc, output in results:
            self.assertEqual(rc, 0, output)
        self.assertEqual(list((self.repo / ".omnilane-tmp").iterdir()), [])

    def test_readonly_directory_with_external_symlinks(self) -> None:
        job = self.make_job()
        outside = self.tmp / "outside"
        outside.mkdir(mode=0o500)
        target = self.tmp / "target"
        target.write_text("untouched")
        target.chmod(0o400)
        nested = job / "readonly"
        nested.mkdir()
        (nested / "directory-link").symlink_to(outside)
        (nested / "file-link").symlink_to(target)
        modes = [stat.S_IMODE(p.stat().st_mode) for p in (outside, target)]
        nested.chmod(0o500)
        self.setup.cleanup(str(job))
        self.assertFalse(job.exists())
        self.assertEqual([stat.S_IMODE(p.stat().st_mode) for p in (outside, target)], modes)
        self.assertEqual(target.read_text(), "untouched")

    def test_cleanup_refuses_concurrently_swapped_directory(self) -> None:
        from unittest import mock
        job = self.make_job()
        nested = job / "nested"
        nested.mkdir(mode=0o500)
        outside = self.tmp / "outside"
        outside.mkdir(mode=0o500)
        before = stat.S_IMODE(outside.stat().st_mode)
        real_open = os.open
        swapped = []

        def swap_before_open(name, flags, *args, **kwargs):
            if name == "nested" and not swapped:
                nested.rename(job / "original")
                nested.symlink_to(outside)
                swapped.append(True)
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(self.setup.os, "open", swap_before_open):
            with self.assertRaises(SystemExit) as error:
                self.setup.cleanup(str(job))
        self.assertEqual(error.exception.code, 125)
        self.assertEqual(swapped, [True])
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), before)
        self.setup.cleanup(str(job))

    def test_write_failure_only_warns(self) -> None:
        import contextlib
        import errno
        import io
        from unittest import mock
        real_fdopen = os.fdopen

        class Full:
            def __init__(self, handle):
                self.handle = handle

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.handle.close()
                return False

            def write(self, text):
                raise OSError(errno.ENOSPC, "No space left on device")

        def full_fdopen(fd, *args, **kwargs):
            return Full(real_fdopen(fd, *args, **kwargs))

        stderr = io.StringIO()
        with mock.patch.object(self.setup.os, "fdopen", full_fdopen), contextlib.redirect_stderr(stderr):
            self.setup.add_exclude(str(self.repo))
        self.assertIn("warning", stderr.getvalue())
        self.assertIn("No space left", stderr.getvalue())

    def test_info_swapped_after_check_is_not_followed(self) -> None:
        import contextlib
        import io
        from unittest import mock
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "exclude").write_text("keep\n")
        info = self.repo / ".git/info"
        real_open = os.open
        swapped = []

        def swapping_open(path, flags, *args, **kwargs):
            if str(path).endswith("exclude") and not swapped:
                info.rename(info.with_name("info.real"))
                info.symlink_to(outside)
                swapped.append(True)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(self.setup.os, "open", swapping_open), contextlib.redirect_stderr(io.StringIO()):
            self.setup.add_exclude(str(self.repo))
        self.assertTrue(swapped)
        self.assertEqual((outside / "exclude").read_text(), "keep\n")
        self.assertIn("/.omnilane-tmp/", (self.repo / ".git/info.real/exclude").read_text())



if __name__ == "__main__":
    unittest.main()
