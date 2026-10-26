import offline_env  # Activate suite isolation for direct file execution.

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as qp


class VendorShortCircuitTests(unittest.TestCase):
    def test_unsupported_vendors_skip_configuration_paths_and_reads(self):
        for vendor in ("claude", "gemini", "grok", "unknown-vendor", "", "Codex", "CODEX", "coDeX"):
            for settings in (
                {"OMNILANE_QUOTA_THRESHOLD_PERCENT": "not-a-number"},
                {"OMNILANE_QUOTA_MAX_AGE_SECONDS": "not-a-number"},
                {"OMNILANE_QUOTA_DIR": "relative/path"},
            ):
                with self.subTest(vendor=vendor, settings=settings), \
                        patch.dict(os.environ, settings, clear=True), \
                        patch.object(os.environ, "get", wraps=os.environ.get) as config, \
                        patch.object(qp, "Path") as path, \
                        patch.object(qp, "read_snapshot") as read:
                    result = qp.snapshot(vendor, "unused-home")
                    self.assertEqual(result["vendor"], vendor)
                    self.assertEqual(result["status"], "unknown")
                    self.assertEqual(result["reason"], "unsupported-vendor")
                    self.assertFalse(result["near_limit"])
                    config.assert_not_called()
                    path.assert_not_called()
                    read.assert_not_called()

    def test_codex_rejects_invalid_configuration(self):
        with patch.dict(os.environ, {"OMNILANE_QUOTA_THRESHOLD_PERCENT": "bad"}, clear=True), \
                patch.object(qp, "Path") as path, patch.object(qp, "read_snapshot") as read:
            result = qp.snapshot("codex", "unused-home")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "invalid-configuration")
        path.assert_not_called()
        read.assert_not_called()

    def test_codex_valid_configuration_constructs_path_and_reads(self):
        directory = Path(tempfile.gettempdir()) / "quota-fixture"
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        raw = json.dumps(dict(vendor="codex", window="weekly", used_percent=95,
                              captured_at=now.isoformat(),
                              resets_at=(now + dt.timedelta(days=1)).isoformat(),
                              source="test-fixture"))
        with patch.dict(os.environ, {"OMNILANE_QUOTA_DIR": str(directory)}, clear=True), \
                patch.object(qp, "Path", wraps=Path) as path, \
                patch.object(qp, "read_snapshot", return_value=raw) as read:
            result = qp.snapshot("codex", "unused-home", now=now)
        path.assert_called_once_with(str(directory))
        read.assert_called_once_with(directory)
        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["reason"], "weekly-snapshot")
        self.assertTrue(result["near_limit"])


if __name__ == "__main__":
    unittest.main()
