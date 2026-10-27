import offline_env  # Activate suite isolation for direct file execution.

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import refusal_evidence as re


class RefusalSurrogateTests(unittest.TestCase):
    def check_main(self, text, enabled=True):
        refusal = dict(allowed=False, failed_gate=text, code=text,
                       reason=text, message=text)
        out, err = io.BytesIO(), io.BytesIO()
        with tempfile.TemporaryDirectory() as home, \
                io.TextIOWrapper(out, encoding="utf-8", errors="strict") as stdout, \
                io.TextIOWrapper(err, encoding="utf-8", errors="strict") as stderr, \
                patch.dict(os.environ, {"OMNILANE_REFUSAL_EVIDENCE": "1" if enabled else "0"}), \
                patch.object(sys, "stdin", io.StringIO(
                    '{"allowed":false,"message":"older"}\n' + json.dumps(refusal))), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(re.main(["--home", home, "--lane", text,
                                      "--headline", text]), 0)
            stdout.flush()
            stderr.flush()
            report = err.getvalue().decode("utf-8")
            self.assertIn("omnilane refusal:", report)
            self.assertIn(text.encode("utf-8", "backslashreplace").decode("utf-8"), report)
            if enabled:
                path = Path(out.getvalue().decode("utf-8").strip())
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(record["refusal"], refusal)
                self.assertEqual(record["lane"], text)
                self.assertEqual(record["headline"], text)
                self.assertIn("evidence=" + str(path), report)
            else:
                self.assertEqual(out.getvalue(), b"")

    def test_lone_high_and_low_surrogates_round_trip(self):
        self.check_main("拒絕\ud800與\udfff")

    def test_summary_safe_even_when_storage_disabled(self):
        self.check_main("拒絕\ud800與\udfff", enabled=False)

    def test_normal_chinese_round_trip(self):
        self.check_main("拒絕：缺少呼叫者資訊")


if __name__ == "__main__":
    unittest.main()
