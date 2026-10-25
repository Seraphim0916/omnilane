"""Canonical appends preserve bytes and remain valid JSONL on repeat runs."""
import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/lib/normalize-claude-stream.py"


class ResultAppendSeparatorTests(unittest.TestCase):
    def test_append_separator_and_repeat(self):
        for ending in (b"\n", b"\r\n", b""):
            for text in ("complete", "繁體中文：完成育苗紀錄"):
                with self.subTest(ending=ending, text=text), tempfile.TemporaryDirectory() as tmp:
                    events = Path(tmp) / "events.jsonl"
                    output = Path(tmp) / "output.txt"
                    assistant = {"type": "assistant", "message": {
                        "content": [{"type": "text", "text": text}]}}
                    original = json.dumps(assistant, ensure_ascii=False).encode() + ending
                    events.write_bytes(original)
                    command = [sys.executable, str(SCRIPT), str(events), str(output)]
                    first = subprocess.run(command, capture_output=True, text=True, timeout=10)
                    self.assertEqual(first.returncode, 0, first.stderr)
                    appended = events.read_bytes()
                    self.assertTrue(appended.startswith(original))
                    expected = {"type": "result", "is_error": False, "result": text,
                                "normalized_by": "omnilane"}
                    self.assertEqual([json.loads(line) for line in appended.splitlines()],
                                     [assistant, expected])
                    canonical = json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode()
                    self.assertEqual(appended, original + (b"" if ending else b"\n") + canonical + b"\n")
                    self.assertEqual(output.read_bytes(), (text + "\n").encode())
                    second = subprocess.run(command, capture_output=True, text=True, timeout=10)
                    self.assertEqual(second.returncode, 0, second.stderr)
                    self.assertEqual(events.read_bytes(), appended)
                    self.assertEqual(output.read_bytes(), (text + "\n").encode())
