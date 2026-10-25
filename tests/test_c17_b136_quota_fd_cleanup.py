import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as quota


class QuotaDescriptorCleanupTests(unittest.TestCase):
    def test_leaf_open_failure_closes_parent_exactly_once(self):
        failure = OSError("synthetic leaf failure")
        with patch.object(quota.os, "open", side_effect=[101, failure]) as opened, \
                patch.object(quota.os, "close") as closed, \
                patch.object(quota.os, "fdopen") as stream:
            with self.assertRaises(OSError) as caught:
                quota.read_snapshot("synthetic-quota")
            self.assertIs(caught.exception, failure)
            self.assertEqual(opened.call_count, 2)
            closed.assert_called_once_with(101)
            stream.assert_not_called()

    def test_parent_open_failure_closes_nothing(self):
        failure = OSError("synthetic parent failure")
        with patch.object(quota.os, "open", side_effect=failure) as opened, \
                patch.object(quota.os, "close") as closed, \
                patch.object(quota.os, "fdopen") as stream:
            with self.assertRaises(OSError) as caught:
                quota.read_snapshot("synthetic-quota")
            self.assertIs(caught.exception, failure)
            opened.assert_called_once()
            closed.assert_not_called()
            stream.assert_not_called()

    def test_success_opens_leaf_relative_to_parent(self):
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.fileno.return_value = 102
        stream.read.return_value = b"{}"
        flags = quota.os.O_RDONLY | quota.os.O_NOFOLLOW | quota.os.O_NONBLOCK
        with patch.object(quota.os, "open", side_effect=[101, 102]) as opened, \
                patch.object(quota.os, "close") as closed, \
                patch.object(quota.os, "fdopen", return_value=stream) as fdopen, \
                patch.object(quota.os, "fstat", return_value=SimpleNamespace(
                    st_mode=stat.S_IFREG, st_size=2)):
            self.assertEqual(quota.read_snapshot("synthetic-quota"), "{}")
            self.assertEqual(opened.call_args_list, [
                call("synthetic-quota", flags | quota.os.O_DIRECTORY),
                call("codex.json", flags, dir_fd=101),
            ])
            closed.assert_called_once_with(101)
            fdopen.assert_called_once_with(102, "rb")
            stream.__exit__.assert_called_once_with(None, None, None)


if __name__ == "__main__":
    unittest.main()
