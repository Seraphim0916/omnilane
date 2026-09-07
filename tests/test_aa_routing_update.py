#!/usr/bin/env python3
"""Offline contract tests for the 2026-09-05 AA routing refresh."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts" / "dispatch.sh"


EXPECTED_DEFAULTS = {
    "hardest-coding": "claude claude-fable-5-1 max",
    "bulk-mechanical": "codex gpt-5.6-sol high",
    "triage": "codex gpt-5.6-luna high",
    "hard-judgment": "claude claude-fable-5-1 xhigh",
    "taste-final": "claude claude-fable-5-1 xhigh",
    "consult": "codex gpt-6-astra xhigh",
    "ui-draft": "codex gpt-5.6-sol high",
    "long-context": "gemini gemini-3.8-flash-medium -",
    "fast-agentic": "gemini gemini-3.8-flash-low -",
    "live-search": "grok grok-4.6 -",
    "coding-overflow": "grok grok-4.6 -",
    "arbitrate": "off - -",
}


class AARoutingUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-aa-routing-")
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.bin = self.base / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.fake_ok = self._script("provider-ok", "exit 0\n")
        # These legacy routing tests exercise provider-selection behavior, not
        # model lineage.  Give the fixture an explicit synthetic-human caller
        # and never inherit a real caller context from the invoking harness.
        self.env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("OMNILANE_AA_") and name != "OMNILANE_DEPTH"
        }
        self.env.update(
            {
                "OMNILANE_HOME": str(self.home),
                "OMNILANE_AA_OPERATOR_ASSERTED_HUMAN": "1",
                "CODEX_BIN": str(self.fake_ok),
                "CLAUDE_BIN": str(self.fake_ok),
                "GROK_BIN": str(self.fake_ok),
                "AGY_BIN": str(self.fake_ok),
            }
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _script(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(DISPATCH), *args],
            cwd=ROOT,
            env=env or self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @staticmethod
    def _listed(stdout: str) -> dict[str, str]:
        rows: dict[str, str] = {}
        for line in stdout.splitlines():
            match = re.match(r"^([a-z][a-z0-9-]*):\s+(.*?)(?:\s+\(fallback \d+/\d+\))?$", line)
            if match:
                rows[match.group(1)] = match.group(2)
        return rows

    def test_all_twelve_default_lane_selections(self) -> None:
        result = self._run("--list")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(EXPECTED_DEFAULTS, self._listed(result.stdout))

    def test_local_whole_lane_precedence(self) -> None:
        (self.home / "routing.local.yaml").write_text(
            "consult: codex local-fixture low | claude untouched-fixture high\n",
            encoding="utf-8",
        )
        result = self._run("--list")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("codex local-fixture low", self._listed(result.stdout)["consult"])
        self.assertEqual(EXPECTED_DEFAULTS["triage"], self._listed(result.stdout)["triage"])

    def test_explicit_vendor_errors_and_model_effort_override(self) -> None:
        (self.home / "routing.local.yaml").write_text(
            "probe: codex route-codex high | claude route-claude xhigh\n",
            encoding="utf-8",
        )
        absent = self._run("--vendor", "grok", "probe", "fixture")
        self.assertEqual(2, absent.returncode, absent.stdout + absent.stderr)

        missing_env = self.env.copy()
        missing_env["CLAUDE_BIN"] = str(self.bin / "missing-claude")
        unavailable = self._run("--vendor", "claude", "probe", "fixture", env=missing_env)
        self.assertEqual(4, unavailable.returncode, unavailable.stdout + unavailable.stderr)

        explicit = self._run(
            "--dry-run",
            "--vendor",
            "codex",
            "--model",
            "explicit-fixture",
            "--effort",
            "low",
            "probe",
            "fixture",
        )
        self.assertEqual(0, explicit.returncode, explicit.stdout + explicit.stderr)
        self.assertIn("model=explicit-fixture\n", explicit.stdout)
        self.assertIn("effort=low\n", explicit.stdout)

    def test_provider_failure_does_not_try_second_vendor(self) -> None:
        marker = self.base / "claude-called"
        codex_fail = self._script("codex-quota-fixture", "exit 75\n")
        claude_mark = self._script("claude-marker", f"printf called > {marker!s}\nexit 0\n")
        env = self.env.copy()
        env.update({"CODEX_BIN": str(codex_fail), "CLAUDE_BIN": str(claude_mark)})
        (self.home / "routing.local.yaml").write_text(
            "probe: codex quota-fixture high | claude second-fixture high\n",
            encoding="utf-8",
        )
        result = self._run("probe", "fixture", env=env)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(marker.exists(), "provider failure crossed into the second vendor")

    def test_direct_api_refuses_work_before_provider_call(self) -> None:
        (self.home / "routing.local.yaml").write_text(
            "probe: openrouter fixture/model - | codex fallback-fixture high\n",
            encoding="utf-8",
        )
        env = self.env.copy()
        env["OPENROUTER_API_KEY"] = "fixture-not-a-secret"
        result = self._run("--mode", "work", "--workdir", str(ROOT), "probe", "fixture", env=env)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)

    def test_catalog_and_vote_models_are_synchronized(self) -> None:
        configure = (ROOT / "scripts" / "configure.sh").read_text(encoding="utf-8")
        for model in (
            "gpt-6-astra",
            "gemini-3.8-flash-high",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-low",
            "claude-fable-5-1",
        ):
            self.assertIn(model, configure)

        vote = (ROOT / "scripts" / "runners" / "run-vote.sh").read_text(encoding="utf-8")
        self.assertIn("printf 'gpt-6-astra\\txhigh'", vote)
        self.assertIn("printf 'claude-fable-5-1\\txhigh'", vote)
        self.assertIn("printf 'gemini-3.8-flash-medium\\t-'", vote)
        self.assertIn("printf 'grok-4.6\\t-'", vote)


if __name__ == "__main__":
    unittest.main()
