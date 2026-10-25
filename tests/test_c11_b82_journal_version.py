import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import goal_dispatch


class JournalVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        self.goal_id = "20261026-000000-1-1"
        goal = home / "goals" / self.goal_id
        (goal / "jobs").mkdir(parents=True)
        self.store = goal_dispatch.Store(str(home), self.goal_id, create=True)
        self.addCleanup(self.store.close)
        self.journal = goal / "dispatch"
        self.name = "intent-00000001-" + "a" * 32
        self.intent = dict(schema_version=1, intent_id=self.name, goal_id=self.goal_id,
                           ordinal=1, lane="test", task="test", fingerprint="a" * 64,
                           workdir=str(home), submitted_epoch=0, submitted="test")

    def test_intent_versions(self):
        for version in (1, True, False, 1.0, 1.5, "1", None, 0, 2):
            with self.subTest(version=version, type=type(version).__name__):
                value = dict(self.intent, schema_version=version)
                (self.journal / (self.name + ".json")).write_text(json.dumps(value))
                if type(version) is int and version == 1:
                    self.assertEqual(self.store.intent(self.name), value)
                else:
                    with self.assertRaisesRegex(ValueError, "^invalid goal dispatch intent: "):
                        self.store.intent(self.name)

    def test_outcome_versions(self):
        for kind in ("claim", "no-launch"):
            for version in (1, True, False, 1.0, 1.5, "1", None, 0, 2):
                with self.subTest(kind=kind, version=version, type=type(version).__name__):
                    value = dict(schema_version=version, intent_id=self.name,
                                 goal_id=self.goal_id, kind=kind)
                    if kind == "claim":
                        value["job_id"] = "20261026-000000-2-2"
                    (self.journal / (self.name + ".outcome.json")).write_text(json.dumps(value))
                    if type(version) is int and version == 1:
                        self.assertEqual(self.store.outcome(self.intent), value)
                    else:
                        with self.assertRaisesRegex(ValueError, "^invalid goal dispatch outcome: "):
                            self.store.outcome(self.intent)

    def test_missing_outcome_remains_absent(self):
        self.assertIsNone(self.store.outcome(self.intent))


if __name__ == "__main__":
    unittest.main()
