"""Non-Claude runners never hand a caller's Claude Code environment to another vendor's CLI."""
import offline_env  # Activate suite isolation for direct file execution.
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import fixture_environment_with_isolated_tools

ROOT = Path(__file__).resolve().parents[1]

# A dispatch from a Claude Code session inherits that session's environment:
# CLAUDE_PLUGIN_ROOT made `grok -p` cancel every prompt, and token-like values
# such as CLAUDE_CODE_MESSAGING_TOKEN must not reach another vendor's worker.
CALLER_ENV = {
    "CLAUDECODE": "1",
    "CLAUDE_PLUGIN_ROOT": "/caller/plugin",
    "CLAUDE_PLUGIN_DATA": "/caller/data",
    "CLAUDE_PROJECT_DIR": "/caller/project",
    "CLAUDE_CODE_SESSION_ID": "caller-session",
    "CLAUDE_CODE_MESSAGING_TOKEN": "caller-secret-not-for-workers",
    "CLAUDE_CONFIG_DIR": "/caller/claude-config",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/caller-proxy",
    "OMNILANE_TEST_UNRELATED": "kept",
}

FAKE_CLI = """#!/usr/bin/env python3
import json, os, pathlib, sys
leaked = sorted(k for k in os.environ
                if k == "CLAUDECODE" or k.startswith("CLAUDE") or k.startswith("ANTHROPIC_"))
pathlib.Path(os.environ["TEST_RECORD"]).write_text(json.dumps(
    {"leaked": leaked, "unrelated": os.environ.get("OMNILANE_TEST_UNRELATED")}))
args = sys.argv[1:]
if "-o" in args:
    pathlib.Path(args[args.index("-o") + 1]).write_text("mock response\\n")
print("mock response")
"""


class RunnerClaudeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="omnilane-runner-claude-env-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.prompt = self.base / "prompt"
        self.prompt.write_text("Answer with one word.\n")
        self.record = self.base / "record.json"
        for name in ("codex", "agy", "grok"):
            fake = self.bin / name
            fake.write_text(FAKE_CLI)
            fake.chmod(0o700)
        self.env = {
            name: value
            for name, value in fixture_environment_with_isolated_tools(self).items()
            if not name.startswith("OMNILANE_AA_") and name != "OMNILANE_DEPTH"
        }
        for name in ("OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID", "OMNILANE_THREAD_NAME",
                     "OMNILANE_INBOX", "OMNILANE_GROK_NO_WEB"):
            self.env.pop(name, None)
        self.env.update(
            CODEX_BIN=str(self.bin / "codex"),
            AGY_BIN=str(self.bin / "agy"),
            GROK_BIN=str(self.bin / "grok"),
            TEST_RECORD=str(self.record),
            OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1",
            OMNILANE_GROK_MAX_ATTEMPTS="1",
            OMNILANE_TIMEOUT="15",
            TMPDIR=str(self.base),
            PATH=str(self.bin) + os.pathsep + self.env["PATH"],
        )
        self.env.update(CALLER_ENV)
        # The Gemini runner prepares isolated agy settings from $HOME/.gemini.
        (Path(self.env["HOME"]) / ".gemini").mkdir(parents=True, exist_ok=True)

    def run_runner(self, runner, model):
        if self.record.exists():
            self.record.unlink()
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/runners" / runner), "advise", str(self.base),
             model, "-", str(self.prompt), str(self.base / "output")],
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertTrue(self.record.exists(),
                        f"{runner} never invoked the CLI: rc={result.returncode}\n{result.stderr}")
        return json.loads(self.record.read_text())

    def test_codex_runner_strips_the_caller_claude_environment(self):
        seen = self.run_runner("run-codex.sh", "gpt-6.1-sol")
        self.assertEqual(seen["leaked"], [])
        self.assertEqual(seen["unrelated"], "kept")

    def test_gemini_runner_strips_the_caller_claude_environment(self):
        seen = self.run_runner("run-gemini.sh", "gemini-3.8-flash-low")
        self.assertEqual(seen["leaked"], [])
        self.assertEqual(seen["unrelated"], "kept")

    def test_grok_runner_strips_the_caller_claude_environment(self):
        seen = self.run_runner("run-grok.sh", "grok-4.6")
        self.assertEqual(seen["leaked"], [])
        self.assertEqual(seen["unrelated"], "kept")


if __name__ == "__main__":
    unittest.main()
