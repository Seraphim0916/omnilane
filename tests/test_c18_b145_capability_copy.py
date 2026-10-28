import offline_env  # Activate suite isolation for direct file execution.
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native_context as nc


class CapabilityCopyTests(unittest.TestCase):
    def test_mutations_leave_inputs_siblings_and_other_calls_unchanged(self):
        with tempfile.TemporaryDirectory() as workdir:
            base = {"model": "alpha", "efforts": ["medium"],
                    "modes": ["work", "advise"], "workdirs": [workdir]}
            entries = [("zeta", ["low", "high"]),
                       ("alpha", ["medium", "low", "high"])]
            original = copy.deepcopy((entries, base))
            expected = [
                {"model": model, "efforts": ["high", "low"],
                 "modes": ["work", "advise"], "workdirs": [workdir],
                 "tools": [], "isolations": ["shared-inherited"],
                 "lifecycles": ["single-shot"]}
                for model in ("alpha", "zeta")
            ]
            with patch.object(nc.caller_identity, "resolve", return_value=({}, None)):
                for field in ("modes", "workdirs", "efforts"):
                    for index in range(2):
                        with self.subTest(field=field, row=index):
                            rows = nc.host_capabilities(entries, {}, "codex", base)
                            other_call = nc.host_capabilities(entries, {}, "codex", base)
                            self.assertEqual(rows, expected)
                            rows[index][field].append("mutation-sentinel")
                            self.assertEqual((entries, base), original)
                            self.assertEqual(rows[1 - index], expected[1 - index])
                            self.assertEqual(other_call, expected)
                            self.assertEqual(
                                nc.host_capabilities(entries, {}, "codex", base), expected)

    def test_current_model_without_extra_efforts_is_omitted(self):
        base = {"model": "alpha", "efforts": ["medium"],
                "modes": ["advise"], "workdirs": []}
        with patch.object(nc.caller_identity, "resolve", return_value=({}, None)):
            self.assertEqual(nc.host_capabilities(
                [("alpha", ["medium"])], {}, "codex", base), [])


if __name__ == "__main__":
    unittest.main()
