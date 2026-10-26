import offline_env  # Activate suite isolation for direct file execution.
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class EarlyRefusalTests(unittest.TestCase):
    def test_untrusted_signatures_never_read_policy_or_resolve_path(self):
        cases = [(dict(kind="invalid"), "invalid"),
                 (dict(kind="unsigned"), "unsigned"),
                 (dict(kind="unverifiable"), "unverifiable"),
                 (dict(kind="unknown"), "unverifiable"),
                 (dict(kind="developer-id", valid=False), "invalid"),
                 (dict(kind="adhoc", valid=False), "invalid"),
                 (None, "unverifiable"),
                 (dict(kind="developer-id", valid=True, signer="bad",
                       identifier="vendor.1.2.3"), "unverifiable"),
                 (dict(kind="developer-id", valid=True, signer="TEST000001",
                       identifier=""), "unverifiable"),
                 (dict(kind="adhoc", valid=True, signer="wrong"), "unverifiable")]
        for current, reason in cases:
            with self.subTest(current=current):
                with patch.object(op, "channels_for", side_effect=AssertionError("policy read")) as policy, \
                        patch.object(op, "install_family", side_effect=AssertionError("path read")) as path:
                    self.assertEqual(op.gate("claude", None, None, current, object()),
                                     (False, reason))
                    policy.assert_not_called()
                    path.assert_not_called()

    def test_trusted_signature_does_read_policy_and_resolve_path(self):
        current = dict(kind="developer-id", valid=True, signer="TEST000001",
                       identifier="vendor.1.2.3")
        location = "/fixture/tool"
        channels = [dict(kind="developer-id", install_family=location,
                         team_id="TEST000001", identifier_family="vendor.<version>")]
        with patch.object(op, "channels_for", return_value=channels) as policy, \
                patch.object(op, "install_family", return_value=location) as path:
            self.assertEqual(op.gate("claude", None, None, current, location),
                             (True, "origin-approved"))
            policy.assert_called_once_with("claude", None, None, None)
            path.assert_called_once_with(location)


if __name__ == "__main__":
    unittest.main()
