import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class OriginReasonAllowlistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"OMNILANE_HOME": str(home)})
        environment.start()
        self.addCleanup(environment.stop)

    def record_source(self, origin_reason):
        status.record(20, ["codex"], {
            "held": ["codex"],
            "vendors": {"codex": {
                "cli": "/fixture/codex", "gate": {"allowed": False},
                "origin_reason": origin_reason,
            }},
        })
        return status.load()["pending"]["codex"]

    def test_all_eight_allowed_source_codes_are_persisted(self):
        for reason in ("origin-unapproved", "team-changed", "identifier-changed",
                       "location-changed", "invalid", "unsigned", "unverifiable", "policy-invalid"):
            with self.subTest(reason=reason):
                entry = self.record_source(reason)
                self.assertEqual(entry["reason"], "review-executable-source")
                self.assertEqual(entry["origin_reason"], reason)

    def test_unknown_text_is_omitted_and_replaces_old_value(self):
        self.assertEqual(self.record_source("invalid")["origin_reason"], "invalid")
        unknown = "synthetic-unknown-source-detail"
        entry = self.record_source(unknown)
        self.assertEqual(entry["reason"], "review-executable-source")
        self.assertNotIn("origin_reason", entry)
        self.assertNotIn(unknown, status.status_path().read_text())

    def test_other_branches_remove_prior_origin_reason(self):
        cases = (
            ("login", 20, "unprobeable", False, "/fixture/codex", "needs-login-session"),
            ("missing_cli", 20, "incomplete", False, None, "cli-not-installed"),
            ("incomplete", 20, "incomplete", True, "/fixture/codex", "provider-verification-incomplete"),
            ("default", 20, "done", True, "/fixture/codex", "operator-or-login-needed"),
            ("success", 0, "done", True, "/fixture/codex", None),
        )
        for label, code, outcome, allowed, cli, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(self.record_source("team-changed")["origin_reason"], "team-changed")
                status.record(code, ["codex"], {
                    "held": ["codex"],
                    "vendors": {"codex": {"cli": cli, "gate": {"allowed": allowed},
                                          "origin_reason": "invalid"}},
                    "sweeps": {"codex": {"outcome": outcome}},
                })
                pending = status.load()["pending"]
                if expected is None:
                    self.assertNotIn("codex", pending)
                else:
                    self.assertEqual(pending["codex"]["reason"], expected)
                    self.assertNotIn("origin_reason", pending["codex"])


if __name__ == "__main__":
    unittest.main()
