import offline_env  # noqa: F401

import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import self_exec


class SelfExecUnicodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.log = self.home / self_exec.LOG_NAME
        self.cutoff = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

    def entry(self, lane, identifier="one"):
        return {"id": identifier, "recorded_at": "2026-01-01T00:00:00Z",
                "lane": lane, "reason": "unchanged", "schema_version": 1,
                "refusal": {"code": "test", "failed_gate": "test"}}

    def write_entries(self, entries, separator="\n"):
        text = separator.join(json.dumps(entry, ensure_ascii=False) for entry in entries)
        self.log.write_bytes((text + separator).encode("utf-8"))

    def test_unicode_separators_preserve_every_field(self):
        for character in ("\u0085", "\u2028", "\u2029"):
            with self.subTest(codepoint=f"U+{ord(character):04X}"):
                entry = self.entry("a" + character + "b")
                self.write_entries([entry])
                self.assertEqual(self_exec.records(self.cutoff), [entry])

    def test_adjacent_records_with_lf_and_crlf(self):
        entries = [self.entry("first"), self.entry("second", "two")]
        for separator in ("\n", "\r\n"):
            with self.subTest(separator=repr(separator)):
                self.write_entries(entries, separator)
                self.assertEqual(self_exec.records(self.cutoff), entries)

    def test_bad_lines_are_skipped(self):
        entry = self.entry("valid")
        self.log.write_bytes(("{broken\n\n[]\n" + json.dumps(entry) + "\n{bad\n").encode("utf-8"))
        self.assertEqual(self_exec.records(self.cutoff), [entry])

    def test_missing_file_returns_empty(self):
        self.assertFalse(self.log.exists())
        self.assertEqual(self_exec.records(self.cutoff), [])

    def test_bare_cr_is_not_a_record_boundary(self):
        entries = [self.entry("first"), self.entry("second", "two")]
        self.write_entries(entries, "\r")
        self.assertEqual(self_exec.records(self.cutoff), [])


if __name__ == "__main__":
    unittest.main()
