import offline_env  # Activate suite isolation for direct file execution.

from contextlib import ExitStack
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class RevokeEarlyValidationTests(unittest.TestCase):
    def test_invalid_options_rejected_before_any_policy_operation(self):
        cases = (("unknown-vendor", "adhoc"), ("codex", "unknown-kind"),
                 ("codex", "unsigned"))
        for vendor, kind in cases:
            for path in (None, "policy.json"):
                with self.subTest(vendor=vendor, kind=kind, path=path), ExitStack() as stack:
                    later = Mock()
                    for name in ("Path", "policy_path", "_lock", "load", "channels_for", "_save"):
                        operation = stack.enter_context(patch.object(origin, name))
                        later.attach_mock(operation, name)
                    with self.assertRaisesRegex(origin.OriginError, "^unknown vendor or channel kind$"):
                        origin.revoke(vendor, kind, path=path)
                    self.assertEqual(later.mock_calls, [])

    def test_valid_revoke_resolves_locks_reads_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            executable = str(Path(directory) / "cli")
            removed = origin._channel("adhoc", executable, {})
            retained = origin._channel("script", executable, {})
            data = dict(schema_version=1, host=socket.gethostname(),
                        vendors={"codex": {"channels": [removed, retained]}})
            origin._save(path, data)
            with patch.object(origin, "policy_path", return_value=path) as resolve, \
                    patch.object(origin, "_lock", wraps=origin._lock) as lock, \
                    patch.object(origin, "load", wraps=origin.load) as load, \
                    patch.object(origin, "_save", wraps=origin._save) as save:
                origin.revoke("codex", "adhoc")
            resolve.assert_called_once_with()
            lock.assert_called_once_with(path)
            self.assertEqual(load.call_count, 2)
            save.assert_called_once_with(path, dict(schema_version=1, host=socket.gethostname(),
                                                  vendors={"codex": {"channels": [retained]}}))
            self.assertEqual(origin.load(path)["vendors"]["codex"]["channels"], [retained])


if __name__ == "__main__":
    unittest.main()
