import offline_env  # Activate suite isolation for direct file execution.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from verification_status import transport_guidance


class GuidancePrecedenceTests(unittest.TestCase):
    def test_single_config_conclusive_failure_overrides_retry(self):
        retry = transport_guidance("codex", config_id="target",
                                   environment_failures=("target",))
        self.assertEqual(retry["next_command"],
                         "omnilane resign --retry-failed --vendor codex")
        result = transport_guidance("codex", config_id="target",
                                    environment_failures=("target",),
                                    conclusive_failures=("target",))
        self.assertEqual(result["next_command"], "omnilane doctor")
        self.assertIs(result["requires_gui_session"], False)
        self.assertIn("It will not retry automatically", result["message"])
        self.assertIn("不會自動重試", result["message_zh_tw"])

    def test_candidates_check_every_member_regardless_of_order(self):
        for candidates in (("other", "target"), ("target", "other")):
            with self.subTest(candidates=candidates):
                retry = transport_guidance("codex", candidates=candidates,
                                           environment_failures=("target",))
                self.assertEqual(retry["next_command"],
                                 "omnilane resign --retry-failed --vendor codex")
                result = transport_guidance("codex", candidates=candidates,
                                            environment_failures=("target",),
                                            conclusive_failures=("target",))
                self.assertEqual(result["next_command"], "omnilane doctor")

    def test_unrelated_failures_do_not_apply_to_current_selection(self):
        selections = ({"config_id": "target"}, {"candidates": ("target", "second")},
                      {"config_id": "target", "candidates": ("unrelated",)})
        for selection in selections:
            with self.subTest(selection=selection):
                result = transport_guidance("codex", **selection,
                                            environment_failures=("unrelated",),
                                            conclusive_failures=("unrelated",))
                self.assertEqual(result["next_command"], "omnilane resign --vendor codex")
                retry = transport_guidance("codex", **selection,
                                           environment_failures=("target",),
                                           conclusive_failures=("unrelated",))
                self.assertEqual(retry["next_command"],
                                 "omnilane resign --retry-failed --vendor codex")

    def test_no_selection_does_not_inherit_other_failures(self):
        result = transport_guidance("codex", environment_failures=("unrelated",),
                                    conclusive_failures=("unrelated",))
        self.assertEqual(result["next_command"], "omnilane resign --vendor codex")


if __name__ == "__main__":
    unittest.main()
