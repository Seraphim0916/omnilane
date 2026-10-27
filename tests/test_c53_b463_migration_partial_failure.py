import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class MigrationPartialFailureTests(unittest.TestCase):
    def test_second_channel_failure_discards_first_with_success_control(self):
        recorded = dict(kind="developer-id", signer="TEST000001", valid=True,
                        identifier="vendor.1.2.3", operator_trust=origin.provenance.TRUST_ADHOC)
        real_channel = origin._channel
        with tempfile.TemporaryDirectory() as directory:
            executable = str(Path(directory) / "cli")
            for error in (origin.OriginError, TypeError, None):
                with self.subTest(error=error):
                    built = []

                    def build(kind, *args, **kwargs):
                        if kind == "adhoc" and error is not None:
                            raise error("second channel failed")
                        result = real_channel(kind, *args, **kwargs)
                        built.append(result)
                        return result

                    with patch.object(origin, "_channel", side_effect=build) as channel:
                        result = origin.implicit_channels(recorded, executable)
                    self.assertEqual(channel.call_args_list, [
                        call("developer-id", executable, recorded, "migrated-from-overlay"),
                        call("adhoc", executable, recorded, "migrated-from-overlay"),
                    ])
                    self.assertEqual(built[0]["kind"], "developer-id")
                    if error is not None:
                        self.assertEqual(len(built), 1)
                        self.assertEqual(result, [])
                    else:
                        self.assertEqual(len(built), 2)
                        self.assertEqual(result, built)
                        self.assertEqual(result[1]["kind"], "location")
                        self.assertEqual(result[1]["accepts"], "adhoc")
                        self.assertTrue(all(c["approved_via"] == "migrated-from-overlay" for c in result))


if __name__ == "__main__":
    unittest.main()
