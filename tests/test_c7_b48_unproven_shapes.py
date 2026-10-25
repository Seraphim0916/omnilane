import offline_env  # Activate suite isolation for direct file execution.
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
from overlay_diagnostics import unproven_rows
import test_overlay_health as health_fixture


BAD_VALUES = (None, {}, "bad", [None], [[]], ["bad"], [3],
              [{"config_id": None}], [{"config_id": []}],
              [{"config_id": {}}], [{"config_id": 3}], [{"config_id": True}])


class UnprovenShapeTests(unittest.TestCase):
    def test_health_cli_uses_suite_default_temporary_directory(self):
        temporary_directory = tempfile.TemporaryDirectory

        def suite_directory(*args, **kwargs):
            self.assertIsNone(kwargs.get("dir"), "Use the suite's default temporary directory")
            return temporary_directory(*args, **kwargs)

        with mock.patch(__name__ + ".tempfile", wraps=tempfile) as temp_module:
            temp_module.TemporaryDirectory.side_effect = suite_directory
            self.test_health_cli_reports_fail_without_traceback()
            temp_module.TemporaryDirectory.assert_called_once_with()

    def test_invalid_shapes_raise_controlled_validation_error(self):
        for value in BAD_VALUES:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "unproven"):
                    unproven_rows({"unproven": value}, {"scored_configs": []})

    def test_valid_rows_and_absent_fields_preserve_existing_defaults(self):
        registry = {"scored_configs": []}
        self.assertEqual(unproven_rows({}, registry), [])
        rows = unproven_rows({"unproven": [{}, {"config_id": "codex/model"}]}, registry)
        self.assertEqual([row["config_id"] for row in rows], ["unknown", "codex/model"])

    def test_health_cli_reports_fail_without_traceback(self):
        fixture = health_fixture.OverlayHealthTests("test_loadable_overlay_passes")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with tempfile.TemporaryDirectory() as directory:
            env, violations = offline_env.isolated_environment(Path(directory), os.environ["PATH"])
            for value in BAD_VALUES:
                with self.subTest(value=value):
                    path = fixture.overlay(unproven=value)
                    env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(path)
                    result = subprocess.run(
                        [sys.executable, "-B", str(health_fixture.HEALTH), str(ROOT)],
                        env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(result.stdout.startswith("FAIL\t"), result.stdout)
                    self.assertIn("unproven", result.stdout)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(violations.exists())


if __name__ == "__main__":
    unittest.main()
