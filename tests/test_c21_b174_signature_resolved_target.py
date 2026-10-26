import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance as cp


class SignatureResolvedTargetTests(unittest.TestCase):
    def test_header_and_all_signature_stages_use_resolved_target(self):
        alias = "input-alias-fixture"
        target = MagicMock()
        target.__str__.return_value = "resolved-target-fixture"
        stream = target.open.return_value.__enter__.return_value
        stream.read.return_value = b"\xcf\xfa\xed\xfe"
        runner = Mock(side_effect=[
            SimpleNamespace(returncode=0, stderr=""),
            SimpleNamespace(returncode=0, stderr="Identifier=test.fixture\nTeamIdentifier=TEST000001\n"),
            SimpleNamespace(returncode=0, stderr=""),
        ])
        with patch.object(cp, "Path") as path, patch.object(sys, "platform", "darwin"):
            path.return_value.resolve.return_value = target
            result = cp.facts(alias, runner)
        path.assert_called_once_with(alias)
        path.return_value.resolve.assert_called_once_with(strict=True)
        path.return_value.open.assert_not_called()
        target.open.assert_called_once_with("rb")
        stream.read.assert_called_once_with(4)
        target.open.return_value.__exit__.assert_called_once()
        requirement = ('=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] '
                       'exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
                       'and certificate leaf[subject.OU] = "TEST000001"')
        self.assertEqual(runner.call_args_list, [
            call(["codesign", "--verify", "--strict", str(target)],
                 capture_output=True, text=True, timeout=15),
            call(["codesign", "-dv", "--verbose=2", str(target)],
                 capture_output=True, text=True, timeout=15),
            call(["codesign", "--verify", "--strict", "-R", requirement, str(target)],
                 capture_output=True, text=True, timeout=15),
        ])
        self.assertEqual(result, dict(kind="developer-id", signer="TEST000001",
                                      identifier="test.fixture", valid=True))

    def test_resolution_failure_never_opens_or_runs(self):
        for error in (FileNotFoundError("missing target"), PermissionError("unreadable target")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(cp, "Path") as path, patch.object(sys, "platform", "darwin"):
                path.return_value.resolve.side_effect = error
                runner = Mock()
                result = cp.facts("input-alias-fixture", runner)
                path.return_value.resolve.assert_called_once_with(strict=True)
                path.return_value.open.assert_not_called()
                runner.assert_not_called()
                self.assertEqual(result["kind"], "unverifiable")
                self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
