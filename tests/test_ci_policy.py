#!/usr/bin/env python3
"""Static least-privilege contract for the GitHub Actions workflow."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class WorkflowPolicyTests(unittest.TestCase):
    def test_token_is_read_only_and_stale_runs_are_cancelled(self):
        self.assertRegex(
            WORKFLOW,
            re.compile(r"^permissions:\n  contents: read$", re.MULTILINE),
        )

    def test_third_party_actions_are_pinned_to_full_commits(self):
        uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", WORKFLOW, re.MULTILINE)
        self.assertTrue(uses, "workflow should contain at least one action")
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertRegex(
            WORKFLOW,
            re.compile(
                r"^concurrency:\n"
                r"  group: \$\{\{ github\.workflow \}\}-\$\{\{ github\.ref \}\}\n"
                r"  cancel-in-progress: true$",
                re.MULTILINE,
            ),
        )


if __name__ == "__main__":
    unittest.main()
