"""Ownership and race contracts for per-run native AGY workspace policies."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class AgyWorkspacePolicyContracts(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "agy_policy_test", Path(__file__).resolve().parents[1] / "scripts/lib/prepare-agy-mode.py")
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory(prefix="omnilane-agy-policy-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / "work"
        self.app = self.root / "app"
        self.work.mkdir()
        self.app.mkdir()

    def stage(self):
        self.m.stage_work_agent(self.app, self.work)
        return next((self.work / ".agents/agents").iterdir())

    def test_cleanup_and_resume_reuse_only_same_empty_owned_inode(self):
        leaf = self.stage()
        inode = leaf.stat().st_ino
        policy = (self.app / "policy/agent.md").read_bytes()
        self.m.cleanup_work_agent(self.app, self.work)
        self.assertTrue(leaf.is_dir())
        self.assertEqual(list(leaf.iterdir()), [])
        self.assertFalse((self.app / "workspace-agent.json").exists())
        self.m.stage_work_agent(self.app, self.work)
        self.assertEqual(leaf.stat().st_ino, inode)
        self.assertEqual((leaf / "agent.md").read_bytes(), policy)
        self.assertEqual((self.app / "policy/agent.md").read_bytes(), policy)

    def test_cache_conflict_precedes_any_workspace_policy_leaf(self):
        (self.work / ".omnilane-cache").symlink_to(self.app)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.stage()
        self.assertFalse((self.work / ".agents").exists())

    def test_cleanup_preserves_concurrent_replacement_directory(self):
        leaf = self.stage()
        moved = self.work / "moved-original"
        real_unlink = self.m.os.unlink

        def replace_after_unlink(path, *args, **kwargs):
            result = real_unlink(path, *args, **kwargs)
            if path == ".omnilane-owned.json" and kwargs.get("dir_fd") is not None:
                leaf.rename(moved)
                leaf.mkdir()
            return result

        with patch.object(self.m.os, "unlink", side_effect=replace_after_unlink):
            with self.assertRaisesRegex(ValueError, "replacement preserved"):
                self.m.cleanup_work_agent(self.app, self.work)
        self.assertTrue(leaf.is_dir())
        self.assertEqual(list(leaf.iterdir()), [])
        self.assertTrue(moved.is_dir())

    def test_resume_rechecks_owned_inode_after_open_before_writing(self):
        leaf = self.stage()
        self.m.cleanup_work_agent(self.app, self.work)
        moved = self.work / "moved-original"
        real_open = self.m.os.open
        replaced = False

        def replace_before_open(path, *args, **kwargs):
            nonlocal replaced
            if Path(path) == leaf and not replaced:
                replaced = True
                leaf.rename(moved)
                leaf.mkdir()
            return real_open(path, *args, **kwargs)

        with patch.object(self.m.os, "open", side_effect=replace_before_open):
            with self.assertRaisesRegex(ValueError, "changed before staging"):
                self.m.stage_work_agent(self.app, self.work)
        self.assertTrue(replaced)
        self.assertEqual(list(leaf.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
