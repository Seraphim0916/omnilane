"""C2-B484: clean() folds whitespace, refuses blank text, and keeps at most TEXT_LIMIT characters (not bytes)."""

from __future__ import annotations
import offline_env  # noqa: F401
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import self_exec


class CleanBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"OMNILANE_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)

    def test_limit_is_500(self):
        self.assertEqual(self_exec.TEXT_LIMIT, 500)

    def test_length_boundaries(self):
        for length, expected in ((499, 499), (500, 500), (501, 500)):
            with self.subTest(length=length):
                text = "a" * (length - 1) + "z"
                value = self_exec.clean(text, "reason")
                self.assertEqual(len(value), expected)
                self.assertEqual(value, text[:500])

    def test_multibyte_cut_by_character(self):
        value = self_exec.clean("  " + "苗" * 501 + " \n", "reason")
        self.assertEqual(value, "苗" * 500)
        self.assertEqual(len(value), 500)
        self.assertEqual(len(value.encode("utf-8")), 1500)

    def test_mixed_width_cut_keeps_whole_characters(self):
        value = self_exec.clean("a" + "苗" * 600, "task")
        self.assertEqual(value, "a" + "苗" * 499)
        value.encode("utf-8").decode("utf-8")

    def test_whitespace_folds_to_single_spaces(self):
        self.assertEqual(self_exec.clean("  one   two\t\tthree\nfour\r\n five  ", "reason"),
                         "one two three four five")

    def test_folding_happens_before_the_cut(self):
        value = self_exec.clean("x" + " \t\n" * 300 + "y" * 600, "reason")
        self.assertEqual(value, "x " + "y" * 498)

    def test_blank_is_refused(self):
        for text in ("", "   ", "\t\n \r\n"):
            with self.subTest(text=text):
                with self.assertRaises(SystemExit) as raised:
                    self_exec.clean(text, "reason")
                self.assertIn("--reason must not be empty", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
