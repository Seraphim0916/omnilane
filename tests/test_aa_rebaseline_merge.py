#!/usr/bin/env python3
"""Offline merge provenance, sparse fields, conflicts and deterministic output."""
from __future__ import annotations

import offline_env
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("aa_merge", ROOT / "scripts/aa_rebaseline.py")
aa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aa)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aa-merge-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def page(self, slug, records, at="2026-10-08T10:00:00+08:00", version="4.3.2"):
        path = self.root / (slug + ".json")
        path.write_text(json.dumps({
            "source_url": aa.PAGE.format(slug=slug), "fetched_at": at,
            "page_sha256": hashlib.sha256(slug.encode()).hexdigest(),
            "benchmark_version": version, "records_on_page": len(records), "records": records,
        }))
        return path

    def test_own_page_wins_conflict_but_null_does_not_erase_evidence(self):
        own = self.page("model-a", {"model-a": {"intelligenceIndex": 43.4, "speed": None}})
        other = self.page("model-b", {"model-a": {"intelligenceIndex": 40, "speed": 12}},
                          at="2026-10-08T12:00:00+08:00")
        merged = aa.merge_extracts([other, own])
        self.assertEqual(merged["records"]["model-a"], {"intelligenceIndex": 43.4, "speed": 12})
        sources = {s["id"]: s["source_url"] for s in merged["sources"]}
        provenance = merged["record_sources"]["model-a"]
        self.assertEqual(sources[provenance["fields"]["intelligenceIndex"]], aa.PAGE.format(slug="model-a"))
        self.assertEqual(sources[provenance["fields"]["speed"]], aa.PAGE.format(slug="model-b"))
        self.assertEqual({c["field"] for c in merged["conflicts"]}, {"speed", "intelligenceIndex"})
        self.assertEqual({c["rule"] for c in merged["conflicts"]}, {aa.MERGE_RULE["id"]})

    def test_fallback_compares_instants_not_timezone_strings_and_keeps_missingness(self):
        earlier = self.page("page-a", {"model": {"score": 1, "nullable": None}},
                            at="2026-10-08T10:00:00+08:00")
        later = self.page("page-b", {"model": {"score": 2}},
                          at="2026-10-08T03:00:00+00:00")
        result = aa.merge_extracts([earlier, later])
        self.assertEqual(result["records"]["model"], {"score": 2, "nullable": None})
        self.assertEqual(result["fetched_at"], "2026-10-08T03:00:00+00:00")
        conflict = next(c for c in result["conflicts"] if c["field"] == "nullable")
        self.assertEqual({o["present"] for o in conflict["observations"]}, {False, True})

    def test_order_independence_deduplication_and_tie_break(self):
        a = self.page("page-a", {"model": {"score": 1}})
        b = self.page("page-b", {"model": {"score": 2}})
        first = aa.merge_extracts([a, b, a])
        self.assertEqual(aa.dump(first), aa.dump(aa.merge_extracts([b, a])))
        self.assertEqual(first["records"]["model"]["score"], 1)
        self.assertEqual(len(first["sources"]), 2)

    def test_rejects_mixed_versions_ambiguous_capture_and_missing_timezone(self):
        a = self.page("page-a", {"model": {"score": 1}})
        b = self.page("page-b", {}, version="4.2")
        with self.assertRaisesRegex(ValueError, "versions differ"):
            aa.merge_extracts([a, b])
        duplicate = self.root / "duplicate.json"
        data = json.loads(a.read_text())
        data["records"]["model"]["score"] = 2
        duplicate.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "different records"):
            aa.merge_extracts([a, duplicate])
        data["fetched_at"] = "2026-10-08T10:00:00"
        duplicate.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "timezone"):
            aa.merge_extracts([duplicate])
        with self.assertRaisesRegex(ValueError, "no page extracts"):
            aa.merge_extracts([])

    def test_all_supplied_pages_reproduce_saved_merge_and_every_selected_field(self):
        pages = sorted((ROOT / "docs/reports/aa-pages-2026-10-08").glob("*.json"))
        merged = aa.merge_extracts(pages[::-1])
        saved = ROOT / "docs/reports/aa-v4.3.2-extract-2026-10-08.json"
        self.assertEqual(aa.dump(merged), saved.read_bytes())
        self.assertEqual(len(merged["sources"]), 39)
        by_capture = {}
        for path in pages:
            page = json.loads(path.read_text())
            by_capture[(page["source_url"], page["fetched_at"], page["page_sha256"])] = page["records"]
        sources = {s["id"]: by_capture[(s["source_url"], s["fetched_at"], s["page_sha256"])]
                   for s in merged["sources"]}
        for slug, fields in merged["record_sources"].items():
            for field, source in fields["fields"].items():
                self.assertEqual(merged["records"][slug][field], sources[source][slug][field])


if __name__ == "__main__":
    unittest.main()
