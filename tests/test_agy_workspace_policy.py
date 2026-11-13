"""Ownership and race contracts for per-run native AGY workspace policies."""
import offline_env  # Activate suite isolation for direct file execution.
import hashlib
import json
import stat
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

    def test_staged_agent_is_private_regular_file_matching_source(self):
        leaf = self.stage()
        agent = leaf / "agent.md"
        source = self.app / "policy/agent.md"
        self.assertFalse(agent.is_symlink())
        self.assertTrue(stat.S_ISREG(agent.lstat().st_mode))
        self.assertEqual(stat.S_IMODE(agent.stat().st_mode), 0o600)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(hashlib.sha256(agent.read_bytes()).hexdigest(), digest)
        marker = json.loads((leaf / ".omnilane-owned.json").read_text())
        self.assertEqual(marker["policy_sha256"], digest)

    def test_cleanup_preserves_changed_agent_and_marker(self):
        leaf = self.stage()
        agent = leaf / "agent.md"
        agent.unlink()
        agent.write_text("someone else's agent")
        with self.assertRaisesRegex(ValueError, "content changed"):
            self.m.cleanup_work_agent(self.app, self.work)
        self.assertEqual(agent.read_text(), "someone else's agent")
        self.assertTrue((leaf / ".omnilane-owned.json").exists())

    def test_cleanup_preserves_substituted_symlink(self):
        leaf = self.stage()
        agent = leaf / "agent.md"
        agent.unlink()
        source = self.app / "policy/agent.md"
        original = source.read_bytes()
        agent.symlink_to(source)
        with self.assertRaises(OSError):
            self.m.cleanup_work_agent(self.app, self.work)
        self.assertTrue(agent.is_symlink())
        self.assertEqual(source.read_bytes(), original)

    def test_readback_detects_corruption_before_recording_success(self):
        real_fsync = self.m.os.fsync

        def corrupt_staged_file(fd):
            real_fsync(fd)
            for agent in self.work.glob(".agents/agents/*/agent.md"):
                agent.write_bytes(b"corrupted")

        with patch.object(self.m.os, "fsync", side_effect=corrupt_staged_file):
            with self.assertRaisesRegex(ValueError, "content changed during staging"):
                self.stage()
        self.assertFalse((self.app / "workspace-agent.json").exists())

    def test_existing_user_agent_prevents_restage(self):
        leaf = self.stage()
        self.m.cleanup_work_agent(self.app, self.work)
        (leaf / "agent.md").write_text("user agent")
        with self.assertRaisesRegex(ValueError, "active or stale"):
            self.m.stage_work_agent(self.app, self.work)
        self.assertEqual((leaf / "agent.md").read_text(), "user agent")

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
