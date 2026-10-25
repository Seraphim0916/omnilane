"""Direct checks of diagnostic data and recovery hints without provider CLIs."""
import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import overlay_diagnostics as diagnostics
import probe_sweep


class OverlayDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        sandbox = ROOT / ".sandbox-tmp"
        sandbox.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="overlay-diagnostics-", dir=sandbox)
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.registry = {"scored_configs": [{
            "id": "codex/test-model", "vendor": "codex", "model": "test-model", "effort": "high",
        }]}

    def write_json(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def rows(self, items, **fields):
        return diagnostics.unproven_rows(dict(unproven=items, **fields), self.registry)

    def test_empty_overlay_has_no_unproven_rows(self):
        self.assertEqual(diagnostics.unproven_rows({}, self.registry), [])

    def test_read_object_preserves_objects(self):
        value = {"unproven": [], "nested": {"value": 7}}
        self.assertEqual(diagnostics.read_object(self.write_json("object.json", value)), value)

    def test_read_object_returns_empty_for_non_object_json(self):
        for value in ([], [1], None, True, 7, "text"):
            with self.subTest(value=value):
                path = self.write_json("not-object.json", value)
                self.assertEqual(diagnostics.read_object(path), {})

    def test_read_object_returns_empty_for_missing_invalid_or_unreadable_file(self):
        invalid = self.base / "invalid.json"
        invalid.write_text("{broken", encoding="utf-8")
        for path in (self.base / "missing.json", invalid, self.base):
            with self.subTest(path=path):
                self.assertEqual(diagnostics.read_object(path), {})

    def test_missing_row_fields_have_explicit_defaults(self):
        row, = self.rows([{}])
        self.assertEqual(row, {
            "config_id": "unknown", "vendor": "unknown",
            "requested_model": None, "requested_effort": None,
            "observed_model": None, "observed_effort": None,
            "failure_class": "conclusive", "reason": "unknown", "last_probe_at": None,
            "evidence_paths": [],
            "next_step": ("will not retry automatically; inspect the original request, CLI model/effort "
                          "selectors and provider model mapping before arranging fresh verification"),
        })
        message = diagnostics.row_message(row)
        self.assertIn("requested model=not recorded", message)
        self.assertIn("evidence paths=not recorded", message)

    def test_failure_classes_and_messages_for_each_condition(self):
        cases = (
            ("timeout", "environment", "--retry-failed --vendor codex"),
            ("not probed on this host", "unprobed", "omnilane resign --vendor codex"),
            ("vendor CLI is not installed", "unprobed", "omnilane resign --vendor codex"),
            ("model-mismatch", "conclusive", "will not retry automatically"),
        )
        for reason, failure_class, next_step in cases:
            with self.subTest(reason=reason):
                row, = self.rows([{"config_id": "codex/test-model", "verdict_reason": reason}])
                self.assertEqual(row["failure_class"], failure_class)
                self.assertIn(next_step, row["next_step"])
                message = diagnostics.row_message(row)
                self.assertIn(f"{failure_class}: {reason}", message)
                self.assertIn(row["next_step"], message)

    def test_manifest_descriptor_fills_observations_and_sorts_paths(self):
        descriptor = self.write_json("descriptor.json", {
            "observed_model": "observed", "observed_effort": "low", "probed_at": "descriptor-time",
            "response": "private response must not appear",
        })
        trace = self.base / "trace.txt"
        manifest = self.write_json("probe-manifest.json", {"failed_probe_runs": {
            "codex/test-model": {"json": {"path": str(descriptor)},
                                 "stderr": {"path": str(trace)}, "duplicate": {"path": str(trace)}},
        }})
        row, = self.rows([{"config_id": "codex/test-model", "verdict_reason": "timeout"}],
                         evidence=[{"path": str(manifest)}])
        self.assertEqual((row["requested_model"], row["requested_effort"]), ("test-model", "high"))
        self.assertEqual((row["observed_model"], row["observed_effort"], row["last_probe_at"]),
                         ("observed", "low", "descriptor-time"))
        self.assertEqual(row["evidence_paths"], sorted([str(descriptor), str(trace)]))
        message = diagnostics.row_message(row)
        for value in ("observed", "low", "descriptor-time", str(descriptor), str(trace)):
            self.assertIn(value, message)
        self.assertNotIn("private response", message)
        self.assertNotIn("response", row)

    def test_overlay_observations_take_precedence_over_descriptor(self):
        descriptor = self.write_json("descriptor.json", {
            "observed_model": "old-model", "observed_effort": "low", "probed_at": "old-time",
        })
        manifest = self.write_json("probe-manifest.json", {"failed_probe_runs": {
            "codex/test-model": {"json": {"path": str(descriptor)}},
        }})
        row, = self.rows([{
            "config_id": "codex/test-model", "observed_model": "new-model",
            "observed_effort": "high", "probed_at": "new-time", "last_failed_at": "fallback-time",
        }], evidence=[{"path": str(manifest)}])
        self.assertEqual((row["observed_model"], row["observed_effort"], row["last_probe_at"]),
                         ("new-model", "high", "new-time"))

    def test_vendor_and_last_failed_time_fallback_without_registry_entry(self):
        row, = self.rows([{"config_id": "other/model", "last_failed_at": "fallback-time"}])
        self.assertEqual(row["vendor"], "other")
        self.assertEqual(row["last_probe_at"], "fallback-time")
        self.assertIsNone(row["requested_model"])

    def test_tagged_and_non_manifest_evidence_are_ignored(self):
        descriptor = self.write_json("descriptor.json", {"observed_model": "ignored"})
        payload = {"failed_probe_runs": {"other/model": {"json": {"path": str(descriptor)}}}}
        manifest = self.write_json("probe-manifest.json", payload)
        other = self.write_json("other.json", payload)
        row, = self.rows([{"config_id": "other/model"}], evidence=[
            {"path": str(manifest), "vendor": "other"}, {"path": str(other)},
        ])
        self.assertIsNone(row["observed_model"])
        self.assertEqual(row["evidence_paths"], [])

    def test_legacy_manifest_uses_planned_descriptor_path(self):
        manifest = self.write_json("probe-manifest.json", {})
        evidence = self.base / "evidence"
        evidence.mkdir()
        descriptor = evidence / "legacy-probe.json"
        descriptor.write_text(json.dumps({"observed_model": "legacy"}), encoding="utf-8")
        with patch.object(probe_sweep, "plan", return_value=[{
            "config_id": "codex/test-model", "name": "legacy-probe",
        }]) as plan:
            row, = self.rows([{"config_id": "codex/test-model"}], evidence=[{"path": str(manifest)}])
        plan.assert_called_once_with("codex")
        self.assertEqual(row["observed_model"], "legacy")
        self.assertEqual(row["evidence_paths"], [str(descriptor)])

    def test_recovery_guidance_includes_paths_and_shell_quotes_repo(self):
        repo = self.base / "repo with spaces"
        manifest = self.base / "probe-manifest.json"
        overlay = self.write_json("overlay.json", {"evidence": [
            {"path": str(manifest)}, {"path": "other.json"}, {}, None, {"path": 42},
        ]})
        before = overlay.read_bytes()
        guidance = diagnostics.recovery_guidance(repo, overlay)
        self.assertIn(str(overlay), guidance)
        self.assertIn(str(manifest), guidance)
        for name in ("probe_sweep.py", "build_overlay.py"):
            self.assertIn(shlex.quote(str(repo / "scripts" / "lib" / name)), guidance)
        self.assertNotIn("other.json", guidance)
        self.assertEqual(overlay.read_bytes(), before)
        self.assertFalse(repo.exists())

    def test_recovery_guidance_tolerates_missing_or_non_list_evidence(self):
        for value in ({}, {"evidence": None}, {"evidence": "not a list"}):
            with self.subTest(value=value):
                overlay = self.write_json("overlay.json", value)
                guidance = diagnostics.recovery_guidance(self.base, overlay)
                self.assertIn(str(overlay), guidance)
                self.assertIn("manifest paths: not recorded", guidance)
        missing = self.base / "missing.json"
        self.assertIn(str(missing), diagnostics.recovery_guidance(self.base, missing))
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
