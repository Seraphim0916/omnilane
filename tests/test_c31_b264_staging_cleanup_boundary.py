import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location(
    "native_b264", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class StagingCleanupBoundaryTests(unittest.TestCase):
    def entry(self, symlink=False, regular=False):
        entry = Mock(spec=Path)
        entry.is_symlink.return_value = symlink
        entry.is_file.return_value = regular
        return entry

    def assert_no_traversal(self, entry):
        entry.iterdir.assert_not_called()
        entry.glob.assert_not_called()
        entry.rglob.assert_not_called()
        entry.resolve.assert_not_called()
        entry.rmdir.assert_not_called()

    def test_regular_file_is_unlinked_at_this_level_only(self):
        path = Mock(spec=Path)
        child = self.entry(regular=True)
        path.iterdir.return_value = iter([child])
        native.cleanup_staging_dir(path)
        child.unlink.assert_called_once_with()
        self.assert_no_traversal(child)
        path.iterdir.assert_called_once_with()
        path.rmdir.assert_called_once_with()

    def test_symlink_is_unlinked_without_inspecting_target(self):
        path = Mock(spec=Path)
        child = self.entry(symlink=True)
        path.iterdir.return_value = iter([child])
        native.cleanup_staging_dir(path)
        child.unlink.assert_called_once_with()
        child.is_file.assert_not_called()
        child.stat.assert_not_called()
        child.readlink.assert_not_called()
        self.assert_no_traversal(child)
        path.rmdir.assert_called_once_with()

    def test_unknown_directory_stops_before_later_entries(self):
        path = Mock(spec=Path)
        directory = self.entry()
        later = self.entry(regular=True)
        path.iterdir.return_value = iter([directory, later])
        native.cleanup_staging_dir(path)
        directory.unlink.assert_not_called()
        self.assert_no_traversal(directory)
        self.assertEqual(later.mock_calls, [])
        path.rmdir.assert_not_called()

    def test_vanished_staging_directory_is_ignored(self):
        path = Mock(spec=Path)
        path.iterdir.side_effect = FileNotFoundError("fixture already removed")
        native.cleanup_staging_dir(path)
        path.iterdir.assert_called_once_with()
        path.unlink.assert_not_called()
        path.rmdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
