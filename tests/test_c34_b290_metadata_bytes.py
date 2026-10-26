import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


class MetadataBytesTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("native_b290", root / "scripts/lib/native.py")
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "uncreated-home"
        self.args = SimpleNamespace(dry_run=False, task="task", home=str(self.home), policy="unused")
        self.policy = dict(code="allowed", effective_ceiling="fixture")
        self.created = "2026-01-01T00:00:00Z"

    def plan(self, lane):
        return dict(lane=lane, vendor="codex", model="fixture", effort="high",
                    mode="work", workdir=str(self.home), executor="native",
                    executor_reason="exact-capability-match", task="task")

    def metadata(self, lane):
        result = self.plan(lane)
        del result["task"]
        result.update(aa_policy_code="allowed", aa_effective_ceiling="fixture",
                      started=self.created, session_mode="native")
        return result

    def exercise(self, lane, accepted, expected_bytes):
        native = self.native
        calls = []
        plan = self.plan(lane)

        def root(home, create=False):
            calls.append("root")
            self.assertEqual(home, str(self.home))
            self.assertTrue(create)
            return self.home / "jobs"

        def publish(root_path, job_id, published, metadata, registry, caller, policy):
            calls.append("publish")
            self.assertEqual(root_path, self.home / "jobs")
            self.assertEqual(metadata, self.metadata(lane))
            self.assertEqual(len(native.json_text(metadata).encode("utf-8")), expected_bytes)
            self.assertLess(len(native.json_text(published).encode("utf-8")), native.MAX_BYTES)

        self.assertEqual(len(native.json_text(self.metadata(lane)).encode("utf-8")), expected_bytes)
        with mock.patch.object(native, "stamp", return_value=self.created), \
                mock.patch.object(native, "jobs_root", side_effect=root), \
                mock.patch.object(native, "publish_job", side_effect=publish), \
                contextlib.redirect_stdout(io.StringIO()):
            if accepted:
                self.assertEqual(native.publish_plan(self.args, plan, self.policy, {}, None), 0)
            else:
                with self.assertRaisesRegex(ValueError, "^metadata too large$"):
                    native.publish_plan(self.args, plan, self.policy, {}, None)
        self.assertLess(len(native.json_text(plan).encode("utf-8")), native.MAX_BYTES)
        self.assertEqual(calls, ["root", "publish"] if accepted else [])
        self.assertFalse(self.home.exists())

    def test_exact_utf8_boundaries_ascii_and_chinese(self):
        base = len(self.native.json_text(self.metadata("")).encode("utf-8"))
        for size in (4096, 4097):
            for character in ("a", "中"):
                with self.subTest(size=size, character=character):
                    count, remainder = divmod(size - base, len(character.encode("utf-8")))
                    self.exercise(character * count + "a" * remainder, size == 4096, size)

    def test_equal_character_counts_have_different_byte_limits(self):
        for character, accepted in (("a", True), ("中", False)):
            with self.subTest(character=character):
                lane = character * 3000
                size = len(self.native.json_text(self.metadata(lane)).encode("utf-8"))
                self.exercise(lane, accepted, size)


if __name__ == "__main__":
    unittest.main()
