import offline_env  # Activate suite isolation for direct file execution.

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class SummaryActionTests(unittest.TestCase):
    def test_reason_specific_actions_do_not_use_transport_guidance(self):
        cases = (
            ("verification-busy", "wait for the running verification, then retry"),
            ("cli-not-installed", "install this vendor's CLI, then run `omnilane resign --vendor {vendor}`"),
            ("host-configuration-unavailable", "check local.sh and the overlay path with `omnilane doctor`"),
            ("not-configured-or-invalid-arguments", "check local.sh and the overlay path with `omnilane doctor`"),
        )
        for vendor in ("claude", "codex", "gemini", "grok"):
            for reason, action in cases:
                with self.subTest(vendor=vendor, reason=reason):
                    state = {"schema_version": 1, "pending": {vendor: {"reason": reason}}}
                    with patch.object(status, "load", return_value=state), \
                            patch.object(status, "transport_guidance") as transport:
                        summary = status.pending_summary()
                    self.assertEqual(summary, "{}: {}; next: {}".format(
                        vendor, reason, action.format(vendor=vendor)))
                    transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
