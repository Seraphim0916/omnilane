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
import quota_preflight as quota


class QuotaConfigNoIOTests(unittest.TestCase):
    def test_invalid_configuration_never_reads_snapshot(self):
        cases = [
            ("OMNILANE_QUOTA_THRESHOLD_PERCENT", value)
            for value in ("-1", "101", "nan", "inf", "invalid")
        ] + [
            ("OMNILANE_QUOTA_MAX_AGE_SECONDS", value)
            for value in ("-1", "nan", "inf", "invalid")
        ] + [("OMNILANE_QUOTA_DIR", "relative/quota")]
        with tempfile.TemporaryDirectory() as home:
            valid = {"OMNILANE_QUOTA_THRESHOLD_PERCENT": "90",
                     "OMNILANE_QUOTA_MAX_AGE_SECONDS": "1800",
                     "OMNILANE_QUOTA_DIR": str(Path(home) / "quota")}
            for key, value in cases:
                with self.subTest(key=key, value=value), \
                        patch.dict(os.environ, dict(valid, **{key: value})), \
                        patch.object(quota, "read_snapshot") as reader:
                    result = quota.snapshot("codex", home)
                    self.assertEqual(result["reason"], "invalid-configuration")
                    reader.assert_not_called()

    def test_valid_configuration_reads_once(self):
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        raw = json.dumps(dict(vendor="codex", window="weekly", used_percent=20,
                              captured_at=now.isoformat(),
                              resets_at=(now + dt.timedelta(days=1)).isoformat(),
                              source="test-fixture"))
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home) / "quota"
            for threshold, max_age in (("90", "1800"), ("0", "0"), ("100", "1800")):
                with self.subTest(threshold=threshold, max_age=max_age), \
                        patch.dict(os.environ, {
                            "OMNILANE_QUOTA_THRESHOLD_PERCENT": threshold,
                            "OMNILANE_QUOTA_MAX_AGE_SECONDS": max_age,
                            "OMNILANE_QUOTA_DIR": str(directory)}), \
                        patch.object(quota, "read_snapshot", return_value=raw) as reader:
                    result = quota.snapshot("codex", home, now=now)
                    self.assertEqual(result["reason"], "weekly-snapshot")
                    self.assertEqual(result["status"], "fresh")
                    reader.assert_called_once_with(directory)


if __name__ == "__main__":
    unittest.main()
