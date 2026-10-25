import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as policy


class DuplicateKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "synthetic-policy.json"
        self.host = "fixture-host"

    def load(self, raw):
        with patch.object(policy, "_read", return_value=raw.encode("utf-8")) as read, \
                patch.object(policy.socket, "gethostname", return_value=self.host):
            try:
                return policy.load(self.path)
            finally:
                read.assert_called_once_with(self.path)

    def document(self, vendors):
        return '{"schema_version":1,"host":"fixture-host","vendors":' + vendors + '}'

    def test_duplicate_keys_at_multiple_depths_and_decoded_names(self):
        cases = (
            ('"codex"', '"codex"', lambda pair: "{" + pair + "}", '{"channels":[]}'),
            ('"channels"', '"channels"', lambda pair: '{"codex":{' + pair + '}}', '[]'),
            ('"kind"', '"kind"', lambda pair: '{"codex":{"channels":[{' + pair + '}]}}', '"location"'),
            ('"codex"', r'"co\u0064ex"', lambda pair: "{" + pair + "}", '{"channels":[]}'),
            ('"channels"', r'"chan\u006eels"', lambda pair: '{"codex":{' + pair + '}}', '[]'),
            ('"kind"', r'"k\u0069nd"', lambda pair: '{"codex":{"channels":[{' + pair + '}]}}', '"location"'),
        )
        for first, second, wrap, value in cases:
            with self.subTest(first=first, second=second):
                raw = self.document(wrap(first + ':' + value + ',' + second + ':' + value))
                with self.assertRaisesRegex(policy.OriginError,
                                            r"^invalid origin policy: duplicate JSON key$"):
                    self.load(raw)

    def test_same_decoded_keys_in_separate_objects_are_valid(self):
        channel = dict(kind="location", accepts="script", install_family="/fixture/tool",
                       approved_at="2026-10-26T00:00:00+00:00", approved_via="operator")
        second_channel = dict(channel, install_family="/fixture/other-tool")
        entry = json.dumps({"channels": [channel, second_channel]})
        escaped_entry = entry.replace('"channels"', r'"chan\u006eels"').replace(
            '"kind"', r'"k\u0069nd"')
        raw = self.document('{"codex":' + entry + ',"claude":' + escaped_entry + '}')
        expected = dict(schema_version=1, host=self.host, vendors={
            vendor: {"channels": [channel, second_channel]} for vendor in ("codex", "claude")})
        self.assertEqual(self.load(raw), expected)


if __name__ == "__main__":
    unittest.main()
