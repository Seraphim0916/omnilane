"""Error results without text must fail through the real CLI."""
import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/lib/normalize-claude-stream.py"


class ResultErrorWithoutTextTests(unittest.TestCase):
    def test_error_cannot_reuse_earlier_success(self):
        assistant = {"type": "assistant", "message": {
            "content": [{"type": "text", "text": "partial"}]}}
        success = {"type": "result", "result": "earlier success", "is_error": False}
        for fields in ({}, {"result": None}, {"result": 42},
                       {"result": False}, {"result": []}, {"result": {}}):
            for prior in ([], [assistant], [success], [assistant, success]):
                with self.subTest(fields=fields, prior=prior), tempfile.TemporaryDirectory() as tmp:
                    events = Path(tmp) / "events.jsonl"
                    output = Path(tmp) / "output.txt"
                    error = dict(type="result", is_error=True, **fields)
                    original = "".join(json.dumps(e) + "\n" for e in [*prior, error]).encode()
                    events.write_bytes(original)
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), str(events), str(output)],
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(output.exists(), "must not emit successful output")
                    self.assertEqual(events.read_bytes(), original, "must not append success")
