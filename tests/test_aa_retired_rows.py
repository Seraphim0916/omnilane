"""Operator retirements keep a still-listed AA row unscored; gpt-5.4-mini is unscored by AA.

The 2026-10-02 gpt-5.4-mini retirement was reversed on 2026-10-18, but AA v5.0.1
(2026-11-10) gives the rows no score, so they sit in unknown_configs as AA withdrawals,
not operator retirements, and no lane or probe candidate lists them.
The retirement mechanism stays, so it is exercised here with a patched RETIRED_ROWS.
"""
import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import aa_rebaseline  # noqa: E402
import build_overlay  # noqa: E402
import probe_sweep  # noqa: E402

MINI = ("codex/gpt-5-4-mini", "codex/gpt-5-4-mini-medium",
        "codex/gpt-5-4-mini-non-reasoning")
REASON = "HTTP 400: not supported when using Codex with a ChatGPT account"
EXTRACT = REPO / "docs/reports/aa-v4.3.2-extract-2026-10-27.json"
CURRENT_EXTRACT = REPO / "docs/reports/aa-v5.0.1-extract-2026-11-10.json"
TERRA = tuple(f"codex/gpt-6-1-terra{suffix}" for suffix in
              ("", "-xhigh", "-high", "-medium", "-low", "-non-reasoning"))


class RetiredRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((REPO / "config/aa-model-policy.json").read_text())

    def test_gpt_5_4_mini_rows_are_unknown_because_aa_gives_no_score(self):
        self.assertEqual(aa_rebaseline.RETIRED_ROWS, {})
        self.assertFalse(set(MINI) & {r["id"] for r in self.registry["scored_configs"]})
        self.assertFalse([r for r in self.registry["scored_configs"] if r["model"] == "gpt-5.4-mini"])
        unknown = [r for r in self.registry["unknown_configs"] if r["model"] == "gpt-5.4-mini"]
        self.assertEqual(sorted((r["effort"] or "", r["reasoning"]) for r in unknown),
                         [("", "non-reasoning"), ("medium", "reasoning"), ("xhigh", "reasoning")])
        for row in unknown:
            self.assertEqual(row["vendor"], "codex")
            self.assertIsNone(row["score"])
            self.assertIsNone(row["estimated"])
            self.assertEqual(row["status"], "unknown")
            self.assertFalse(row["authority_eligible"])
            self.assertIn("AA v5.0.1 no longer lists gpt-5-4-mini", row["reason"])
            self.assertFalse(row["transport_mapping"]["runtime_verified"])
        alias = next(a for a in self.registry["aliases"]
                     if (a["catalog_vendor"], a["catalog_model"]) == ("codex", "gpt-5.4-mini"))
        self.assertEqual(alias["candidate_config_ids"], [])
        self.assertIsNone(alias["resolved_config_id"])
        self.assertFalse(alias["runtime_verified"])

    def test_no_lane_or_probe_candidate_lists_them(self):
        self.assertNotIn("gpt-5.4-mini", (REPO / "routing.yaml").read_text())
        self.assertNotIn("gpt-5-4-mini", (REPO / "routing.yaml").read_text())
        self.assertFalse(set(MINI) & set(build_overlay.PROVEN))
        self.assertFalse(any(model == "gpt-5.4-mini" for _, model, _ in build_overlay.PROVEN.values()))
        self.assertFalse(any(entry["model"] == "gpt-5.4-mini" for entry in probe_sweep.plan("codex")))

    def test_build_retires_still_listed_rows_and_is_repeatable(self):
        # gpt-5.4-mini has no AA v5.0.1 score, so the mechanism is exercised on a model AA
        # still scores: every GPT-6.1 Terra row, which is also the whole of its alias.
        records = json.loads(CURRENT_EXTRACT.read_text())["records"]
        self.assertTrue(all(cid.split("/")[1] in records for cid in TERRA))
        retired = {cid: ("2026-10-02", REASON) for cid in TERRA}
        new_rows = [row for row in aa_rebaseline.NEW_ROWS if row[0] not in retired]
        new_aliases = {k: v for k, v in aa_rebaseline.NEW_ALIASES.items() if k != "gpt-6.1-terra"}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.json"
            path.write_bytes(aa_rebaseline.dump(self.registry))
            args = SimpleNamespace(extract=str(CURRENT_EXTRACT), base=str(path), as_of="2026-11-10",
                                   revision=1, approval="proposed")
            with patch.object(aa_rebaseline, "RETIRED_ROWS", retired), \
                    patch.object(aa_rebaseline, "NEW_ROWS", new_rows), \
                    patch.object(aa_rebaseline, "NEW_ALIASES", new_aliases), \
                    patch.object(aa_rebaseline, "REGISTRY", path), \
                    contextlib.redirect_stdout(io.StringIO()):
                path.write_bytes(aa_rebaseline.dump(self.registry))
                self.assertEqual(aa_rebaseline.cmd_build(args), 0)
                first = path.read_bytes()
                path.write_bytes(aa_rebaseline.dump(self.registry))
                self.assertEqual(aa_rebaseline.cmd_build(args), 0)
                self.assertEqual(path.read_bytes(), first)
            result = json.loads(first)
        self.assertFalse(set(TERRA) & {r["id"] for r in result["scored_configs"]})
        unknown = [r for r in result["unknown_configs"] if r["model"] == "gpt-6.1-terra"]
        self.assertEqual({r["effort"] for r in unknown}, {"max", "xhigh", "high", "medium", "low", None})
        for row in unknown:
            self.assertIsNone(row["score"])
            self.assertFalse(row["authority_eligible"])
            self.assertIn("2026-10-02", row["reason"])
            self.assertIn(REASON, row["reason"])
            self.assertNotIn("no longer lists", row["reason"])
        self.assertFalse(any((a["catalog_vendor"], a["catalog_model"]) == ("codex", "gpt-6.1-terra")
                             for a in result["aliases"]))
        scores = lambda rows: [(r["id"], r["score"], r["score_raw"], r["estimated"])
                               for r in rows if r["id"] not in TERRA]
        self.assertEqual(scores(result["scored_configs"]), scores(self.registry["scored_configs"]))

    def test_retired_row_cannot_be_added_by_new_rows(self):
        retired = {MINI[0]: ("2026-10-02", REASON)}
        args = SimpleNamespace(extract=str(EXTRACT), base=str(REPO / "config/aa-model-policy.json"),
                               as_of="2026-10-18", revision=1, approval="proposed")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "must-not-be-written.json"
            with patch.object(aa_rebaseline, "RETIRED_ROWS", retired), \
                    patch.object(aa_rebaseline, "REGISTRY", path):
                with self.assertRaisesRegex(SystemExit, "retired.*NEW_ROWS"):
                    aa_rebaseline.cmd_build(args)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
