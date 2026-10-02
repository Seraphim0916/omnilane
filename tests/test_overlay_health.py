"""Doctor's transport-overlay check: the AA gate must not fail silently.

Fixtures are synthesised so the suite stays portable; nothing here reads the
host's ~/.omnilane or contacts a provider.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "scripts" / "lib" / "overlay_health.py"
REGISTRY = json.loads((ROOT / "config" / "aa-model-policy.json").read_text())


def run(overlay_path, **extra):
    sandbox = ROOT / ".sandbox-tmp"
    sandbox.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="overlay-health-tools-", dir=sandbox) as directory:
        env, violations = isolated_environment(Path(directory), os.environ["PATH"])
        env = {k: v for k, v in env.items() if not k.startswith("OMNILANE_AA_")}
        env["OMNILANE_HOME"] = str(Path(directory) / "omnilane")
        env.update(extra)
        if overlay_path is None:
            env.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
        else:
            env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(overlay_path)
        proc = subprocess.run([sys.executable, str(HEALTH), str(ROOT)],
                              capture_output=True, text=True, env=env)
        if violations.exists():
            raise AssertionError("unmocked network command")
    level, _, message = proc.stdout.strip().partition("\t")
    return proc.returncode, level, message


class OverlayHealthTests(unittest.TestCase):
    def setUp(self):
        sandbox = ROOT / ".sandbox-tmp"
        sandbox.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="overlay-health-", dir=sandbox)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.evidence = self.base / "fake-cli"
        self.evidence.write_bytes(b"pretend this is a vendor binary\n")
        self.digest = hashlib.sha256(self.evidence.read_bytes()).hexdigest()

    def overlay(self, **changes):
        entry = {"path": str(self.evidence), "sha256": self.digest}
        entry.update(changes.pop("evidence", {}))
        value = {
            "schema_version": 1,
            "snapshot_id": REGISTRY["snapshot"]["id"],
            "host": socket.gethostname(),
            "source": "test fixture",
            "evidence": [entry],
            "mappings": [],
        }
        value.update(changes)
        path = self.base / "overlay.json"
        path.write_text(json.dumps(value))
        return path

    def test_absent_configuration_warns_and_names_the_first_install_steps(self):
        rc, level, message = run(None)
        self.assertEqual(rc, 0)
        self.assertEqual(level, "WARN")
        self.assertIn("no overlay configured", message)
        self.assertIn("build_overlay.py", message)

    def test_a_human_operated_host_needs_no_overlay(self):
        rc, level, message = run(None, OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1")
        self.assertEqual((rc, level), (0, "PASS"))
        self.assertIn("model caller would be refused", message)

    def test_configured_but_missing_file_fails(self):
        rc, level, message = run(self.base / "not-here.json")
        self.assertEqual(level, "FAIL")
        self.assertIn("missing", message)

    def test_loadable_overlay_passes(self):
        rc, level, message = run(self.overlay())
        self.assertEqual(level, "PASS", message)
        self.assertIn("verified mappings", message)

    def test_untagged_drift_fails_and_names_the_file(self):
        """The 2026-09-09 incident: one drifted hash refused every dispatch."""
        overlay = self.overlay(evidence={"sha256": "0" * 64})
        rc, level, message = run(overlay)
        self.assertEqual(level, "FAIL", message)
        self.assertIn("every dispatch is refused", message)
        self.assertIn(str(self.evidence), message)

    def test_tagged_drift_warns_instead_of_failing(self):
        overlay = self.overlay(evidence={"sha256": "0" * 64, "vendor": "codex"})
        rc, level, message = run(overlay)
        self.assertEqual(level, "WARN", message)
        self.assertIn("codex", message)
        self.assertIn(str(self.evidence), message)

    def test_tagged_missing_file_warns(self):
        overlay = self.overlay(
            evidence={"path": str(self.base / "gone"), "vendor": "grok"})
        rc, level, message = run(overlay)
        self.assertEqual(level, "WARN", message)
        self.assertIn("grok", message)

    def test_unproven_configs_are_surfaced(self):
        overlay = self.overlay(unproven=[
            {"config_id": "claude/claude-fable-5-1", "verdict_reason": "quota"}])
        rc, level, message = run(overlay)
        self.assertEqual(level, "PASS", message)
        self.assertIn("unproven", message)

    def vendor_fixture(self):
        env, violations = isolated_environment(self.base / "tools", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists(), "unmocked network command"))
        env["OMNILANE_HOME"] = str(self.base / "omnilane")
        Path(env["OMNILANE_HOME"]).mkdir()
        self.evidence.write_text("#!/bin/sh\nprintf 'fake codex A\\n'\n")
        self.evidence.chmod(0o755)
        self.digest = hashlib.sha256(self.evidence.read_bytes()).hexdigest()
        self.local = Path(env["OMNILANE_HOME"]) / "local.sh"
        self.local.write_text(f"export CODEX_BIN='{self.evidence}'\n")
        return env, self.overlay(evidence={"vendor": "codex"})

    def doctor(self, env, overlay, *options):
        env = dict(env, OMNILANE_AA_TRANSPORT_OVERLAY=str(overlay))
        return subprocess.run(["/bin/bash", str(ROOT / "bin/omnilane"),
                               "doctor", *options], env=env, capture_output=True, text=True)

    def test_different_resolved_path_fails_and_names_both_paths(self):
        env, overlay = self.vendor_fixture()
        replacement = self.base / "replacement-cli"
        replacement.write_bytes(self.evidence.read_bytes())
        replacement.chmod(0o755)
        self.local.write_text(f"export CODEX_BIN='{replacement}'\n")
        rc, level, message = run(overlay, **env)
        self.assertEqual((rc, level), (0, "FAIL"), message)
        self.assertIn("codex", message)
        self.assertIn(f"runs {replacement}, overlay pins {self.evidence}", message)
        self.assertIn("omnilane resign", message)
        for options in ((), ("--strict",), ("--json",), ("--strict", "--json")):
            with self.subTest(options=options):
                result = self.doctor(env, overlay, *options)
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                if "--json" in options:
                    payload = json.loads(result.stdout)
                    report = next(r for r in payload["checks"] if r["check"] == "transport-overlay")
                    self.assertEqual(report["level"], "FAIL")
                    self.assertFalse(payload["ok"])
                    message = report["message"]
                else:
                    message = result.stdout
                    self.assertRegex(message, r"(?m)^FAIL\s+transport-overlay\s")
                self.assertIn(f"runs {replacement}, overlay pins {self.evidence}", message)
                self.assertIn("omnilane resign", message)

    def test_same_path_changed_content_keeps_vendor_warning(self):
        env, overlay = self.vendor_fixture()
        self.evidence.write_text("#!/bin/sh\nprintf 'fake codex updated\\n'\n")
        rc, level, message = run(overlay, **env)
        self.assertEqual((rc, level), (0, "WARN"), message)
        self.assertIn("stale vendor(s) codex degraded to unverified", message)
        self.assertNotIn("runs ", message)
        self.assertNotIn("overlay pins", message)

    def test_matching_executable_keeps_exact_pass_message(self):
        env, overlay = self.vendor_fixture()
        self.assertEqual(run(overlay, **env), (0, "PASS", "verified mappings: none"))

    def test_symlink_to_pinned_executable_is_not_a_path_mismatch(self):
        env, overlay = self.vendor_fixture()
        alias = self.base / "cli-alias"
        alias.symlink_to(self.evidence)
        self.local.write_text(f"export CODEX_BIN='{alias}'\n")
        self.assertEqual(run(overlay, **env), (0, "PASS", "verified mappings: none"))

    def test_path_mismatch_fails_even_when_pinned_evidence_is_stale(self):
        env, overlay = self.vendor_fixture()
        replacement = self.base / "replacement-cli"
        replacement.write_bytes(self.evidence.read_bytes())
        replacement.chmod(0o755)
        self.local.write_text(f"export CODEX_BIN='{replacement}'\n")
        self.evidence.write_text("#!/bin/sh\nprintf 'stale pinned codex\\n'\n")
        rc, level, message = run(overlay, **env)
        self.assertEqual((rc, level), (0, "FAIL"), message)
        self.assertIn(f"runs {replacement}, overlay pins {self.evidence}", message)

    def test_unresolvable_cli_is_not_a_path_mismatch(self):
        env, overlay = self.vendor_fixture()
        self.local.write_text("export CODEX_BIN='not-installed-c2'\n")
        self.assertEqual(run(overlay, **env), (0, "PASS", "verified mappings: none"))

    def test_no_overlay_keeps_exact_warning_and_human_pass(self):
        env, _ = self.vendor_fixture()
        env.pop("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN", None)
        self.assertEqual(run(None, **env), run(None))
        self.assertEqual(run(None, **dict(env, OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1")),
                         run(None, OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1"))

    def test_runner_only_drift_keeps_vendor_warning(self):
        env, overlay = self.vendor_fixture()
        value = json.loads(overlay.read_text())
        runner = ROOT / "scripts/runners/codex.sh"
        value["evidence"].append({"vendor": "codex", "path": str(runner), "sha256": "0" * 64})
        overlay.write_text(json.dumps(value))
        rc, level, message = run(overlay, **env)
        self.assertEqual((rc, level), (0, "WARN"), message)
        self.assertIn("stale vendor(s) codex degraded to unverified", message)
        self.assertIn(str(runner), message)
        self.assertNotIn("runs ", message)
        self.assertNotIn("overlay pins", message)


if __name__ == "__main__":
    unittest.main()
