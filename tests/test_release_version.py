#!/usr/bin/env python3
"""Keep every user-visible release version on one source of truth."""

import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"


class ReleaseVersionTests(unittest.TestCase):
    def version(self):
        self.assertTrue(VERSION_FILE.is_file(), "VERSION file is required")
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        return version

    def test_plugin_manifests_match_version(self):
        version = self.version()
        for relative in ("plugin.json", ".claude-plugin/plugin.json"):
            with self.subTest(manifest=relative):
                data = json.loads((ROOT / relative).read_text(encoding="utf-8"))
                self.assertEqual(data["version"], version)

    def test_cli_reports_version(self):
        version = self.version()
        result = subprocess.run(
            [str(ROOT / "bin" / "omnilane"), "--version"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"omnilane {version}")

    def test_changelog_has_release_and_compare_link(self):
        version = self.version()
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(
            changelog,
            re.compile(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", re.MULTILINE),
        )
        self.assertIn(
            f"[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v{version}...HEAD",
            changelog,
        )

    def test_localized_readmes_advertise_version(self):
        version = re.escape(self.version())
        headings = {
            "README.md": rf"^## What's new in v{version}$",
            "README.zh-TW.md": rf"^## v{version} 新功能$",
            "README.zh-CN.md": rf"^## v{version} 新功能$",
            "README.ja.md": rf"^## v{version} の新機能$",
            "README.ko.md": rf"^## v{version} 새 기능$",
        }
        for relative, pattern in headings.items():
            with self.subTest(readme=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(content, re.compile(pattern, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
