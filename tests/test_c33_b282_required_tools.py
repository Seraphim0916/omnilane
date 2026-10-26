import offline_env  # Activate suite isolation for direct file execution.
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class RequiredToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workdir = str(Path(temporary.name).resolve())
        self.args = SimpleNamespace(executor="auto", vendor="codex", model="fixture-model",
                                    effort="high", mode="work", workdir=self.workdir,
                                    background=False, session="auto", thread=None,
                                    job_timeout=None, idle_timeout=None)

    def context(self, required, available):
        return dict(schema_version=1, harness="codex", vendor="codex",
                    requirements=dict(tools=required, isolation="shared-inherited", lifecycle="single-shot"),
                    capabilities=[dict(model="fixture-model", efforts=["high"], modes=["work"],
                                       workdirs=[self.workdir], tools=available,
                                       isolations=["shared-inherited"], lifecycles=["single-shot"])])

    def test_validated_requirements_use_exact_subset_matching(self):
        for available in (["shell"], ["shell", "read", "custom-tool"]):
            for required, executor, reason in (
                    ([], "native", "exact-capability-match"),
                    (["shell"], "native", "exact-capability-match"),
                    (["Shell"], "cli", "tools-mismatch")):
                with self.subTest(required=required, available=available):
                    ctx = native.capability_context(None, value=self.context(required[:], available[:]))
                    self.assertEqual(native.choose(self.args, ctx), (executor, reason, "fixture-model"))
                    self.assertEqual(ctx["requirements"]["tools"], required)
                    self.assertEqual(ctx["capabilities"][0]["tools"], available)

    def test_duplicate_requirements_are_rejected_before_selection(self):
        ctx = self.context(["shell", "shell"], ["shell", "read"])
        with self.assertRaisesRegex(ValueError, "^duplicate required tools$"):
            native.capability_context(None, value=ctx)


if __name__ == "__main__":
    unittest.main()
