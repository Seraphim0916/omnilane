"""In-process coverage for scripts/lib/build_overlay.py main() edge cases.

Every build writes only under a temporary --root. No vendor CLI is resolved:
core_evidence() and cli_provenance.facts() are replaced by fixtures.
"""
import offline_env  # Activate suite isolation for direct file execution.
import home_isolation  # noqa: F401

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import build_overlay
import transport_compat

GROK = "grok/grok-4-7"
CODEX = "codex/gpt-6-sol-high"
UNSCORED = "claude/withdrawn-row"


def passthrough(row, selector, files, descriptor):
    return descriptor


class BuildOverlayMainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cov3-overlay-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "sweep"
        (self.root / "evidence").mkdir(parents=True)
        self.proven = {
            GROK: build_overlay.PROVEN[GROK],
            CODEX: build_overlay.PROVEN[CODEX],
            UNSCORED: ("model_and_effort", "withdrawn", "cl-withdrawn"),
        }
        patcher = mock.patch.object(build_overlay, "PROVEN", self.proven)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(build_overlay, "core_evidence", return_value=[])
        self.core_evidence = patcher.start()
        self.addCleanup(patcher.stop)

    def evidence(self, cid, descriptor, **streams):
        name = self.proven[cid][2]
        path = self.root / "evidence" / f"{name}.json"
        path.write_text(json.dumps(descriptor), encoding="utf-8")
        for suffix, text in streams.items():
            (self.root / "evidence" / f"{name}.{suffix}").write_text(text, encoding="utf-8")
        return path

    def build(self, checked=passthrough):
        out = io.StringIO()
        with mock.patch.object(transport_compat, "checked_descriptor", checked), \
                contextlib.redirect_stdout(out):
            build_overlay.main(["--root", str(self.root), "--source", "cov3 fixture"])
        overlay = json.loads((self.root / "transport-contracts.local.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.root / "probe-manifest.json").read_text(encoding="utf-8"))
        return overlay, manifest, out.getvalue()

    def unproven(self, overlay):
        return {entry["config_id"]: entry for entry in overlay["unproven"]}

    def test_nothing_probed_still_writes_an_empty_overlay(self):
        overlay, manifest, out = self.build()
        self.assertEqual(overlay["mappings"], [])
        self.assertEqual(overlay["source"], "cov3 fixture")
        self.assertEqual(overlay["compatibility_version"], transport_compat.VERSION)
        self.assertEqual(set(self.unproven(overlay)), set(self.proven))
        self.assertTrue(all(e["verdict_reason"] == "not probed on this host" for e in overlay["unproven"]))
        self.assertEqual(manifest, {"probe_runs": {}, "failed_probe_runs": {}, "failure_metadata_version": 1})
        manifest_anchor = overlay["evidence"][-1]
        self.assertEqual(manifest_anchor["path"], str(self.root / "probe-manifest.json"))
        self.assertEqual(manifest_anchor["sha256"],
                         hashlib.sha256((self.root / "probe-manifest.json").read_bytes()).hexdigest())
        self.assertIn("with 0 mappings and 1 evidence anchors", out)
        self.assertNotIn("left out", out)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["evidence", "probe-manifest.json", "transport-contracts.local.json"])

    def test_non_object_descriptor_aborts_the_build(self):
        self.evidence(GROK, ["not", "an", "object"])
        with self.assertRaises(SystemExit) as raised:
            self.build()
        self.assertEqual(str(raised.exception), f"invalid probe descriptor for {GROK}: gk-grok-4_7-xhigh")
        self.assertFalse((self.root / "transport-contracts.local.json").exists())

    def test_unknown_evidence_tier_aborts_the_build(self):
        self.evidence(GROK, {"verdict": "pass", "evidence_tier": "vibes"})
        with self.assertRaises(SystemExit) as raised:
            self.build()
        self.assertEqual(str(raised.exception), f"unknown evidence tier for {GROK}: vibes")

    def test_failed_probe_is_kept_as_integrity_protected_negative_proof(self):
        path = self.evidence(GROK, {"verdict": "fail", "verdict_reason": "quota", "observed_model": "grok-4.6",
                                    "probed_at": "2026-10-01T00:00:00+00:00",
                                    "last_failed_at": "2026-10-02T00:00:00+00:00"},
                             stdout="out", stderr="err")
        self.evidence(CODEX, {"verdict": "error"})
        overlay, manifest, _ = self.build(transport_compat.checked_descriptor)
        failed = self.unproven(overlay)
        self.assertEqual(failed[GROK], {"config_id": GROK, "verdict_reason": "quota",
                                        "observed_model": "grok-4.6",
                                        "probed_at": "2026-10-01T00:00:00+00:00",
                                        "last_failed_at": "2026-10-02T00:00:00+00:00"})
        self.assertEqual(failed[CODEX]["verdict_reason"], "verdict: error")
        self.assertNotIn("last_failed_at", failed[CODEX])
        self.assertRegex(failed[CODEX]["probed_at"], r"^\d{4}-\d\d-\d\dT.*\+00:00$")
        entry = manifest["failed_probe_runs"][GROK]
        self.assertEqual(sorted(entry), ["json", "stderr", "stdout"])
        self.assertEqual(entry["json"], {"path": str(path),
                                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.assertEqual(manifest["probe_runs"], {})
        self.assertEqual(overlay["mappings"], [])

    def test_passing_probe_without_an_installed_cli_maps_nothing(self):
        self.evidence(GROK, {"verdict": "pass"})
        overlay, manifest, out = self.build()
        self.assertIn(GROK, manifest["probe_runs"])
        self.assertEqual(self.unproven(overlay)[GROK]["verdict_reason"], "vendor CLI is not installed")
        self.assertIn("with 0 mappings", out)

    def test_unscored_rows_are_left_out_and_reported(self):
        self.evidence(UNSCORED, {"verdict": "pass", "evidence_tier": "client-echo"})
        overlay, manifest, out = self.build()
        self.assertIn(UNSCORED, manifest["probe_runs"])
        self.assertNotIn(UNSCORED, self.unproven(overlay))
        self.assertIn("  left out, not scored by the registry: " + UNSCORED, out)

    def test_anchored_passing_probe_becomes_a_mapping(self):
        cli = self.root / "grok-binary"
        cli.write_text("binary", encoding="utf-8")
        runner = build_overlay.REPO / "scripts/runners" / build_overlay.RUNNERS["grok"]
        self.core_evidence.return_value = [(cli, "grok"), (runner, "grok")]
        self.evidence(GROK, {"verdict": "pass", "evidence_tier": "billed-model",
                             "probed_at": "2026-10-03T00:00:00+00:00"})
        with mock.patch.object(build_overlay.cli_provenance, "facts",
                               return_value={"kind": "script"}) as facts:
            overlay, _, out = self.build()
        facts.assert_called_once_with(cli)
        [mapping] = overlay["mappings"]
        row = build_overlay.ROWS[GROK]
        self.assertEqual(mapping["config_id"], GROK)
        self.assertEqual(mapping["identity"], {key: row[key] for key in build_overlay.IDENTITY_FIELDS})
        self.assertEqual((mapping["runtime_model"], mapping["runtime_effort"]), ("grok-4.7", row["effort"]))
        self.assertEqual(mapping["cli_flag"], "--reasoning-effort")
        self.assertEqual(mapping["evidence_tier"], "billed-model")
        self.assertEqual(mapping["verified_at"], "2026-10-03T00:00:00+00:00")
        self.assertEqual(mapping["source_snapshot_id"], build_overlay.REGISTRY["snapshot"]["id"])
        anchors = {Path(e["path"]).name: e for e in overlay["evidence"]}
        self.assertEqual(anchors["grok-binary"]["codesign"], {"kind": "script"})
        self.assertNotIn("codesign", anchors[runner.name])
        self.assertIn("evidence tiers: billed-model 1", out)

    def test_missing_tier_defaults_to_selector_only(self):
        cli = self.root / "codex-binary"
        cli.write_text("binary", encoding="utf-8")
        self.core_evidence.return_value = [(cli, "codex")]
        self.evidence(CODEX, {"verdict": "pass"})
        with mock.patch.object(build_overlay.cli_provenance, "facts", return_value={}):
            overlay, _, _ = self.build()
        [mapping] = overlay["mappings"]
        self.assertEqual(mapping["evidence_tier"], "selector-only")
        self.assertNotIn("cli_flag", mapping)


class HelperTests(unittest.TestCase):
    def test_unscored_proven_uses_given_rows(self):
        runs = {cid: {} for cid in list(build_overlay.PROVEN)[:3]}
        self.assertEqual(build_overlay.unscored_proven(runs, rows={}), sorted(runs))
        self.assertEqual(build_overlay.unscored_proven(runs, rows=build_overlay.ROWS), [])
        self.assertEqual(build_overlay.unscored_proven({"unknown/row": {}}, rows={}), [])

    def test_sha256_streams_multiple_blocks(self):
        with tempfile.TemporaryDirectory(prefix="cov3-overlay-sha-") as directory:
            path = Path(directory) / "blob"
            data = os.urandom(16) * ((1 << 20) // 16 + 3)
            path.write_bytes(data)
            self.assertEqual(build_overlay.sha256(path), hashlib.sha256(data).hexdigest())

    def test_cli_path_refuses_an_unresolvable_cli(self):
        with mock.patch.object(build_overlay, "cli_command", return_value="no-such-cli"), \
                mock.patch.object(build_overlay.shutil, "which", return_value=None):
            with self.assertRaisesRegex(SystemExit, "cannot resolve the grok CLI"):
                build_overlay.cli_path("grok")

    def test_core_evidence_skips_vendors_that_are_not_installed(self):
        with mock.patch.object(build_overlay, "cli_command", side_effect=lambda name: name), \
                mock.patch.object(build_overlay.shutil, "which", return_value=None):
            self.assertEqual(build_overlay.core_evidence(), [])


if __name__ == "__main__":
    unittest.main()
