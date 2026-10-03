"""Re-sign anchors and probes the binary dispatch runs: the host's *_BIN override when set."""
import offline_env  # Activate suite isolation for direct file execution.
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import build_overlay  # noqa: E402
import probe_sweep  # noqa: E402

BINS = ("CODEX_BIN", "CLAUDE_BIN", "GROK_BIN", "AGY_BIN")


class CliOverrideResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-cli-override-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.on_path = self.fake("on-path/codex")
        self.pinned = self.fake("pinned/codex-pinned")
        self.clean = {key: value for key, value in os.environ.items() if key not in BINS}
        self.clean["PATH"] = f"{self.on_path.parent}{os.pathsep}{self.clean.get('PATH', '')}"
        # The resolver reads the host's local.sh; point it at a home that has none.
        self.clean["OMNILANE_HOME"] = str(self.root / "home")

    def fake(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_bare_name_follows_path_when_no_override_is_set(self):
        with mock.patch.dict(os.environ, self.clean, clear=True):
            self.assertEqual(build_overlay.cli_command("codex"), "codex")
            self.assertEqual(build_overlay.cli_path("codex"), self.on_path)
            self.assertEqual(probe_sweep.command("codex", "gpt-5.6-sol", "high")[0], "codex")

    def test_override_wins_for_the_anchor_and_the_probe(self):
        env = dict(self.clean, CODEX_BIN=str(self.pinned))
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(build_overlay.cli_path("codex"), self.pinned)
            self.assertEqual(probe_sweep.command("codex", "gpt-5.6-sol", "high")[0], str(self.pinned))

    def test_every_probed_vendor_honours_its_override(self):
        env = dict(self.clean, CLAUDE_BIN="/x/claude", GROK_BIN="/x/grok", AGY_BIN="/x/agy")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(probe_sweep.command("claude", "claude-opus-5", "high")[0], "/x/claude")
            self.assertEqual(probe_sweep.command("grok", "grok-4.6", "high")[0], "/x/grok")
            self.assertEqual(probe_sweep.command("gemini", "gemini-3.8-flash-low", None, "app")[0], "/x/agy")

    def test_missing_override_target_is_an_error_not_a_silent_path_fallback(self):
        env = dict(self.clean, CODEX_BIN=str(self.root / "nowhere" / "codex"))
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                build_overlay.cli_path("codex")


if __name__ == "__main__":
    unittest.main()
