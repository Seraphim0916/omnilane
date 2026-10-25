import offline_env  # Activate suite isolation for direct file execution.
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import probe
import probe_failure


class StreamTests(unittest.TestCase):
    def test_environment_error_in_either_stream(self):
        for kind in ("invalid-json", "invalid-json-result"):
            for stdout, stderr in (("Please run /login", ""),
                                   ("Please run /login", "startup warning"),
                                   ("startup warning", "Please run /login")):
                with self.subTest(kind=kind, stderr=stderr):
                    verdict = ("fail", kind, "observed", "tier")
                    result = probe_failure.contextual_failure(verdict, {"exit_code": 1}, stdout, stderr)
                    self.assertEqual(result, ("fail", "result-error: Please run /login", "observed", "tier"))

    def test_guards_preserve_original_verdict(self):
        cases = [("fail", reason, 1) for reason in probe_failure.CONCLUSIVE_REASONS]
        cases += [("pass", "invalid-json", 1), ("fail", "invalid-json", 0),
                  ("fail", "missing-token", 1)]
        for status, reason, code in cases:
            with self.subTest(status=status, reason=reason, code=code):
                verdict = (status, reason, None, None)
                self.assertEqual(probe_failure.contextual_failure(
                    verdict, {"exit_code": code}, "Please run /login", "startup warning"), verdict)

    def test_generic_warnings_remain_invalid_json(self):
        verdict = ("fail", "invalid-json", None, None)
        self.assertEqual(probe_failure.contextual_failure(
            verdict, {"exit_code": 1}, "ordinary output", "startup warning"), verdict)

    def test_real_proof_engine_then_contextual_classification(self):
        descriptor = {"exit_code": 1, "timed_out": False}
        verdict = probe.verdict(descriptor, "Please run /login", "startup warning", "claude", "fixture-token")
        self.assertEqual(verdict[:2], ("fail", "invalid-json"))
        classified = probe_failure.contextual_failure(
            verdict, descriptor, "Please run /login", "startup warning")
        self.assertEqual(classified[:2], ("fail", "result-error: Please run /login"))


if __name__ == "__main__":
    unittest.main()
