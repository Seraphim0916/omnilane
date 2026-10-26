import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from verification_status import transport_guidance


class GuidancePlatformTests(unittest.TestCase):
    def test_platform_vendor_and_failure_matrix(self):
        original_platform = sys.platform
        for platform in ("darwin", "linux", "win32"):
            for vendor in ("claude", "codex", "gemini", "grok"):
                for failed in (False, True):
                    for selection in ({"config_id": "target"}, {"candidates": ("target",)}):
                        with self.subTest(platform=platform, vendor=vendor,
                                          failed=failed, selection=selection):
                            with patch("verification_status.sys.platform", platform):
                                result = transport_guidance(
                                    vendor, **selection,
                                    environment_failures=("target",) if failed else ())
                            self.assertEqual(sys.platform, original_platform)
                            gui = platform == "darwin" and vendor in ("claude", "gemini", "grok")
                            self.assertIs(result["requires_gui_session"], gui)
                            self.assertEqual("desktop terminal" in result["message"], gui)
                            self.assertEqual("SSH alone" in result["message"], gui)
                            self.assertEqual("圖形桌面終端機" in result["message_zh_tw"], gui)
                            if not gui:
                                self.assertIn("Use a terminal with this CLI signed in.", result["message"])
                                self.assertNotIn("這台 Mac", result["message_zh_tw"])
                            command = "omnilane resign" + (" --retry-failed" if failed else "")
                            command += " --vendor " + vendor
                            self.assertEqual(result["next_command"], command)
                            for field in ("message", "message_zh_tw"):
                                self.assertIn(command, result[field])
                            self.assertEqual("environment reason" in result["message"], failed)
                            self.assertEqual("環境原因失敗" in result["message_zh_tw"], failed)


if __name__ == "__main__":
    unittest.main()
