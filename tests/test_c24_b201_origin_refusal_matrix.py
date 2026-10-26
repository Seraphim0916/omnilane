import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from verification_status import origin_guidance


class OriginRefusalMatrixTests(unittest.TestCase):
    KINDS = ("developer-id", "adhoc", "script", "no-platform-signature")

    def test_unrepairable_reasons_override_every_kind(self):
        for reason in ("invalid", "unsigned", "unverifiable", "policy-invalid"):
            for kind in self.KINDS:
                with self.subTest(reason=reason, kind=kind):
                    result = origin_guidance("claude", reason, {"kind": kind})
                    self.assertIsNone(result["next_command"])
                    self.assertIn("No approval command repairs this", result["message"])
                    self.assertIn("核准指令無法修好這個問題", result["message_zh_tw"])
                    for field in ("message", "message_zh_tw"):
                        for flag in ("--approve", "--trust-origin", "--trust-adhoc", "--trust-location"):
                            self.assertNotIn(flag, result[field])

    def test_approvable_reason_preserves_kind_specific_command(self):
        expected_flags = {
            "developer-id": "--trust-origin", "adhoc": "--trust-adhoc",
            "script": "--approve", "no-platform-signature": "--approve",
        }
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                result = origin_guidance("claude", "origin-unapproved", {"kind": kind})
                command = "omnilane resign " + expected_flags[kind] + " claude"
                self.assertEqual(result["next_command"], command)
                for field in ("message", "message_zh_tw"):
                    self.assertIn(command, result[field])
                self.assertNotIn("No approval command repairs this", result["message"])


if __name__ == "__main__":
    unittest.main()
