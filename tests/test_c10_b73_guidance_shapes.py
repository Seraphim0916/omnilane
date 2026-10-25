import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GuidanceShapesTests(unittest.TestCase):
    def forward(self, raw):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/lib/aa_lanes.py"),
             "--registry", str(ROOT / "config/aa-model-policy.json")],
            input=raw, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_non_objects_forward_verbatim_with_existing_newline_rule(self):
        for value in ("[]", '[1, {"allowed": false}]', "null", "true", "false",
                      "0", "-3.5", '"拒絕說明"'):
            for ending in ("", "\n", "\n\n", " \t"):
                raw = " \t" + value + ending
                with self.subTest(raw=raw):
                    self.assertEqual(self.forward(raw),
                                     raw if raw.endswith("\n") else raw + "\n")

    def test_invalid_json_still_forwards_original(self):
        for raw in ("", "not JSON", "{\n"):
            with self.subTest(raw=raw):
                self.assertEqual(self.forward(raw),
                                 raw if raw.endswith("\n") else raw + "\n")

    def test_objects_keep_normal_serialization(self):
        self.assertEqual(self.forward(' { "allowed": true, "reason": "原文" } '),
                         '{"allowed":true,"reason":"原文"}\n')

    def test_refused_object_still_gets_guidance(self):
        decision = {"allowed": False, "effective_ceiling": 0, "reason": "refused"}
        output = self.forward(json.dumps(decision))
        self.assertEqual(json.loads(output), dict(decision, eligible_lanes=[]))
        self.assertTrue(output.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
