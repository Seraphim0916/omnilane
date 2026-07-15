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
