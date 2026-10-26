import offline_env  # Activate suite isolation for direct file execution.
import copy
import itertools
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class ChannelOrderTests(unittest.TestCase):
    def test_permutations_preserve_decision_reason_and_inputs(self):
        location = "/fixture/bin/tool"
        developer = dict(kind="developer-id", install_family=location,
                         team_id="TEST000001", identifier_family="vendor.<version>")
        for kind in ("developer-id", "adhoc", "script", "no-platform-signature"):
            current = dict(kind=kind, valid=True, identifier="vendor.1.2.3",
                           signer="TEST000001" if kind == "developer-id" else "adhoc")
            approved = (developer.copy() if kind == "developer-id" else
                        dict(kind="location", accepts=kind, install_family=location))
            elsewhere = dict(approved, install_family="/elsewhere/tool")
            other_kind = (dict(kind="location", accepts="script", install_family=location)
                          if kind == "developer-id" else developer.copy())
            cases = [([elsewhere, other_kind, approved], (True, "origin-approved")),
                     ([elsewhere, other_kind], (False, "location-changed")),
                     ([other_kind], (False, "origin-unapproved"))]
            for channels, expected in cases:
                for order in itertools.permutations(channels):
                    with self.subTest(kind=kind, order=order, expected=expected):
                        supplied = list(order)
                        recorded = {"kind": "unsigned"}
                        before = copy.deepcopy((supplied, current, recorded))
                        with patch.object(op, "channels_for", return_value=supplied), \
                                patch.object(op, "install_family", return_value=location):
                            self.assertEqual(op.gate("claude", recorded, location,
                                                     current, location), expected)
                        self.assertEqual((supplied, current, recorded), before)


if __name__ == "__main__":
    unittest.main()
