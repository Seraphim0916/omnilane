"""Operator-retired rows stay unscored even while AA still publishes their scores."""
import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import copy
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

RETIRED = ("codex/gpt-5-4-mini", "codex/gpt-5-4-mini-medium",
           "codex/gpt-5-4-mini-non-reasoning")
EXTRACT = REPO / "docs/reports/aa-v4.3.2-extract-2026-10-08.json"


class RetiredRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((REPO / "config/aa-model-policy.json").read_text())

    def test_retired_rows_are_unknown_without_authority_or_aliases(self):
        self.assertTrue(all(cid not in {r["id"] for r in self.registry["scored_configs"]}
                            for cid in RETIRED))
        unknown = [r for r in self.registry["unknown_configs"]
                   if (r["vendor"], r["model"]) == ("codex", "gpt-5.4-mini")]
        self.assertEqual(len(unknown), 3)
        self.assertEqual({r["effort"] for r in unknown}, {"xhigh", "medium", None})
        for row in unknown:
            self.assertIsNone(row["score"])
            self.assertFalse(row["authority_eligible"])
            self.assertFalse(row["transport_mapping"]["runtime_verified"])
            self.assertIn("2026-10-02", row["reason"])
            self.assertIn("not supported when using Codex with a ChatGPT account", row["reason"])
            self.assertNotIn("no longer lists", row["reason"])
        for alias in self.registry["aliases"]:
            self.assertNotEqual((alias["catalog_vendor"], alias["catalog_model"]),
                                ("codex", "gpt-5.4-mini"))
            self.assertFalse(set(RETIRED) & set(alias["candidate_config_ids"]))

    def test_no_chain_or_probe_plan_requests_retired_rows(self):
        self.assertNotIn("gpt-5.4-mini", (REPO / "routing.yaml").read_text())
        self.assertFalse(set(RETIRED) & set(build_overlay.PROVEN))
        self.assertTrue(all(entry["model"] != "gpt-5.4-mini"
                            for entry in probe_sweep.plan("codex")))

    def test_build_retires_still_listed_rows_and_is_repeatable(self):
        records = json.loads(EXTRACT.read_text())["records"]
        self.assertTrue(all(cid.split("/")[1] in records for cid in RETIRED))
        base = copy.deepcopy(self.registry)
        base["unknown_configs"] = [r for r in base["unknown_configs"]
                                   if r["model"] != "gpt-5.4-mini"]
        prototype = next(r for r in base["scored_configs"] if r["vendor"] == "codex")
        for cid, effort in zip(RETIRED, ("xhigh", "medium", None)):
            if any(r["id"] == cid for r in base["scored_configs"]):
                continue
            row = copy.deepcopy(prototype)
            row.update(id=cid, model="gpt-5.4-mini", effort=effort, fallback=None,
                       reasoning="reasoning" if effort else "non-reasoning",
                       aa_slug=cid.split("/")[1])
            base["scored_configs"].append(row)
        if not any(a["catalog_model"] == "gpt-5.4-mini" for a in base["aliases"]):
            base["aliases"].append({"catalog_vendor": "codex", "catalog_model": "gpt-5.4-mini",
                                    "candidate_config_ids": list(RETIRED)})
        with tempfile.TemporaryDirectory(dir=REPO / ".sandbox-tmp") as temp:
            path = Path(temp) / "registry.json"
            path.write_bytes(aa_rebaseline.dump(base))
            args = SimpleNamespace(extract=str(EXTRACT), base=str(path), as_of="2026-10-08",
                                   revision=1, approval="proposed")
            with patch.object(aa_rebaseline, "REGISTRY", path), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aa_rebaseline.cmd_build(args), 0)
                first = path.read_bytes()
                self.assertEqual(aa_rebaseline.cmd_build(args), 0)
                self.assertEqual(path.read_bytes(), first)
            result = json.loads(first)
        self.assertFalse(set(RETIRED) & {r["id"] for r in result["scored_configs"]})
        self.assertEqual(len([r for r in result["unknown_configs"] if r["model"] == "gpt-5.4-mini"]), 3)
        expected = [r["id"] for r in self.registry["scored_configs"] if r["id"] not in RETIRED]
        self.assertEqual([r["id"] for r in result["scored_configs"]], expected)
        scores = lambda rows: [(r["id"], r["score"], r["score_raw"], r["estimated"])
                               for r in rows if r["id"] not in RETIRED]
        self.assertEqual(scores(result["scored_configs"]), scores(self.registry["scored_configs"]))

    def test_retired_row_cannot_be_added_by_new_rows(self):
        collision = (RETIRED[0], "codex", "gpt-5.4-mini", "xhigh", "reasoning",
                     "gpt-5-4-mini", "codex/gpt-6-astra")
        args = SimpleNamespace(extract=str(EXTRACT), base=str(REPO / "config/aa-model-policy.json"),
                               as_of="2026-10-08", revision=1, approval="proposed")
        with tempfile.TemporaryDirectory(dir=REPO / ".sandbox-tmp") as temp:
            path = Path(temp) / "must-not-be-written.json"
            with patch.object(aa_rebaseline, "NEW_ROWS", [*aa_rebaseline.NEW_ROWS, collision]), \
                    patch.object(aa_rebaseline, "REGISTRY", path):
                with self.assertRaisesRegex(SystemExit, "retired.*NEW_ROWS"):
                    aa_rebaseline.cmd_build(args)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
