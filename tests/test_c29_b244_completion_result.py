import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import native


class CompletionResultTests(unittest.TestCase):
    def validate(self, result):
        state = dict(job_id="20261026-120000-1-1", vendor="codex",
                     model="fixture-model", effort="high", harness="codex")
        value = dict(schema_version=1, job_id=state["job_id"], agent_id="/root/worker",
                     runtime={key: state[key] for key in
                              ("vendor", "model", "effort", "harness")},
                     outcome="success", result=result, evidence=["fixture evidence"])
        value["runtime"]["backend"] = "collaboration.spawn_agent"
        validated = native.validate_completion(value, state)
        self.assertIs(validated, value)
        self.assertEqual(validated["result"], result)
        self.assertIs(validated["result"], result)
        return validated

    def test_exact_character_limit_preserves_ascii_and_chinese(self):
        for character in ("a", "中"):
            with self.subTest(character=character):
                self.validate(character * 65536)

    def test_one_character_over_limit_is_rejected(self):
        for character in ("a", "中"):
            with self.subTest(character=character):
                with self.assertRaisesRegex(ValueError, "^invalid result$"):
                    self.validate(character * 65537)

    def test_lf_tab_and_surrounding_whitespace_are_preserved(self):
        self.validate(" \t中文\n結果\t \n")

    def test_cr_nul_del_are_rejected(self):
        for character in ("\r", "\x00", "\x7f"):
            with self.subTest(character=repr(character)):
                with self.assertRaisesRegex(ValueError, "^control character in result$"):
                    self.validate("中文" + character + "結果")


if __name__ == "__main__":
    unittest.main()
