import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from cli_provenance import family


class InstallFamilyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / "install-family-fixture"
        self.downloads = self.root / ".grok" / "downloads"

    def test_release_metadata_preserves_platform_architecture_and_unknown_suffix(self):
        versions = ("1.2.3", "v1.2.3", "1.2.3-rc.1", "1.2.4+build.7",
                    "1.2.3-alpha.1", "1.2.3-beta.2", "1.2.3-dev.3",
                    "1.2.3-pre.4", "1.2.3-preview.5", "1.2.3-canary.6",
                    "1.2.3-nightly.7", "1.2.3-rc.1+build.7")
        suffixes = ("", "-macos-aarch64", "-macos-x86_64", "-linux-aarch64",
                    "-unknown-macos-aarch64", "-custom.7-macos-aarch64")
        for version in versions:
            for suffix in suffixes:
                with self.subTest(version=version, suffix=suffix):
                    original = self.downloads / ("grok-" + version + suffix)
                    expected = str(self.downloads / ("grok-<version>" + suffix))
                    normalized = family(original)
                    self.assertEqual(normalized, expected)
                    self.assertEqual(family(normalized), normalized)

    def test_different_platforms_architectures_and_locations_do_not_merge(self):
        paths = (
            self.downloads / "grok-1.2.3-rc.1-macos-aarch64",
            self.downloads / "grok-1.2.3-macos-x86_64",
            self.downloads / "grok-1.2.3-linux-aarch64",
            self.downloads / "grok-1.2.3-unknown-macos-aarch64",
            self.root / "other" / ".grok/downloads/grok-1.2.3-macos-aarch64",
        )
        self.assertEqual(len({family(path) for path in paths}), len(paths))

    def test_unsupported_install_location_stays_unchanged(self):
        path = str(self.root / "bin/grok-1.2.3-rc.1-macos-aarch64")
        self.assertEqual(family(path), path)
        self.assertEqual(family(family(path)), path)


if __name__ == "__main__":
    unittest.main()
