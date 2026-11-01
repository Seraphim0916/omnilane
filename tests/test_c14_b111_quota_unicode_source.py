import offline_env  # Activate suite isolation for direct file execution.
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight


class QuotaUnicodeSourceTests(unittest.TestCase):
    def snapshot_for(self, source):
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        value = dict(vendor="codex", window="weekly", used_percent=50,
                     captured_at=now.isoformat(),
                     resets_at=(now + dt.timedelta(days=1)).isoformat(), source=source)
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {
                "OMNILANE_QUOTA_DIR": directory,
                "OMNILANE_QUOTA_THRESHOLD_PERCENT": "90",
                "OMNILANE_QUOTA_MAX_AGE_SECONDS": "1800",
            }), patch.object(quota_preflight, "read_snapshot",
                             return_value=json.dumps(value, ensure_ascii=False)) as reader:
                result = quota_preflight.snapshot("codex", directory, now=now)
                reader.assert_called_once_with(Path(directory))
                return result

    def test_unicode_length_is_characters_not_bytes(self):
        for character in ("菜", "\U0001f331"):
            for count in (1, 128, 129):
                with self.subTest(character=character, count=count):
                    source = character * count
                    result = self.snapshot_for(source)
                    expected = "weekly-snapshot" if count <= 128 else "invalid-snapshot"
                    self.assertEqual(result["reason"], expected)
                    self.assertEqual(result["source"], source if count <= 128 else None)
                    self.assertEqual(result["status"], "fresh" if count <= 128 else "unknown")
                    print(f"source U+{ord(character):04X} chars={count} "
                          f"bytes={len(source.encode('utf-8'))}: {result['reason']}")

    def test_nonprintable_unicode_is_rejected_below_length_limit(self):
        for character in ("\u2028", "\u200b"):
            with self.subTest(character=character):
                result = self.snapshot_for("菜" + character + "苗")
                self.assertEqual(result["reason"], "invalid-snapshot")
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["source"])
                print(f"source U+{ord(character):04X}: {result['reason']}")


if __name__ == "__main__":
    unittest.main()
