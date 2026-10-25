#!/usr/bin/env python3
"""Offline contracts: workers do not delegate, and Codex gets a writable temp dir."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from offline_env import fixture_environment_with_isolated_tools

ROOT = Path(__file__).resolve().parents[1]
MARKER = "[omnilane worker rules]"

FAKE_CODEX = """#!/usr/bin/env bash
set -euo pipefail
FAKE_STDIN="$(cat; printf x)"
export FAKE_STDIN="${FAKE_STDIN%x}"
python3 - "$FAKE_CODEX_RECORD" "$@" <<'PY'
import json, os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "argv": sys.argv[2:],
    "TMPDIR": os.environ.get("TMPDIR"),
    "TMPPREFIX": os.environ.get("TMPPREFIX"),
    "stdin": os.environ["FAKE_STDIN"],
}))
PY
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then shift; out="$1"; fi
  shift
done
printf 'fixture\\n' > "$out"
printf '{"type":"thread.started","thread_id":"fixture"}\\n'
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

    def test_work_gets_temp_dir_inside_workdir(self) -> None:
        tmp_dir = self.workdir / ".omnilane-tmp"
        for mode in ("work", "work"):
            with self.subTest(mode=mode):
                result, data = self.run_codex(mode, self.prompt(f"{mode}.txt"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data["TMPDIR"], str(tmp_dir) + "/")
                self.assertEqual(data["TMPPREFIX"], str(tmp_dir / "zsh"))
                values = config_values(data["argv"])
                self.assertIn(f'shell_environment_policy.set.TMPDIR="{tmp_dir}/"', values)
                self.assertIn(f'shell_environment_policy.set.TMPPREFIX="{tmp_dir}/zsh"', values)
                self.assertTrue(tmp_dir.is_dir())
                self.assertEqual(stat.S_IMODE(tmp_dir.stat().st_mode), 0o700)
        exclude = (self.workdir / ".git/info/exclude").read_text().splitlines()
        self.assertEqual(exclude.count("/.omnilane-tmp/"), 1)
        status = subprocess.run(["git", "-C", str(self.workdir), "status", "--porcelain"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

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
        self.assertEqual(data["TMPDIR"], str(self.workdir / ".omnilane-tmp") + "/")

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
        fake.write_text("""#!/usr/bin/env python3
import json, os, sys
json.dump({"argv": sys.argv[1:], "TMPDIR": os.environ.get("TMPDIR")}, open(os.environ["FAKE_LIVE_RECORD"], "w"))
sys.exit(3)
""")
        fake.chmod(0o755)
        env = fixture_environment_with_isolated_tools(self)
        env.update(OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1", CODEX_BIN=str(fake), OMNILANE_REPO=str(ROOT),
                   OMNILANE_TIMEOUT="20", OMNILANE_INBOX=str(inbox), FAKE_LIVE_RECORD=str(record),
                   OMNILANE_CODEX_PROVIDER_BASE_URL="http://127.0.0.1:8791/backend-api/codex",
                   OMNILANE_CODEX_HTTP_HEADERS='{"x-headroom-bypass"="true"}')
        for key in ("OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID"):
            env.pop(key, None)
        subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/runners/run-codex.sh"), "work", str(self.workdir),
             "gpt-6-astra", "medium", str(self.prompt()), str(self.tmp / "live.out")],
            env=env, text=True, capture_output=True, check=False, timeout=60)
        data = json.loads(record.read_text())
        argv = data["argv"]
        self.assertEqual(argv[-1], "app-server")
        self.assertEqual(argv[argv.index("--disable") + 1], "multi_agent")
        values = config_values(argv)
        tmp_dir = self.workdir / ".omnilane-tmp"
        self.assertIn('model_provider="omnilane_codex"', values)
        self.assertTrue(any(v.startswith("model_providers.omnilane_codex=") and
                            'http_headers={"x-headroom-bypass"="true"}' in v for v in values))
        self.assertIn(f'shell_environment_policy.set.TMPPREFIX="{tmp_dir}/zsh"', values)
        self.assertEqual(data["TMPDIR"], str(tmp_dir) + "/")


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
