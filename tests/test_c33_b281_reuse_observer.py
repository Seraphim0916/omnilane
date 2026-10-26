import offline_env  # Activate suite isolation for direct file execution.
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class ReuseObserverTests(unittest.TestCase):
    def context(self, workdir, observer):
        return dict(
            schema_version=1, harness="codex", vendor="codex", agent_strategy="reuse",
            existing_agent=dict(agent_id="/root/worker", vendor="codex", model="fixture-model",
                                effort="high", harness="codex", state="idle",
                                observed_by=observer, evidence=["caller observation"]),
            requirements=dict(tools=[], isolation="shared-inherited", lifecycle="single-shot"),
            capabilities=[dict(model="fixture-model", efforts=["high"], modes=["work"],
                               workdirs=[workdir], tools=[], isolations=["shared-inherited"],
                               lifecycles=["single-shot"], agent_strategy="reuse",
                               existing_agent_id="/root/worker")])

    def test_exact_caller_reaches_real_workdir_validation(self):
        with tempfile.TemporaryDirectory() as workdir:
            ctx = self.context(workdir, "caller")
            with mock.patch.object(native, "canonical_dir", wraps=native.canonical_dir) as paths:
                self.assertIs(native.capability_context(None, value=ctx), ctx)
                paths.assert_called_once_with(workdir)
            self.assertEqual(ctx["capabilities"][0]["workdirs"], [str(Path(workdir).resolve())])

    def test_other_observers_fail_before_workdir_validation(self):
        with tempfile.TemporaryDirectory() as workdir:
            for observer in ("host", "Caller", "", None, True, False):
                with self.subTest(observer=observer):
                    ctx = self.context(workdir, observer)
                    with mock.patch.object(native, "canonical_dir", wraps=native.canonical_dir) as paths:
                        with self.assertRaisesRegex(ValueError, "^existing agent must be caller-observed$"):
                            native.capability_context(None, value=ctx)
                        paths.assert_not_called()


if __name__ == "__main__":
    unittest.main()
