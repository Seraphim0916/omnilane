import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "c22_b183_transport", Path(__file__).resolve().parents[1] / "scripts/lib/transport_compat.py")
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class RequestMismatchNoIOTests(unittest.TestCase):
    def test_pass_descriptor_mismatch_never_reads_or_judges_evidence(self):
        expected = ["fixture-cli", "--model", "fixture-model", "--effort", "high"]
        variants = (
            ["fixture-cli", "--model", "other-model", "--effort", "high"],
            ["fixture-cli", "--model", "fixture-model", "--effort", "low"],
            ["fixture-cli", "--effort", "high", "--model", "fixture-model"],
        )
        row = dict(vendor="claude", effort="high")
        selector = dict(vendor="claude", runtime_model="fixture-model", runtime_effort="high")
        files = {key: {"path": "/fixture/" + key}
                 for key in ("stdout", "stderr", "rollout", "cli_log")}
        for actual in variants:
            with self.subTest(command=actual):
                command = Mock(return_value=expected)
                verdict = Mock(side_effect=AssertionError("must not judge mismatched evidence"))
                contextual = Mock(side_effect=AssertionError("must not classify mismatched evidence"))
                modules = {
                    "probe": SimpleNamespace(verdict=verdict),
                    "probe_sweep": SimpleNamespace(command=command, TOKENS={"claude": "FIXTURE_OK"}),
                    "probe_failure": SimpleNamespace(contextual_failure=contextual),
                }
                descriptor = dict(verdict="pass", command=actual, exit_code=0,
                                  timed_out=False, observed_model="stale-model")
                with patch.dict("sys.modules", modules), patch.object(
                        Path, "read_text", side_effect=AssertionError("must not read old evidence")) as read:
                    result = transport.checked_descriptor(row, selector, files, descriptor)
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["verdict_reason"], "source-request-contract-mismatch")
                self.assertIsNone(result["observed_model"])
                self.assertEqual(result["evidence_tier"], "selector-only")
                self.assertEqual(result["command"], actual)
                self.assertEqual(descriptor["verdict"], "pass")
                command.assert_called_once_with("claude", "fixture-model", "high")
                read.assert_not_called()
                verdict.assert_not_called()
                contextual.assert_not_called()


if __name__ == "__main__":
    unittest.main()
