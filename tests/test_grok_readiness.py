"""Grok advise isolates MCP readiness without disabling hooks or permissions."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GrokReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="omnilane-grok-readiness-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.prompt = self.base / "prompt"
        self.prompt.write_text("Read official documentation without modifying files.\n")
        self.record = self.base / "record.json"
        self.fake = self.bin / "grok"
        self.fake.write_text("""#!/usr/bin/env python3
import json, os, pathlib, stat, sys
name = os.environ.get('CONTEXT_MODE_MCP_SENTINEL_DIR')
path = pathlib.Path(name) if name else None
data = {'args': sys.argv[1:], 'sentinel_dir': name,
        'exists': bool(path and path.is_dir()),
        'contents': sorted(p.name for p in path.iterdir()) if path and path.is_dir() else [],
        'mode': stat.S_IMODE(path.stat().st_mode) if path and path.is_dir() else None,
        'hooks_env': {k: os.environ.get(k) for k in ['GROK_CLAUDE_HOOKS_ENABLED', 'GROK_CURSOR_HOOKS_ENABLED']}}
pathlib.Path(os.environ['TEST_GROK_RECORD']).write_text(json.dumps(data))
print('mock Grok response')
raise SystemExit(int(os.environ.get('TEST_GROK_RC', '0')))
""")
        self.fake.chmod(0o700)
        # Exercise the existing Linux work branch independently of the test host.
        (self.bin / "uname").write_text("#!/bin/sh\nprintf 'Linux\\n'\n")
        (self.bin / "uname").chmod(0o700)
        self.env = os.environ.copy()
        for name in ("CONTEXT_MODE_MCP_SENTINEL_DIR", "OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID", "OMNILANE_INBOX", "OMNILANE_GROK_NO_WEB"):
            self.env.pop(name, None)
        self.env.update(GROK_BIN=str(self.fake), TEST_GROK_RECORD=str(self.record),
                        OMNILANE_GROK_MAX_ATTEMPTS="1", OMNILANE_TIMEOUT="15",
                        TMPDIR=str(self.base), PATH=str(self.bin) + os.pathsep + self.env["PATH"])

    def run_mode(self, mode="advise", **extra):
        return subprocess.run(["/bin/bash", str(ROOT / "scripts/runners/run-grok.sh"),
                               mode, str(self.base), "grok-test", "-", str(self.prompt),
                               str(self.base / "output")], env={**self.env, **extra},
                              capture_output=True, text=True, timeout=20)

    def test_advise_uses_private_empty_job_scope_and_keeps_native_denies(self):
        unrelated = self.base / "context-mode-mcp-ready-unrelated"
        unrelated.write_text("preserve this foreign marker")
        result = self.run_mode()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.record.read_text())
        self.assertTrue(data["exists"])
        self.assertEqual(data["mode"], 0o700)
        self.assertEqual(data["contents"], [])
        self.assertNotEqual(data["sentinel_dir"], str(self.base))
        self.assertFalse(Path(data["sentinel_dir"]).exists(), "owned leaf must be cleaned")
        self.assertEqual(unrelated.read_text(), "preserve this foreign marker")
        args = data["args"]
        denies = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "--deny"]
        self.assertTrue({"Bash", "Edit", "MCPTool"}.issubset(denies))
        self.assertEqual(args[args.index("--output-format") + 1], "plain")
        selected = args[args.index("--tools") + 1].split(",")
        self.assertEqual(selected, ["Bash", "Read", "Glob", "Grep", "web_search", "web_fetch"])
        allows = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "--allow"]
        self.assertTrue({"WebSearch", "WebFetch"}.issubset(allows))
        self.assertNotIn("--always-approve", args)
        for key, value in data["hooks_env"].items():
            self.assertEqual(value, self.env.get(key), "do not disable compatibility hooks")

    def test_advise_does_not_overwrite_explicit_readiness_scope(self):
        external = self.base / "explicit-scope"
        external.mkdir()
        marker = external / "context-mode-mcp-ready-external"
        marker.write_text("caller-owned")
        result = self.run_mode(CONTEXT_MODE_MCP_SENTINEL_DIR=str(external))
        self.assertEqual(result.returncode, 2)
        self.assertIn("CONTEXT_MODE_MCP_SENTINEL_DIR", result.stderr)
        self.assertFalse(self.record.exists(), "conflict must stop before provider startup")
        self.assertEqual(marker.read_text(), "caller-owned")

    def test_sysops_preserves_explicit_scope_and_permissions(self):
        scope = str(self.base / "caller-scope")
        result = self.run_mode("sysops", CONTEXT_MODE_MCP_SENTINEL_DIR=scope)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.record.read_text())
        self.assertEqual(data["sentinel_dir"], scope)
        self.assertIn("--always-approve", data["args"])
        self.assertEqual(data["args"][data["args"].index("--sandbox") + 1], "off")

    def test_linux_work_preserves_scope_and_network_restriction(self):
        scope = str(self.base / "caller-scope")
        result = self.run_mode("work", CONTEXT_MODE_MCP_SENTINEL_DIR=scope)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.record.read_text())
        self.assertEqual(data["sentinel_dir"], scope)
        self.assertIn("--disable-web-search", data["args"])
        self.assertEqual(data["args"][data["args"].index("--sandbox") + 1], "strict")

    def test_owned_readiness_scope_is_cleaned_on_provider_error(self):
        result = self.run_mode(TEST_GROK_RC="5")
        self.assertEqual(result.returncode, 5)
        data = json.loads(self.record.read_text())
        self.assertTrue(data["exists"])
        self.assertFalse(Path(data["sentinel_dir"]).exists())


if __name__ == "__main__":
    unittest.main()
