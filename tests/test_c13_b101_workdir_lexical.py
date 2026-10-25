import offline_env  # Activate suite isolation for direct file execution.
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "c13_budget", Path(__file__).resolve().parents[1] / "scripts/lib/goal_budget.py")
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)


class WorkdirLexicalTests(unittest.TestCase):
    def test_valid_absolute_paths_are_preserved_without_filesystem_access(self):
        paths = ("/", "/.", "/..", "/fixture/./child", "/fixture/../other",
                 "//fixture///child/", "/fixture/中文 空白", "/fixture/ link /../中文/.")
        for path in paths:
            with self.subTest(path=path):
                value = dict(status="open", budget_jobs=None, budget_seconds=5,
                             spent_jobs=0, spent_seconds=0, fuse_trips=0,
                             started_epoch=0, workdir=path)
                before = value.copy()
                with ExitStack() as stack:
                    for target in ("builtins.open", "io.open", "os.stat", "os.lstat",
                                   "os.readlink", "os.listdir", "os.scandir",
                                   "os.path.realpath", "os.path.abspath", "os.path.normpath",
                                   "pathlib.Path.resolve"):
                        guard = stack.enter_context(patch(
                            target, side_effect=AssertionError("unexpected path lookup: " + target)))
                        stack.callback(guard.assert_not_called)
                    result = budget.validate_budget(value)
                self.assertIs(result, value)
                self.assertEqual(result["workdir"], path)
                self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
