import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance as cp


class SignatureDisplayFailureTests(unittest.TestCase):
    def test_display_exceptions_never_reach_requirement_check(self):
        for error in (subprocess.TimeoutExpired("codesign", 15),
                      FileNotFoundError("missing tool"), OSError("read failed")):
            with self.subTest(error=type(error).__name__):
                target = MagicMock()
                target.__str__.return_value = "resolved-fixture"
                target.open.return_value.__enter__.return_value.read.return_value = b"\xcf\xfa\xed\xfe"
                runner = Mock(side_effect=[SimpleNamespace(returncode=0, stderr=""), error])
                with patch.object(cp, "Path") as path, patch.object(sys, "platform", "darwin"):
                    path.return_value.resolve.return_value = target
                    result = cp.facts("alias-fixture", runner)
                self.assertEqual(result["kind"], "unverifiable")
                self.assertFalse(result["valid"])
                self.assertEqual(result["signer"], cp.UNSIGNED)
                self.assertEqual(runner.call_args_list, [
                    call(["codesign", "--verify", "--strict", str(target)],
                         capture_output=True, text=True, timeout=15),
                    call(["codesign", "-dv", "--verbose=2", str(target)],
                         capture_output=True, text=True, timeout=15),
                ])


if __name__ == "__main__":
    unittest.main()
