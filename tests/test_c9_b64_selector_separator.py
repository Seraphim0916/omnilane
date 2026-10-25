import offline_env  # Activate suite isolation for direct file execution.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from caller_identity import read_selector


class SelectorSeparatorTests(unittest.TestCase):
    def test_payload_flags_cannot_override_selector(self):
        for payload in (["--model=payload", "--effort=max"],
                        ["--model", "payload", "--effort", "max"]):
            with self.subTest(payload=payload):
                self.assertEqual(read_selector([
                    "claude", "--model", "actual", "--effort", "high", "--", *payload
                ]), ("claude", "actual", "high"))

    def test_last_flag_before_separator_wins(self):
        for suffix in ([], ["--", "--model=payload", "--effort=max"]):
            with self.subTest(suffix=suffix):
                self.assertEqual(read_selector([
                    "claude", "--model", "first", "--model=actual",
                    "--effort=low", "--effort", "high", *suffix
                ]), ("claude", "actual", "high"))

    def test_flags_only_in_payload_are_absent(self):
        self.assertEqual(read_selector([
            "claude", "--", "--model=payload", "--effort=max"
        ]), ("claude", None, None))

    def test_shared_parser_preserves_short_aliases(self):
        for vendor in ("codex", "grok", "agy"):
            with self.subTest(vendor=vendor):
                self.assertEqual(read_selector([
                    vendor, "--model=first", "-m", "actual", "--", "-m=payload"
                ]), ("gemini" if vendor == "agy" else vendor, "actual", None))


if __name__ == "__main__":
    unittest.main()
