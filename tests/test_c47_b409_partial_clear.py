import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class PartialClearTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"OMNILANE_HOME": str(home)})
        environment.start()
        self.addCleanup(environment.stop)
        status.record(20, ["codex", "grok"])
        self.grok_before = status.load()["pending"]["grok"]

    def assert_pending(self, expected):
        pending = status.load()["pending"]
        self.assertEqual(set(pending), expected)
        self.assertEqual(pending["grok"], self.grok_before)
        return pending

    def test_each_retention_condition_independently(self):
        for condition in ("held", "deferred_retries", "failed_retries", "still_failed_environment"):
            with self.subTest(condition=condition):
                summary = {"held": ["grok"]}
                if condition == "held":
                    summary["held"].append("codex")
                else:
                    summary[condition] = {"codex": []}
                before = status.load()["pending"]["codex"]["attempts"]
                status.record(20, ["codex"], summary)
                pending = self.assert_pending({"codex", "grok"})
                self.assertEqual(pending["codex"]["attempts"], before + 1)

    def test_clear_only_selected_vendor_when_no_retention_matches(self):
        status.record(20, ["codex"], {
            "held": ["grok"],
            "deferred_retries": {"grok": []},
            "failed_retries": {"grok": []},
            "still_failed_environment": {"grok": []},
        })
        self.assert_pending({"grok"})

    def test_empty_held_does_not_clear(self):
        status.record(20, ["codex"], {"held": []})
        pending = self.assert_pending({"codex", "grok"})
        self.assertEqual(pending["codex"]["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
