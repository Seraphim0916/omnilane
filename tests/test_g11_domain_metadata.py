"""The v1 metadata contract, including explicit missing values and source separation."""

import offline_env  # Activate suite isolation before production imports.

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import aa_domain_metadata as metadata


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "schema_version": 1,
            "snapshot": {"id": "fixture-é"},
            "policy": {"score_field": "score"},
            "scored_configs": [{"id": "codex/model", "score": 3}, {"id": "claude/model", "score": 2}],
        }
        self.row = {
            "lane": "hardest-coding",
            "measure_id": "scicode",
            "config_id": "codex/model",
            "value": 0.25,
            "unit": "ratio",
            "observed_at": "2024-02-29",
            "source_ref": "docs/extract.json#/records/model/scicode",
            "evidence_kind": "measured",
            "missing_reason": None,
        }
        self.claim = {
            "vendor": "codex", "product": "Fixture", "effort": "high",
            "summary": "Vendor claim fixture", "official_url": "https://openai.com/research",
            "verified_at": "2026-11-22", "surface": "API", "plan": "Unspecified",
            "region": "Unspecified", "limitations": "Vendor claim only",
        }
        self.companion = {
            "schema_version": 1,
            "snapshot_id": self.registry["snapshot"]["id"],
            "registry_sha256": metadata.registry_payload_sha256(self.registry),
            "measurements": [self.row],
            "vendor_claims": [self.claim],
        }

    def test_canonical_hash_excludes_only_pointer(self):
        expected_bytes = (
            '{"policy":{"score_field":"score"},"schema_version":1,'
            '"scored_configs":[{"id":"codex/model","score":3},{"id":"claude/model","score":2}],'
            '"snapshot":{"id":"fixture-é"}}'
        ).encode("utf-8")
        expected = hashlib.sha256(expected_bytes).hexdigest()
        self.registry["snapshot"]["domain_strength"] = {"path": "config/example.json"}
        self.assertEqual(metadata.registry_payload_sha256(self.registry), expected)
        self.registry["scored_configs"].reverse()
        self.assertNotEqual(metadata.registry_payload_sha256(self.registry), expected)

    def test_available_and_unknown_evidence_are_distinct(self):
        kinds = [
            dict(self.row, evidence_kind="measured", value=0),
            dict(self.row, evidence_kind="estimated", value=0.1),
            dict(self.row, evidence_kind="derived", value=0.2),
            dict(self.row, evidence_kind="unknown", value=None, missing_reason="Unpublished"),
        ]
        kinds[-1].pop("config_id")
        self.companion["measurements"] = kinds
        original = copy.deepcopy(self.companion)
        metadata.validate_companion(self.companion, self.registry)
        self.assertEqual(self.companion, original)

    def test_conflicting_values_report_the_observation_and_both_values(self):
        self.companion["measurements"].append(dict(self.row, value=0.75))
        with self.assertRaises(ValueError) as caught:
            metadata.validate_companion(self.companion, self.registry)
        message = str(caught.exception)
        for text in ("companion.measurements[1].value", "conflicting values", "0.25", "0.75",
                     "hardest-coding", "scicode", "codex/model", "2024-02-29",
                     "docs/extract.json#/records/model/scicode", "measured"):
            self.assertIn(text, message)

    def test_exact_duplicate_is_rejected_even_for_unknown_values(self):
        for row in (self.row, dict(self.row, evidence_kind="unknown", value=None,
                                   missing_reason="Unpublished")):
            with self.subTest(kind=row["evidence_kind"]):
                self.companion["measurements"] = [row, row.copy()]
                with self.assertRaisesRegex(ValueError, r"measurements\[1\]: duplicate observation"):
                    metadata.validate_companion(self.companion, self.registry)

    def test_each_observation_key_field_distinguishes_measurements(self):
        for field, other in (("lane", "coding"), ("measure_id", "gpqa"), ("config_id", None),
                             ("observed_at", "2024-03-01"), ("source_ref", "docs/other.json"),
                             ("evidence_kind", "estimated")):
            with self.subTest(field=field):
                self.companion["measurements"] = [self.row, dict(self.row, value=0.75, **{field: other})]
                metadata.validate_companion(self.companion, self.registry)

    def test_invalid_measurements_name_the_rejected_field(self):
        cases = (
            ("evidence_kind", "vendor-claim"),
            ("unit", "usd"),
            ("measure_id", "unregistered"),
            ("lane", "has spaces"),
            ("config_id", "codex/missing"),
            ("source_ref", " "),
            ("source_ref", "https://bad host.test"),
            ("source_ref", "../private.json"),
            ("observed_at", "2026-02-29"),
            ("observed_at", "2026-1-1"),
            ("value", True),
            ("value", float("inf")),
            ("value", 1.01),
            ("value", None),
            ("missing_reason", "spurious"),
            ("typo", "unexpected"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.companion["measurements"] = [dict(self.row, **{field: value})]
                with self.assertRaisesRegex(ValueError, rf"companion\.measurements\[0\]\.{field}:"):
                    metadata.validate_companion(self.companion, self.registry)
        self.companion["measurements"] = [{key: value for key, value in self.row.items()
                                          if key != "source_ref"}]
        with self.assertRaisesRegex(ValueError, "source_ref: required"):
            metadata.validate_companion(self.companion, self.registry)

    def test_unknown_requires_null_and_reason(self):
        for value, reason, field in ((0, "Unpublished", "value"), (None, " ", "missing_reason")):
            with self.subTest(field=field):
                self.companion["measurements"] = [
                    dict(self.row, evidence_kind="unknown", value=value, missing_reason=reason)
                ]
                with self.assertRaisesRegex(ValueError, rf"\.{field}:"):
                    metadata.validate_companion(self.companion, self.registry)

    def test_claim_requires_official_url_and_no_numeric_score(self):
        for url in (
            "http://openai.com", "https://", "https://openai.com.evil.test",
            "https://openai.com@evil.test", "https://openai.com:bad",
            "https://openai.com\\@evil.test", "https://anthropic.com",
        ):
            with self.subTest(url=url):
                self.companion["vendor_claims"] = [dict(self.claim, official_url=url)]
                with self.assertRaisesRegex(ValueError, r"companion\.vendor_claims\[0\]\.official_url:"):
                    metadata.validate_companion(self.companion, self.registry)
        self.companion["vendor_claims"] = [dict(self.claim, score=50)]
        with self.assertRaisesRegex(ValueError, "score: unknown field"):
            metadata.validate_companion(self.companion, self.registry)

    def test_wrong_registry_bindings_and_schema_fail_closed(self):
        for field, value in (
            ("schema_version", 2), ("schema_version", True), ("snapshot_id", "different"),
            ("registry_sha256", "f" * 64),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, rf"companion\.{field}:"):
                    metadata.validate_companion(dict(self.companion, **{field: value}), self.registry)
        for raw in (b'{"schema_version":1,"schema_version":2}', b'{"value":NaN}', b"\xff"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "companion: invalid JSON"):
                    metadata.decode_json(raw, "companion")

    def test_shipped_measurements_point_to_exact_extract_value_and_date(self):
        registry = json.loads((ROOT / "config/aa-model-policy.json").read_bytes())
        companion = json.loads((ROOT / "config/aa-domain-strength.json").read_bytes())
        metadata.validate_companion(companion, registry)
        for row in companion["measurements"]:
            with self.subTest(source_ref=row["source_ref"]):
                path, _, fragment = row["source_ref"].partition("#/")
                extract = json.loads((ROOT / path).read_bytes())
                section, slug, field = fragment.split("/")
                self.assertEqual(row["value"], extract[section][slug][field])
                source_id = extract["record_sources"][slug]["fields"][field]
                page = next(item for item in extract["sources"] if item["id"] == source_id)
                self.assertEqual(row["observed_at"], page["fetched_at"][:10])
                config = next(item for item in registry["scored_configs"] if item["id"] == row["config_id"])
                self.assertEqual(config["aa_slug"], slug)
