import offline_env  # Activate suite isolation for direct file execution.
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class DualMigrationTests(unittest.TestCase):
    def test_signer_and_operator_migrations_are_independent(self):
        original = dict(kind="developer-id", signer="TEST000001", valid=True,
                        identifier="vendor.1.2.3", operator_trust=op.provenance.TRUST_ADHOC)
        cases = [("both", {}, ["developer-id", "adhoc"]),
                 ("invalid_signature", {"valid": False}, ["adhoc"]),
                 ("invalid_signer", {"signer": "bad"}, ["adhoc"]),
                 ("missing_identifier", {"identifier": ""}, ["adhoc"]),
                 ("wrong_kind", {"kind": "unsigned"}, ["adhoc"])]
        path = "/fixture/bin/tool"
        for name, overrides, expected in cases:
            with self.subTest(case=name):
                recorded = dict(original, **overrides)
                before = copy.deepcopy(recorded)

                def channel(kind, actual_path, current, via):
                    self.assertEqual(actual_path, path)
                    self.assertIs(current, recorded)
                    return dict(kind=kind, approved_via=via)

                with patch.object(op, "_channel", side_effect=channel) as build:
                    result = op.implicit_channels(recorded, path)
                    self.assertEqual([item["kind"] for item in result], expected)
                    self.assertEqual(result, [dict(kind=kind, approved_via="migrated-from-overlay")
                                              for kind in expected])
                    self.assertEqual(build.call_args_list,
                                     [call(kind, path, recorded, "migrated-from-overlay")
                                      for kind in expected])
                self.assertEqual(recorded, before)


if __name__ == "__main__":
    unittest.main()
