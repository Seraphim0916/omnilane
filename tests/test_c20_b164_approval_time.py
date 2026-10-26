import offline_env  # Activate suite isolation for direct file execution.
import copy
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class ApprovalTimeTests(unittest.TestCase):
    def policy(self, timestamp):
        return dict(schema_version=1, host=socket.gethostname(), vendors={
            "claude": {"channels": [dict(kind="location", accepts="script",
                install_family="/fixture/tool", approved_via="operator",
                approved_at=timestamp)]}})

    def test_aware_values_preserved_verbatim(self):
        for timestamp in ("2026-10-26T12:00:00Z", "2026-10-26T12:00:00z",
                          "2026-10-26T20:00:00+08:00", "2026-10-26T07:00:00-05:00",
                          "2026-10-26T12:00:00.123456Z",
                          "2026-10-26T12:00:00.123456+00:00"):
            with self.subTest(timestamp=timestamp):
                data = self.policy(timestamp)
                before = copy.deepcopy(data)
                self.assertIs(op.validate(data), data)
                self.assertEqual(data, before)
                self.assertEqual(data["vendors"]["claude"]["channels"][0]["approved_at"],
                                 timestamp)

    def test_invalid_or_naive_values_remain_origin_errors(self):
        for timestamp in ("2026-10-26T12:00:00", "2026-10-26",
                          "2026-02-30T12:00:00+00:00", "2026-13-26T12:00:00Z",
                          "2026-10-26T12:00:00+25:00", "not-a-date", "", None, 42):
            with self.subTest(timestamp=timestamp):
                data = self.policy(timestamp)
                before = copy.deepcopy(data)
                with self.assertRaisesRegex(op.OriginError, "^invalid approval time$"):
                    op.validate(data)
                self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
