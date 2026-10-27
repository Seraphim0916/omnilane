import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class ReasonPrecedenceTests(unittest.TestCase):
    def test_competing_failures_preserve_priority(self):
        cases = (
            ("login_over_gate", "unprobeable", False, "/fixture/codex", "needs-login-session"),
            ("gate_over_incomplete", "incomplete", False, "/fixture/codex", "review-executable-source"),
            ("missing_cli", "incomplete", False, None, "cli-not-installed"),
            ("login_over_missing_cli", "unprobeable", False, None, "needs-login-session"),
            ("incomplete_without_gate_failure", "incomplete", True, "/fixture/codex", "provider-verification-incomplete"),
        )
        for label, outcome, allowed, cli, expected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary).resolve()
                with patch.dict(os.environ, {"OMNILANE_HOME": str(home)}):
                    status.record(20, ["codex"], {
                        "held": ["codex"],
                        "vendors": {"codex": {"cli": cli, "gate": {"allowed": allowed}}},
                        "sweeps": {"codex": {"outcome": outcome}},
                    })
                    pending = status.load()["pending"]
                    self.assertEqual(set(pending), {"codex"})
                    self.assertEqual(pending["codex"]["reason"], expected)
                    self.assertEqual(pending["codex"]["exit_code"], 20)


if __name__ == "__main__":
    unittest.main()
