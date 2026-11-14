#!/usr/bin/env python3
"""Offline contract tests for the 0.63.0 (aa-v5.0.1-2026-11-13-v2) AA routing defaults."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest


from offline_env import fixture_environment_with_isolated_tools

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts" / "dispatch.sh"


# 2026-11-13 AA v5.0.1 full-lane value rerank (0.63.0, snapshot aa-v5.0.1-2026-11-13-v2):
# six value lanes place the full aa_rebaseline.py value chain first; Carbon heads hardest-coding
# and ui-draft (with Opus 5.5 high next), Fable 5.2 xhigh heads hard-judgment, GPT-6.1 Sol xhigh
# heads bulk-mechanical, and GPT-6 Astra medium heads long-context.
EXPECTED_DEFAULTS = {
    # 2026-11-13 (0.63.0): full-lane AA v5.0.1 value rerank moves bulk-mechanical's head to
    # GPT-6.1 Sol xhigh and places Opus 5.5 high right after Carbon in hardest-coding.
    # Hydrogen high sits right after Argon high in every lane that lists Argon and heads no lane.
    # 2026-11-12: Carbon heads hardest-coding and ui-draft. --list shows chain heads only; a host without
    # Carbon transport proof skips it at dispatch (test_human_lane_dispatch_skips_unproven_carbon).
    "hardest-coding": "gemini gemini-4-carbon-high -",
    "bulk-mechanical": "codex gpt-6.1-sol xhigh",
    "triage": "codex gpt-6-luna high",
    "hard-judgment": "claude claude-fable-5-2 xhigh",
    "taste-final": "claude claude-sonnet-5-5 xhigh",
    "consult": "codex gpt-6-astra xhigh",
    "ui-draft": "gemini gemini-4-carbon-medium -",
    "long-context": "codex gpt-6-astra medium",
    "fast-agentic": "codex gpt-6.1-sol medium",
    "live-search": "grok grok-4.7 high",
    "coding-overflow": "grok grok-4.7 high",
    "arbitrate": "off - -",
}
CARBON_FIRST = {"hardest-coding": ["gemini gemini-4-carbon-high -", "gemini gemini-4-carbon-low -"],
                "ui-draft": ["gemini gemini-4-carbon-medium -"]}
# Next value rung in line on a host without Carbon proof (2026-11-13 AA v5.0.1 value rerank).
BEFORE_CARBON = {"hardest-coding": "claude claude-opus-5-5 high", "ui-draft": "claude claude-opus-5-5 high"}


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
            for name, value in fixture_environment_with_isolated_tools(self).items()
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

    def test_argon_and_hydrogen_precede_flash_under_expected_lane_heads(self) -> None:
        """Verify bulk-mechanical (0.63.0 value head Sol xhigh) and four purpose lanes keep EXPECTED_DEFAULTS[lane] first and Argon high -> Hydrogen high -> Flash in order."""
        lanes = {}
        for line in (ROOT / "routing.yaml").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([a-z-]+):\s*([^#]+)", line)
            if match:
                lanes[match.group(1)] = [item.strip() for item in match.group(2).split("|")]
        for lane in ("bulk-mechanical", "triage", "fast-agentic", "live-search", "coding-overflow"):
            with self.subTest(lane=lane):
                chain = lanes[lane]
                argon = "gemini gemini-4-argon-high -"
                self.assertEqual(1, chain.count(argon))
                flash = next(i for i, item in enumerate(chain)
                             if item.startswith("gemini gemini-3.8-flash-"))
                # 2026-11-13: Gemini 4 Hydrogen high sits between Argon high and Flash.
                self.assertEqual("gemini gemini-4-hydrogen-high -", chain[flash - 1])
                self.assertEqual(flash - 2, chain.index(argon))
                self.assertEqual(EXPECTED_DEFAULTS[lane], chain[0])

    def test_agy_only_host_selects_argon_in_every_flash_lane(self) -> None:
        env = self.env.copy()
        env.update({name: str(self.bin / "not-installed")
                    for name in ("CODEX_BIN", "CLAUDE_BIN", "GROK_BIN")})
        result = self._run("--list", env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        selected = self._listed(result.stdout)
        # --list shows the first installed vendor's row; Carbon heads two lanes, Argon every other one
        # (Gemini 4 Hydrogen high sits right after Argon, 2026-11-13).
        carbon = {"hardest-coding": "gemini gemini-4-carbon-high -", "ui-draft": "gemini gemini-4-carbon-medium -"}
        for lane in EXPECTED_DEFAULTS.keys() - {"arbitrate"}:
            self.assertEqual(selected[lane].split("#", 1)[0].strip(),
                             carbon.get(lane, "gemini gemini-4-argon-high -"), lane)
        # Dispatch, unlike --list, needs Carbon's transport proof even for the human exemption.
        for lane in ("hardest-coding", "ui-draft"):
            dry = self._run("--dry-run", lane, "fixture", env=env)
            self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
            self.assertIn("model=gemini-4-argon-high\n", dry.stdout, lane)
        consult = self._run("--dry-run", "--vendor", "gemini", "consult", "fixture", env=env)
        self.assertEqual(0, consult.returncode, consult.stdout + consult.stderr)
        self.assertIn("model=gemini-4-argon-high\n", consult.stdout)

    def test_carbon_heads_its_two_lanes_in_routing_yaml(self) -> None:
        lanes = {}
        for line in (ROOT / "routing.yaml").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([a-z-]+):\s*([^#]+)", line)
            if match:
                lanes[match.group(1)] = [item.strip() for item in match.group(2).split("|")]
        for lane, head in CARBON_FIRST.items():
            self.assertEqual(head + [BEFORE_CARBON[lane]], lanes[lane][:len(head) + 1], lane)
        for lane, chain in lanes.items():
            if lane not in CARBON_FIRST:
                self.assertFalse(any("gemini-4-carbon" in item for item in chain), lane)

    def test_human_lane_dispatch_skips_unproven_carbon(self) -> None:
        expected = {"hardest-coding": ("claude-opus-5-5", "high"), "ui-draft": ("claude-opus-5-5", "high")}
        for lane, (model, effort) in expected.items():
            with self.subTest(lane=lane):
                dry = self._run("--dry-run", lane, "fixture")
                self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
                self.assertIn(f"model={model}\n", dry.stdout)
                self.assertIn(f"effort={effort}\n", dry.stdout)
                self.assertNotIn("gemini-4-carbon", dry.stdout)

    def test_human_exemption_refuses_unproven_carbon_selectors(self) -> None:
        # Quick start exports the human assertion and agy exists, but no probe has proven Carbon.
        cases = [("--model", "gemini-4-carbon-high"), ("--model", "gemini-4-carbon-medium"),
                 ("--model", "gemini-4-carbon-low"),
                 ("--target-config", "gemini/gemini-4-carbon")]
        for lane in ("hardest-coding", "ui-draft", "consult"):
            for flag, value in cases:
                with self.subTest(lane=lane, flag=flag, value=value):
                    args = ["--dry-run", "--vendor", "gemini", flag, value]
                    if flag == "--target-config":
                        args += ["--model", "gemini-4-carbon-high"]
                    result = self._run(*args, lane, "fixture")
                    self.assertEqual(3, result.returncode, result.stdout + result.stderr)
                    self.assertNotIn("dry_run=yes", result.stdout)
                    self.assertIn("target-transport", result.stderr)
                    self.assertIn("omnilane resign --vendor gemini", result.stderr)

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
            "claude-fable-5-2",
        ):
            self.assertIn(model, configure)

        vote = (ROOT / "scripts" / "runners" / "run-vote.sh").read_text(encoding="utf-8")
        self.assertIn("printf 'gpt-6-astra\\txhigh'", vote)
        self.assertIn("printf 'claude-fable-5-2\\tlow'", vote)
        self.assertIn("printf 'gemini-3.8-flash-medium\\t-'", vote)
        self.assertIn("printf 'grok-4.7\\thigh'", vote)
        self.assertNotIn("grok-4.6", vote)
        # dispatch.sh pre-checks the panel with its own copy of the voter table; keep it in step.
        dispatch = DISPATCH.read_text(encoding="utf-8")
        self.assertIn("claude) printf 'claude-fable-5-2\\tlow\\n' ;;", dispatch)
        self.assertIn("grok) printf 'grok-4.7\\thigh\\n' ;;", dispatch)
        self.assertNotIn("printf 'grok-4.6", dispatch)


if __name__ == "__main__":
    unittest.main()
