#!/usr/bin/env python3
"""Job artifacts must be private before the vendor finishes, without changing its umask."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class RunnerPrivateFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-private-files-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.job = self.root / "job"
        self.job.mkdir()
        self.output = self.job / "out.txt"
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("offline prompt\n")
        self.ready = self.root / "ready"
        self.release = self.root / "release"
        self.project_file = self.root / "project-file"
        self.env.update(
            OMNILANE_HOME=str(self.root / "isolated/home/.omnilane"),
            OMNILANE_TIMEOUT="15",
            FIXTURE_READY=str(self.ready),
            FIXTURE_RELEASE=str(self.release),
            FIXTURE_PROJECT_FILE=str(self.project_file),
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.fake = self.root / "fake-vendor"
        self.fake.write_text(r"""#!/usr/bin/env python3
import os
from pathlib import Path
import sys
import time
Path(os.environ["FIXTURE_PROJECT_FILE"]).write_text("project output\n")
if os.environ.get("FIXTURE_STDERR", "1") == "1":
    print("fixture diagnostic", file=sys.stderr, flush=True)
if "-o" in sys.argv:
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text("completed vendor result\n")
print(os.environ.get("FIXTURE_RESPONSE", "completed vendor result"), flush=True)
Path(os.environ["FIXTURE_READY"]).touch()
deadline = time.monotonic() + 10
while not Path(os.environ["FIXTURE_RELEASE"]).exists():
    if time.monotonic() >= deadline:
        raise SystemExit("fixture release timed out")
    time.sleep(0.02)
""")
        self.fake.chmod(0o755)

    def assert_private(self, expected):
        files = sorted(self.job.glob("out.txt*"))
        self.assertEqual({p.name for p in files}, set(expected))
        for path in files:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))

    def run_sleeping_runner(self, vendor, empty_stderr=False):
        self.env["FIXTURE_STDERR"] = "0" if empty_stderr else "1"
        self.env[f"{vendor.upper()}_BIN"] = str(self.fake)
        model = str(self.fake) if vendor == "exec" else "offline-model"
        effort = "-"
        if vendor == "vote":
            self.env["CLAUDE_BIN"] = str(self.fake)
            model, effort = "claude", "1"
        if vendor == "openai-compat":
            fake_bin = self.root / "fake-bin"
            fake_bin.mkdir()
            (fake_bin / "curl").symlink_to(self.fake)
            self.env["PATH"] = str(fake_bin) + os.pathsep + self.env["PATH"]
            self.env.update(
                OMNILANE_OAI_VENDOR="groq", GROQ_API_KEY="offline-fixture-only",
                FIXTURE_RESPONSE='{"choices":[{"message":{"content":"completed vendor result\\n"}}]}',
            )
        command = [
            "bash", "-c", 'umask 022; exec bash "$@"', "fixture",
            str(ROOT / f"scripts/runners/run-{vendor}.sh"),
            "advise" if vendor in ("grok", "vote", "openai-compat") else "work",
            str(self.root), model, effort, str(self.prompt), str(self.output),
        ]
        process = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not self.ready.exists():
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.fail(f"runner exited {process.returncode}: {stdout!r} {stderr!r}")
                self.assertLess(time.monotonic(), deadline, "vendor did not start")
                time.sleep(0.02)
            self.assertIsNone(process.poll())
            running = {"out.txt.stderr.log"}
            if vendor not in ("exec", "vote", "openai-compat"):
                running.add("out.txt.tmp")
            if vendor == "codex":
                running.add("out.txt.progress.log")
            if vendor == "vote":
                running = {"out.txt"}
            if vendor == "openai-compat":
                running.update({"out.txt.headers", "out.txt.request.json", "out.txt.response.json"})
            self.assert_private(running)
            self.assertEqual(stat.S_IMODE(self.project_file.stat().st_mode), 0o644)
            self.release.touch()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, (stdout, stderr))
            ended = set(running)
            if vendor not in ("exec", "vote", "openai-compat"):
                ended.remove("out.txt.tmp")
                ended.add("out.txt")
                self.assertEqual(self.output.read_text(), "completed vendor result\n")
            if vendor == "vote":
                self.assertIn("completed vendor result\n", self.output.read_text())
            if vendor == "openai-compat":
                ended = {"out.txt", "out.txt.stderr.log"}
                self.assertEqual(self.output.read_text(), "completed vendor result\n")
            if empty_stderr:
                ended.remove("out.txt.stderr.log")
            self.assert_private(ended)
        finally:
            self.release.touch()
            if process.poll() is None:
                process.communicate(timeout=15)

    def test_exec_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("exec")

    def test_qwen_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("qwen")

    def test_codex_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("codex")

    def test_claude_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("claude")

    def test_grok_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("grok")

    def test_kimi_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("kimi")

    def test_opencode_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("opencode")

    def test_vote_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("vote")

    def test_openai_compat_private_while_running_and_after_exit(self):
        self.run_sleeping_runner("openai-compat")

    def test_exec_still_removes_empty_stderr(self):
        self.run_sleeping_runner("exec", empty_stderr=True)

    def test_qwen_still_removes_empty_stderr(self):
        self.run_sleeping_runner("qwen", empty_stderr=True)

    def test_helper_preserves_existing_content_and_parent_umask(self):
        existing = self.job / "existing"
        existing.write_text("keep me\n")
        existing.chmod(0o644)
        new = self.job / "new"
        result = subprocess.run(
            ["bash", "-ec", 'umask 022; source "$1"; private_job_files "$2" "$3"; umask',
             "fixture", str(ROOT / "scripts/lib/common.sh"), str(existing), str(new)],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0022")
        self.assertEqual(existing.read_text(), "keep me\n")
        self.assertEqual(new.read_bytes(), b"")
        for path in (existing, new):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
