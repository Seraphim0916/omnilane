import offline_env  # Activate suite isolation for direct file execution.

import copy
import itertools
import unittest
from unittest import mock

from test_native_context_host_rows import native_context


class CapabilityOrderTests(unittest.TestCase):
    def test_model_and_effort_permutations_preserve_complete_output(self):
        base = {"model": "base", "efforts": ["low"],
                "modes": ["work", "advise"], "workdirs": ["/fixture/work"]}
        entries = [("z", ["low", "high"]), ("base", ["low", "high"]),
                   ("a", ["low", "high"])]
        expected = [{"model": model, "efforts": efforts,
                     "modes": ["work", "advise"], "workdirs": ["/fixture/work"],
                     "tools": [], "isolations": ["shared-inherited"],
                     "lifecycles": ["single-shot"]}
                    for model, efforts in (("a", ["high", "low"]),
                                           ("base", ["high"]), ("z", ["high", "low"]))]
        registry = {"fixture": "scored configurations"}
        original_base = copy.deepcopy(base)
        with mock.patch.object(native_context.caller_identity, "resolve",
                               return_value=({"scored": True}, "")) as resolve:
            baseline = native_context.host_capabilities(entries, registry, "codex", base)
            self.assertEqual(baseline, expected)
            for order in itertools.permutations(entries):
                for reverse in itertools.product((False, True), repeat=3):
                    variant = [(model, list(reversed(efforts)) if flip else list(efforts))
                               for (model, efforts), flip in zip(order, reverse)]
                    with self.subTest(order=[row[0] for row in order], reverse=reverse):
                        before = copy.deepcopy(variant)
                        resolve.reset_mock()
                        actual = native_context.host_capabilities(variant, registry, "codex", base)
                        self.assertEqual(actual, baseline)
                        self.assertEqual(actual, expected)
                        self.assertEqual(variant, before)
                        self.assertEqual(base, original_base)
                        self.assertCountEqual(resolve.call_args_list, [
                            mock.call(registry, "codex", model, effort)
                            for model, efforts in variant for effort in efforts])


if __name__ == "__main__":
    unittest.main()
