"""Withdrawal stays fail-closed; a later capture can restore the same scored row."""
import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import copy
import io
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import build_overlay  # noqa: E402
sys.path.insert(0, str(REPO / "scripts"))
import aa_rebaseline  # noqa: E402

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

    def test_restored_row_has_score_alias_and_probe_candidate_but_no_runtime_proof(self):
        row = next(r for r in self.registry["scored_configs"] if r["id"] == WITHDRAWN)
        self.assertEqual(row["score"], 36)
        self.assertFalse(row["transport_mapping"]["runtime_verified"])
        self.assertIn(WITHDRAWN, build_overlay.PROVEN)
        alias = next(a for a in self.registry["aliases"] if a["catalog_model"] == "claude-sonnet-5-5")
        self.assertIn(WITHDRAWN, alias["candidate_config_ids"])
        self.assertFalse(any(r["model"] == "claude-sonnet-5-5" and r["effort"] == "low"
                             for r in self.registry["unknown_configs"]))

    def test_withdrawal_is_idempotent_and_later_capture_can_restore_row(self):
        extract = json.loads((REPO / "docs/reports/aa-v4.3.2-extract-2026-10-08.json").read_text())
        without = copy.deepcopy(extract)
        del without["records"]["claude-sonnet-5-5-low"]
        with tempfile.TemporaryDirectory(dir=REPO / "tests") as tmp:
            output = Path(tmp) / "policy.json"
            captured = Path(tmp) / "extract.json"
            output.write_text(json.dumps(self.registry))
            captured.write_text(json.dumps(without))
            args = SimpleNamespace(extract=str(captured), base=str(output), as_of="2026-10-08",
                                   revision=1, approval="proposed")
            with patch.object(aa_rebaseline, "REGISTRY", output), contextlib.redirect_stdout(io.StringIO()):
                aa_rebaseline.cmd_build(args)
                once = output.read_bytes()
                aa_rebaseline.cmd_build(args)
                self.assertEqual(once, output.read_bytes())
                missing = json.loads(once)
                self.assertNotIn(WITHDRAWN, {r["id"] for r in missing["scored_configs"]})
                unknown = next(r for r in missing["unknown_configs"]
                               if r["model"] == "claude-sonnet-5-5" and r["effort"] == "low")
                self.assertIsNone(unknown["score"])
                self.assertFalse(unknown["authority_eligible"])
                self.assertFalse(any(WITHDRAWN in a["candidate_config_ids"] for a in missing["aliases"]))
                captured.write_text(json.dumps(extract))
                aa_rebaseline.cmd_build(args)
                self.assertIn(WITHDRAWN, {r["id"] for r in json.loads(output.read_text())["scored_configs"]})

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
