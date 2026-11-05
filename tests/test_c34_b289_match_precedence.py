import offline_env  # Activate suite isolation for direct file execution.

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


class MatchPrecedenceTests(unittest.TestCase):
    def test_first_remaining_mismatch_and_inputs_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("native_b289", root / "scripts/lib/native.py")
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        args = SimpleNamespace(executor="auto", vendor="codex", model="fixture-model",
                               effort="high", mode="work", workdir=str(root),
                               background=False, session="auto", thread="",
                               job_timeout="", idle_timeout="")
        cap = dict(model=args.model, efforts=[], modes=[], workdirs=[], tools=[],
                   isolations=[], lifecycles=[])
        ctx = dict(vendor="codex", capabilities=[cap], requirements=dict(
            tools=["shell"], isolation="shared-inherited", lifecycle="single-shot"))
        repairs = [("effort", "efforts", "high"), ("mode", "modes", "work"),
                   ("workdir", "workdirs", str(root)), ("tools", "tools", "shell"),
                   ("isolation", "isolations", "shared-inherited"),
                   ("lifecycle", "lifecycles", "single-shot")]
        for reason, key, value in repairs:
            with self.subTest(reason=reason):
                before = copy.deepcopy((vars(args), ctx))
                self.assertEqual(native.choose(args, ctx),
                                 ("cli", reason + "-mismatch", args.model))
                self.assertEqual((vars(args), ctx), before)
            cap[key] = [value]
        before = copy.deepcopy((vars(args), ctx))
        self.assertEqual(native.choose(args, ctx),
                         ("native", "exact-capability-match", args.model))
        self.assertEqual((vars(args), ctx), before)


if __name__ == "__main__":
    unittest.main()
