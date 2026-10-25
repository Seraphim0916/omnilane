import offline_env  # Activate suite isolation for direct file execution.
from pathlib import Path
import socket
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


def policy(field, text):
    channel = {"kind": "developer-id", "team_id": "TEST000001",
               "identifier_family": "vendor.tool", "install_family": "/fixture/tool",
               "approved_at": "2026-10-26T00:00:00+00:00", "approved_via": "operator"}
    channel[field] = text
    return {"schema_version": 1, "host": socket.gethostname(),
            "vendors": {"codex": {"channels": [channel]}}}


class OriginTextBoundsTests(unittest.TestCase):
    def test_length_boundary_through_validate(self):
        for field in ("identifier_family", "install_family"):
            for length in (4096, 4097):
                with self.subTest(field=field, length=length):
                    text = ("/" if field == "install_family" else "v") + "x" * (length - 1)
                    data = policy(field, text)
                    if length == 4096:
                        self.assertEqual(op.validate(data), data)
                    else:
                        with self.assertRaises(op.OriginError):
                            op.validate(data)

    def test_all_c0_ascii_controls_are_rejected(self):
        for field in ("identifier_family", "install_family"):
            for codepoint in range(32):
                with self.subTest(field=field, codepoint=codepoint):
                    with self.assertRaises(op.OriginError):
                        op.validate(policy(field, "/prefix" + chr(codepoint) + "suffix"))

    def test_empty_identifier_allowed_but_empty_location_rejected(self):
        data = policy("identifier_family", "")
        self.assertEqual(op.validate(data), data)
        with self.assertRaisesRegex(op.OriginError, "invalid install family"):
            op.validate(policy("install_family", ""))

    def test_unicode_and_del_remain_accepted(self):
        # The current contract rejects ord(c) < 32, not DEL or Unicode separators.
        for field in ("identifier_family", "install_family"):
            for text in ("/育苗/工具", "/café/工具\u2028下一行", "/prefix\x7fsuffix"):
                with self.subTest(field=field, text=text):
                    data = policy(field, text)
                    self.assertEqual(op.validate(data), data)


if __name__ == "__main__":
    unittest.main()
