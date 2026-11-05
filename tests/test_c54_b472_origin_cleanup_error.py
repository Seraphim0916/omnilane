"""A failed temporary cleanup never replaces the write error that caused it."""
import offline_env  # noqa: F401
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


def policy():
    return dict(schema_version=1, host=socket.gethostname(), vendors={})


def temporaries(directory):
    return list(Path(directory).glob("policy.json.tmp-*"))


class CleanupErrorTests(unittest.TestCase):
    def test_replace_error_survives_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            replace_error = OSError("REPLACE_FAIL")
            with patch.object(op.os, "replace", side_effect=replace_error), \
                    patch.object(op.os, "unlink", side_effect=OSError("CLEANUP_FAIL")):
                with self.assertRaises(OSError) as caught:
                    op._save(path, policy())
            self.assertIs(caught.exception, replace_error)
            self.assertFalse(path.exists())
            self.assertEqual(len(temporaries(directory)), 1)

    def test_write_error_survives_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            write_error = OSError("FSYNC_FAIL")
            with patch.object(op.os, "fsync", side_effect=write_error), \
                    patch.object(op.os, "unlink", side_effect=OSError("CLEANUP_FAIL")):
                with self.assertRaises(OSError) as caught:
                    op._save(path, policy())
            self.assertIs(caught.exception, write_error)

    def test_replace_error_still_removes_temporary_when_cleanup_works(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            replace_error = OSError("REPLACE_FAIL")
            with patch.object(op.os, "replace", side_effect=replace_error):
                with self.assertRaises(OSError) as caught:
                    op._save(path, policy())
            self.assertIs(caught.exception, replace_error)
            self.assertEqual(temporaries(directory), [])

    def test_cleanup_failure_alone_is_raised_after_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            cleanup_error = OSError("CLEANUP_FAIL")
            # A copy publishes the policy but leaves the temporary for cleanup to remove.
            with patch.object(op.os, "replace", side_effect=shutil.copy2), \
                    patch.object(op.os, "unlink", side_effect=cleanup_error):
                with self.assertRaises(OSError) as caught:
                    op._save(path, policy())
            self.assertIs(caught.exception, cleanup_error)
            self.assertEqual(op.load(path), policy())

    def test_success_publishes_and_leaves_no_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            op._save(path, policy())
            self.assertEqual(op.load(path), policy())
            self.assertEqual(temporaries(directory), [])


if __name__ == "__main__":
    unittest.main()
