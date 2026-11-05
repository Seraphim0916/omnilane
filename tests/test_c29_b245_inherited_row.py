import offline_env  # Activate suite isolation for direct file execution.

from copy import deepcopy
from itertools import permutations
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class InheritedRowTests(unittest.TestCase):
    def test_each_condition_must_be_satisfied_by_one_complete_row(self):
        with tempfile.TemporaryDirectory() as workdir:
            args = SimpleNamespace(background=False, session="auto", thread=None,
                                   job_timeout=None, idle_timeout=None,
                                   mode="work", workdir=workdir)
            identity = dict(vendor="codex", model="fixture-model")
            complete = dict(model=identity["model"], modes=["work"],
                            workdirs=[workdir], tools=["read", "write"],
                            isolations=["shared-inherited"], lifecycles=["single-shot"])
            ctx = dict(inherits_caller_runtime=True, vendor=identity["vendor"],
                       current_model=identity["model"],
                       requirements=dict(tools=["read", "write"],
                                         isolation="shared-inherited", lifecycle="single-shot"))
            # Every pair covers the requirements collectively, but neither row
            # has all permissions. The second row always lacks read permission.
            splits = (
                ("tools", ["read"]),
                ("workdirs", [str(Path(workdir) / "other")]),
                ("modes", ["review"]),
                ("lifecycles", ["persistent"]),
                ("isolations", ["isolated"]),
            )
            for key, missing in splits:
                first, second = deepcopy(complete), deepcopy(complete)
                first[key] = missing
                second["tools"] = ["write"]
                for rows in permutations((first, second)):
                    with self.subTest(condition=key, rows=rows):
                        ctx["capabilities"] = list(rows)
                        self.assertEqual(native.choose_inherited(args, ctx, identity),
                                         (None, "no-capability-row-for-caller-model-mode-and-workdir"))
                for rows in permutations((first, second, complete)):
                    with self.subTest(condition=key, complete_row=True, rows=rows):
                        ctx["capabilities"] = list(rows)
                        self.assertEqual(native.choose_inherited(args, ctx, identity),
                                         ("native", "inherited-caller-runtime"))


if __name__ == "__main__":
    unittest.main()
