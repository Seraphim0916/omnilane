import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


spec = importlib.util.spec_from_file_location(
    "c22_b181_quota", Path(__file__).resolve().parents[1] / "scripts/lib/quota_preflight.py")
quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quota)


class QuotaFallbackVoteTests(unittest.TestCase):
    def test_empty_snapshot_matches_complete_participants_only(self):
        cases = (
            ("codex,claude", "codex"), ("claude,codex", "codex"),
            ("claude,codex,grok", "codex"), ("codex", "codex"),
            ("notcodex,claude", "vote"), ("codexish,claude", "vote"),
            ("claude,mycodex", "vote"), ("Codex,claude", "vote"),
            ("claude,grok", "vote"), ("", "vote"),
        )
        for participants, vendor in cases:
            with self.subTest(participants=participants):
                original = ["vote", participants, "high"]
                final = ["claude", "fixture-final-model", "medium"]
                result = quota.decision(SimpleNamespace(
                    snapshot="", lane="fixture-vote", action="preserve",
                    original=original, final=final))
                self.assertEqual(result["snapshot"], quota.unknown_snapshot(
                    vendor, "snapshot-read-failed"))
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["exclusion_reason"])
                expected = dict(vendor="vote", model=participants, effort="high")
                self.assertEqual(result["original"], expected)
                self.assertEqual(result["final"], dict(
                    vendor="claude", model="fixture-final-model", effort="medium"))
                self.assertEqual(result["action"], "preserve")


if __name__ == "__main__":
    unittest.main()
