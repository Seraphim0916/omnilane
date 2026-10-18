"""Retired agy selectors must not return via probes, carry, or menus."""
import offline_env  # noqa: F401
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import build_overlay
import transport_compat

RETIRED = {"gemini/gemini-3-6-flash", "gemini/gemini-3-7-flash",
           "gemini/gemini-3-7-flash-medium", "gemini/gemini-3-7-flash-low"}


class RetiredAgyTests(unittest.TestCase):
    def test_probe_candidates(self):
        self.assertFalse(RETIRED & build_overlay.PROVEN.keys())

    def test_configure_menu(self):
        menu = next(line for line in (ROOT / "scripts/configure.sh").read_text().splitlines()
                    if line.startswith("GEMINI_MODELS="))
        self.assertNotRegex(menu, r"gemini-3\.[67]-flash-(?:high|medium|low)")
        self.assertIn("gemini-3.8-flash-high", menu)

    def test_transport_carry_is_retired_in_every_snapshot(self):
        archive = json.loads(transport_compat.ARCHIVE.read_text())
        for snapshot in archive["snapshots"].values():
            # Historical rows remain present, but cannot grant transport reuse.
            for cid in RETIRED:
                with self.subTest(cid=cid):
                    self.assertIsNone(snapshot["selectors"].get(cid))


class RetiredGrokTests(unittest.TestCase):
    def test_probe_candidates_and_transport_carry(self):
        retired = {"grok/grok-4-5", "grok/grok-4-6", "grok/grok-4-6-low",
                   "grok/grok-4-6-medium", "grok/grok-4-6-xhigh"}
        self.assertFalse(retired & build_overlay.PROVEN.keys())
        archive = json.loads(transport_compat.ARCHIVE.read_text())
        for snapshot in archive["snapshots"].values():
            for cid in retired:
                with self.subTest(snapshot=snapshot.get("id"), cid=cid):
                    self.assertIsNone(snapshot["selectors"].get(cid))

    def test_native_menu_and_routes(self):
        menu = next(line for line in (ROOT / "scripts/configure.sh").read_text().splitlines()
                    if line.startswith("GROK_MODELS="))
        self.assertNotRegex(menu, r"grok-4\.[56]")
        self.assertIn("grok-4.7", menu)
        self.assertNotRegex((ROOT / "routing.yaml").read_text(), r"grok-4\.[56]")

