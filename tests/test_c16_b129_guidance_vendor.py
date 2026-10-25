import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from verification_status import transport_guidance


class GuidanceVendorTests(unittest.TestCase):
    def test_unknown_vendors_only_suggest_generic_check(self):
        vendors = ("CODEX", "Codex", "CLAUDE", "Gemini", "GROK", "", None,
                   "unknown", "codex --approve", "claude --retry-failed")
        for vendor in vendors:
            for failed in (False, True):
                for selection in ({"config_id": "target"}, {"candidates": ("target",)}):
                    with self.subTest(vendor=vendor, failed=failed, selection=selection):
                        result = transport_guidance(
                            vendor, **selection,
                            environment_failures=("target",) if failed else ())
                        self.assertEqual(result["next_command"], "omnilane resign --check")
                        self.assertFalse(result["requires_gui_session"])
                        for field in ("message", "message_zh_tw"):
                            self.assertIn("`omnilane resign --check`", result[field])

    def test_known_vendors_preserve_original_commands(self):
        for vendor in ("claude", "codex", "gemini", "grok"):
            for failed in (False, True):
                for platform in ("darwin", "linux"):
                    for selection in ({"config_id": "target"}, {"candidates": ("target",)}):
                        with self.subTest(vendor=vendor, failed=failed, platform=platform,
                                          selection=selection):
                            with patch("verification_status.sys.platform", platform):
                                result = transport_guidance(
                                    vendor, **selection,
                                    environment_failures=("target",) if failed else ())
                            expected = ("omnilane resign" + (" --retry-failed" if failed else "")
                                        + " --vendor " + vendor)
                            self.assertEqual(result["next_command"], expected)
                            self.assertEqual(result["requires_gui_session"],
                                             platform == "darwin" and vendor != "codex")
                            for field in ("message", "message_zh_tw"):
                                self.assertIn(f"`{expected}`", result[field])


if __name__ == "__main__":
    unittest.main()
