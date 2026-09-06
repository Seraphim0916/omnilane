#!/usr/bin/env python3
"""Offline contract checks for the full AA/catalog coverage inventory."""

import json
import math
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COVERAGE = ROOT / "docs" / "aa-model-coverage-2026-09-05.json"
CONFIGURE = ROOT / "scripts" / "configure.sh"
CAPABILITIES = ROOT / "docs" / "model-capabilities-2026-09.md"
ARRAYS = {
    "codex": "CODEX_MODELS",
    "claude": "CLAUDE_MODELS",
    "gemini": "GEMINI_MODELS",
    "grok": "GROK_MODELS",
    "kimi": "KIMI_MODELS",
    "qwen": "QWEN_MODELS",
    "opencode": "OPENCODE_MODELS",
    "openrouter": "OPENROUTER_MODELS",
    "deepseek": "DEEPSEEK_MODELS",
    "zai": "ZAI_MODELS",
    "mistral": "MISTRAL_MODELS",
    "groq": "GROQ_MODELS",
    "cerebras": "CEREBRAS_MODELS",
}


def configured_models():
    text = CONFIGURE.read_text()
    result = set()
    for vendor, array_name in ARRAYS.items():
        match = re.search(rf"^{array_name}=\(([^\n]*)\)", text, re.MULTILINE)
        if not match:
            raise AssertionError(f"missing {array_name}")
        result.update((vendor, value) for value in re.findall(r'"([^"]+)"', match.group(1)))
    return result


class AAModelCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(COVERAGE.read_text())
        cls.rows = {row["aaSlug"]: row for row in cls.data["aaModels"]}

    def test_full_flight_snapshot_is_unique_and_complete(self):
        self.assertEqual(self.data["sources"][0]["indexVersion"], "AA Intelligence Index v4.2")
        self.assertTrue(self.data["sources"][0]["extractionComplete"])
        self.assertEqual(self.data["counts"]["aaRows"], 643)
        self.assertEqual(len(self.data["aaModels"]), 643)
        self.assertEqual(len(self.rows), 643)

    def test_every_catalog_value_and_alias_has_mapping_or_exception(self):
        documented = {(row["vendor"], row["catalogModel"]) for row in self.data["catalogMappings"]}
        self.assertEqual(documented, configured_models())
        for row in self.data["catalogMappings"]:
            self.assertTrue(row["mappingStatus"])
            self.assertTrue(row["mappingReason"])
            self.assertIsInstance(row["aaSlugs"], list)
        for alias in self.data["namedAliases"]:
            self.assertTrue(alias["mappingStatus"])
            self.assertTrue(alias["mappingReason"])
            self.assertIsInstance(alias["aaSlugs"], list)

    def test_scores_are_numeric_or_null_and_estimates_are_retained(self):
        for row in self.data["aaModels"]:
            for key, value in row["scores"].items():
                self.assertTrue(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)))
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value))
            expected = [key for key, value in row["scores"].items() if isinstance(value, (int, float)) and not isinstance(value, bool)]
            self.assertEqual(row["availableScores"], expected)
            self.assertIn("intelligenceIndexIsEstimated", row)
        self.assertTrue(self.rows["claude-fable-5-1-high"]["intelligenceIndexIsEstimated"])
        self.assertTrue(self.rows["claude-fable-5-1-xhigh"]["intelligenceIndexIsEstimated"])
        self.assertTrue(self.rows["gpt-5-6-luna-high"]["intelligenceIndexIsEstimated"])
        self.assertIsNone(self.rows["gemini-3-8-flash"]["scores"]["medianOutputTokensPerSecond"])

    def test_mixed_cli_and_api_families_are_not_described_as_api_only(self):
        for slug in ("claude-opus-5", "claude-sonnet-5", "gpt-5-6-sol", "gpt-5-6-luna"):
            row = self.rows[slug]
            self.assertEqual(row["disposition"], "explicit-consult")
            self.assertIn("Supported CLI family", row["reason"])
            self.assertNotIn("only to advise-only API", row["reason"])
            self.assertTrue(any(match["access"] == "cli" for match in row["catalogMatches"]))

    def test_generic_codex_alias_is_explicitly_ambiguous(self):
        row = next(
            item for item in self.data["catalogMappings"]
            if item["vendor"] == "codex" and item["catalogModel"] == "gpt-5.6"
        )
        self.assertEqual(row["mappingStatus"], "ambiguous-runtime-alias")
        self.assertEqual(row["aaSlugs"], [])
        self.assertIn("no verified one-to-one", row["mappingReason"])

    def test_high_strength_aliases_map_only_to_the_high_aa_row(self):
        rows = {
            (row["vendor"], row["catalogModel"]): row
            for row in self.data["catalogMappings"]
        }
        self.assertEqual(
            rows[("gemini", "gemini-3.8-flash-high")]["aaSlugs"],
            ["gemini-3-8-flash"],
        )
        self.assertEqual(
            rows[("gemini", "gemini-3.7-flash-high")]["aaSlugs"],
            ["gemini-3-7-flash"],
        )

    def test_unspecified_runner_effort_is_not_presented_as_exact(self):
        expected = {
            "grok-4-6": "unverified",
            "kimi-k3": "unverified",
            "claude-4-5-haiku-reasoning": "default-unspecified",
            "claude-4-5-haiku": "default-unspecified",
        }
        for slug, alignment in expected.items():
            self.assertEqual(self.rows[slug]["effortAlignment"], alignment)
            self.assertIn("unverified", self.rows[slug]["reason"])

    def test_current_capability_table_uses_live_lcr_values(self):
        text = CAPABILITIES.read_text()
        self.assertIn("| Claude Opus 5 high | 52.03 | 52.91 | 76.52 | .79 |", text)
        self.assertIn("| Claude Opus 5 xhigh | 53.37 | 55.67 | 77.00 | .8033 |", text)
        self.assertIn("| GPT-5.6 Luna medium | 30.19 | 25.49 | 50.73 | .75 |", text)

    def test_new_official_api_ids_are_explicit_consult_only(self):
        expected = {
            ("zai", "glm-5.3"),
            ("deepseek", "deepseek-v4-pro"),
            ("deepseek", "deepseek-v4-flash"),
            ("mistral", "mistral-medium-3-5"),
        }
        rows = {
            (row["vendor"], row["catalogModel"]): row
            for row in self.data["catalogMappings"]
        }
        for key in expected:
            self.assertIn(key, rows)
            self.assertEqual(rows[key]["access"], "api-advise-only")
            self.assertEqual(rows[key]["disposition"], "explicit-consult")


if __name__ == "__main__":
    unittest.main()
