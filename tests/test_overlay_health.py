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

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "scripts" / "lib" / "overlay_health.py"
REGISTRY = json.loads((ROOT / "config" / "aa-model-policy.json").read_text())


def run(overlay_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_AA_")}
    if overlay_path is None:
        env.pop("OMNILANE_AA_TRANSPORT_OVERLAY", None)
    else:
        env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(overlay_path)
    proc = subprocess.run([sys.executable, str(HEALTH), str(ROOT)],
                          capture_output=True, text=True, env=env)
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

    def test_absent_configuration_is_not_a_failure(self):
        rc, level, message = run(None)
        self.assertEqual(rc, 0)
        self.assertEqual(level, "PASS")
        self.assertIn("no overlay configured", message)

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


if __name__ == "__main__":
    unittest.main()
