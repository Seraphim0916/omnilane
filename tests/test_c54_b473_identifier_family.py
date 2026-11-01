"""Signing identifier normalization blanks versions, keeps other text and is stable."""
import offline_env  # noqa: F401
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance as provenance


class IdentifierFamilyTests(unittest.TestCase):
    CASES = (
        ("vendor.1.2.3.bridge.4.5", "vendor.<version>.bridge.<version>"),
        ("vendor.1.2.3-beta.1", "vendor.<version>"),
        ("vendor.1.2.3+build.7", "vendor.<version>"),
        ("vendor.1.2.3-rc.1+build.7", "vendor.<version>"),
        ("vendor-1.2.3", "vendor-<version>"),
        ("vendor.bridge", "vendor.bridge"),
        ("vendor.2.bridge", "vendor.2.bridge"),
        ("vendor.v2.bridge", "vendor.v2.bridge"),
        ("vendor.2fast.bridge", "vendor.2fast.bridge"),
        ("vendor.<version>.bridge", "vendor.<version>.bridge"),
        ("vendor.<version>.bridge.1.2", "vendor.<version>.bridge.<version>"),
    )

    def test_versions_become_placeholders_and_other_text_is_kept(self):
        for identifier, expected in self.CASES:
            with self.subTest(identifier=identifier):
                self.assertEqual(provenance.identifier_family(identifier), expected)

    def test_normalizing_again_changes_nothing(self):
        for identifier, expected in self.CASES:
            with self.subTest(identifier=identifier):
                self.assertEqual(provenance.identifier_family(expected), expected)

    def test_identifiers_differing_only_by_version_share_a_family(self):
        self.assertEqual(provenance.identifier_family("vendor.1.2.3.bridge.4.5"),
                         provenance.identifier_family("vendor.9.0.bridge.10.11.12-rc.1"))


if __name__ == "__main__":
    unittest.main()
