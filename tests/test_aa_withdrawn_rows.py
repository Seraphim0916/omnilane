"""A row the benchmark withdrew is unscored everywhere: registry, aliases, chains, probe plan."""
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import build_overlay  # noqa: E402

WITHDRAWN = "claude/claude-sonnet-5-5-low"


class WithdrawnRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((REPO / "config" / "aa-model-policy.json").read_text())
        cls.scored = {row["id"] for row in cls.registry["scored_configs"]}
        cls.chains = {}
        for line in (REPO / "routing.yaml").read_text().splitlines():
            match = re.match(r"^([a-z][a-z-]*):\s*(.+)$", line.split("#")[0].rstrip())
            if match:
                cls.chains[match.group(1)] = [" ".join(segment.split()) for segment in match.group(2).split("|")]

    def test_withdrawn_row_is_unknown_and_not_an_alias_candidate(self):
        self.assertNotIn(WITHDRAWN, self.scored)
        unknown = [row for row in self.registry["unknown_configs"]
                   if (row["vendor"], row["model"], row["effort"]) == ("claude", "claude-sonnet-5-5", "low")]
        self.assertEqual(len(unknown), 1)
        self.assertIsNone(unknown[0]["score"])
        for alias in self.registry["aliases"]:
            self.assertNotIn(WITHDRAWN, alias["candidate_config_ids"])

    def test_no_chain_names_the_withdrawn_row(self):
        for lane, chain in self.chains.items():
            self.assertNotIn("claude claude-sonnet-5-5 low", chain, lane)

    def test_every_provable_row_is_scored(self):
        # build_overlay indexes the registry by every PROVEN id; an unscored one aborts resign.
        self.assertEqual(sorted(set(build_overlay.PROVEN) - self.scored), [])
        self.assertIn("grok/grok-4-7-low", build_overlay.PROVEN)
        self.assertIn("grok/grok-4-7-low", self.scored)

    def test_fast_agentic_reaches_the_higher_scored_low_row_first(self):
        chain = self.chains["fast-agentic"]
        self.assertLess(chain.index("codex gpt-6.1-sol low"), chain.index("codex gpt-6-sol low"))


if __name__ == "__main__":
    unittest.main()
