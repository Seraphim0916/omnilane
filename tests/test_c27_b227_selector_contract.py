import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import build_overlay
import transport_compat


class SelectorContractTests(unittest.TestCase):
    def test_selector_types_preserve_runtime_alias_effort_and_flag(self):
        cases = (
            ("cli_reasoning_effort", "grok", "runtime.alias", "high", "--reasoning-effort"),
            ("model_and_effort", "codex", "runtime-model", "medium", None),
            ("model_id_encoded_effort", "gemini", "runtime-model-ultra", "ultra", None),
        )
        proven = {"synthetic-" + selector: (selector, alias, "unused-proof")
                  for selector, vendor, alias, effort, flag in cases}
        with patch.dict(build_overlay.PROVEN, proven, clear=True):
            for selector, vendor, alias, effort, flag in cases:
                with self.subTest(selector=selector):
                    row = {"id": "synthetic-" + selector, "vendor": vendor,
                           "model": "registered-name-not-runtime", "effort": effort}
                    before = dict(row)
                    self.assertEqual(transport_compat.selector_contract(row), {
                        "vendor": vendor, "selector_type": selector,
                        "runtime_model": alias, "runtime_effort": effort, "cli_flag": flag,
                    })
                    self.assertEqual(row, before)

    def test_unknown_id_has_no_selector_contract(self):
        with patch.dict(build_overlay.PROVEN, {}, clear=True):
            self.assertIsNone(transport_compat.selector_contract({"id": "unknown"}))


if __name__ == "__main__":
    unittest.main()
