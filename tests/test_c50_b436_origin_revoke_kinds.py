import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginRevokeKindsTests(unittest.TestCase):
    def test_script_and_location_revoke_only_the_requested_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "policy.json"
            channels = [origin._channel(kind, root / "cli", {})
                        for kind in ("script", "adhoc", "no-platform-signature")]
            channels.append(origin._channel("developer-id", root / "cli",
                                            {"signer": "TEST000001", "identifier": "example.cli"}))
            original = dict(schema_version=1, host=socket.gethostname(), vendors={
                vendor: {"channels": copy.deepcopy(channels)} for vendor in origin.VENDORS})
            for kind, remaining in (("script", channels[1:]), ("location", channels[3:])):
                with self.subTest(kind=kind):
                    origin._save(path, original)
                    origin.revoke("claude", kind, path=path)
                    actual = origin.load(path)
                    expected = copy.deepcopy(original)
                    expected["vendors"]["claude"]["channels"] = remaining
                    self.assertEqual(actual, expected)
                    self.assertEqual(
                        [channel.get("accepts", channel["kind"])
                         for channel in actual["vendors"]["claude"]["channels"]],
                        ["adhoc", "no-platform-signature", "developer-id"]
                        if kind == "script" else ["developer-id"])


if __name__ == "__main__":
    unittest.main()
