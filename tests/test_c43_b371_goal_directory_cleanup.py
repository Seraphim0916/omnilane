import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import goal_dispatch as gd


class DirectoryCleanupTests(unittest.TestCase):
    def test_fstat_failure_closes_once_and_preserves_exception(self):
        failure = OSError("synthetic fstat failure")
        with patch.object(gd.os, "open", return_value=71), \
                patch.object(gd.os, "fstat", side_effect=failure), \
                patch.object(gd.os, "close") as close:
            with self.assertRaises(OSError) as caught:
                gd.directory(None, "journal")
        self.assertIs(caught.exception, failure)
        close.assert_called_once_with(71)

    def test_cleanup_failure_does_not_mask_original(self):
        failure = OSError("synthetic fstat failure")
        with patch.object(gd.os, "open", return_value=71), \
                patch.object(gd.os, "fstat", side_effect=failure), \
                patch.object(gd.os, "close", side_effect=OSError("close failed")) as close:
            with self.assertRaises(OSError) as caught:
                gd.directory(None, "journal")
        self.assertIs(caught.exception, failure)
        close.assert_called_once_with(71)

    def test_success_transfers_live_descriptor_to_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            fd = gd.directory(None, directory)
            try:
                self.assertEqual(os.fstat(fd).st_uid, os.geteuid())
            finally:
                os.close(fd)

    def test_wrong_owner_still_rejected_and_closed_once(self):
        with patch.object(gd.os, "open", return_value=71), \
                patch.object(gd.os, "fstat") as info, \
                patch.object(gd.os, "close") as close:
            info.return_value.st_uid = os.geteuid() + 1
            with self.assertRaisesRegex(ValueError, "different owner"):
                gd.directory(None, "journal")
        close.assert_called_once_with(71)


if __name__ == "__main__":
    unittest.main()
