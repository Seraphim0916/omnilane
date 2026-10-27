import offline_env  # Activate suite isolation for direct file execution.
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class GateErrorsTests(unittest.TestCase):
    def test_policy_and_path_errors_deny_otherwise_approved_origin(self):
        location = "/fixture/bin/tool"
        current = dict(kind="adhoc", valid=True, signer="adhoc")
        channels = [dict(kind="location", accepts="adhoc", install_family=location)]
        for stage in ("policy", "path"):
            for error_type in (op.OriginError, OSError, RuntimeError):
                with self.subTest(stage=stage, error=error_type.__name__):
                    with patch.object(op, "channels_for", return_value=channels) as policy, \
                            patch.object(op, "install_family", return_value=location) as resolve:
                        failing = policy if stage == "policy" else resolve
                        failing.side_effect = error_type("injected failure")
                        self.assertEqual(op.gate("claude", None, None, current, location),
                                         (False, "policy-invalid"))
                        policy.assert_called_once_with("claude", None, None, None)
                        if stage == "policy":
                            resolve.assert_not_called()
                        else:
                            resolve.assert_called_once_with(location)

    def test_matching_origin_is_approved_without_errors(self):
        location = "/fixture/bin/tool"
        current = dict(kind="adhoc", valid=True, signer="adhoc")
        channels = [dict(kind="location", accepts="adhoc", install_family=location)]
        with patch.object(op, "channels_for", return_value=channels) as policy, \
                patch.object(op, "install_family", return_value=location) as resolve:
            self.assertEqual(op.gate("claude", None, None, current, location),
                             (True, "origin-approved"))
            policy.assert_called_once_with("claude", None, None, None)
            resolve.assert_called_once_with(location)


if __name__ == "__main__":
    unittest.main()
