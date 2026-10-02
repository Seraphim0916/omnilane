"""Offline completion-inbox regressions for replay and unreadable records."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]


class InboxRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omnilane-inbox-recovery-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env, self.violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = self.root / "state"
        self.inbox = self.home / "inbox"
        self.consumed = self.inbox / "consumed"
        self.consumed.mkdir(parents=True)
        self.workdir = self.root / "project"
        self.workdir.mkdir()
        self.env.update(OMNILANE_HOME=str(self.home), CLAUDE_PROJECT_DIR=str(self.workdir))

    def record(self, index, raw=None):
        job_id = f"20260830-01{index:04d}-1-1"
        path = self.inbox / (job_id + ".json")
        if raw is None:
            raw = json.dumps(dict(job_id=job_id, lane="triage", vendor="exec",
                                  workdir=str(self.workdir), exit=0, tail="tail-" + job_id)) + "\n"
        path.write_text(raw, encoding="utf-8")
        path.chmod(0o600)
        return path

    def deliver(self):
        result = subprocess.run([str(ROOT / "hooks/report-completions.sh")],
                                input="{}", env=self.env, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        headers = [line for line in result.stdout.splitlines()
                   if line.startswith("Omnilane completion:")]
        return result.stdout, headers

    def test_consumed_replays_do_not_starve_fresh_completions(self):
        originals = {}
        for index in range(1, 11):
            pending = self.record(index)
            archived = self.consumed / pending.name
            archived.write_bytes(pending.read_bytes())
            originals[archived] = archived.read_bytes()
        fresh = [self.record(11), self.record(12)]

        output, headers = self.deliver()
        self.assertEqual(len(headers), 2, output)
        for pending in fresh:
            self.assertIn("job=" + pending.stem, output)
            self.assertFalse(pending.exists())
        self.assertNotIn("withheld", output)
        for archived, original in originals.items():
            self.assertEqual(archived.read_bytes(), original)
        repeated, headers = self.deliver()
        self.assertEqual((repeated, headers), ("", []))

    def test_unparseable_records_obey_delivery_budget(self):
        self.check_unreadable_budget("{broken JSON\n")

    def test_consumed_symlinks_do_not_use_slots_or_modify_targets(self):
        target = self.root / "unrelated-target"
        target.write_bytes(b"preserve this unrelated file\n")
        missing = self.root / "missing-target"
        links = {}
        for index in range(1, 11):
            pending = self.record(index)
            destination = self.consumed / pending.name
            linked_target = target if index % 2 else missing
            destination.symlink_to(linked_target)
            links[destination] = linked_target
        fresh = self.record(11)

        output, headers = self.deliver()
        self.assertEqual(len(headers), 1, output)
        self.assertIn("job=" + fresh.stem, output)
        self.assertNotIn("withheld", output)
        self.assertFalse(fresh.exists())
        self.assertEqual(target.read_bytes(), b"preserve this unrelated file\n")
        self.assertFalse(missing.exists())
        for destination, linked_target in links.items():
            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(linked_target))
            self.assertTrue((self.inbox / destination.name).is_file())
        self.assertEqual(self.deliver(), ("", []))

    def test_oversized_records_obey_delivery_budget(self):
        self.check_unreadable_budget("x" * 65537)

    def check_unreadable_budget(self, raw):
        paths = [self.record(index, raw) for index in range(1, 13)]
        output, headers = self.deliver()
        self.assertEqual(len(headers), 10, output)
        self.assertIn("2 matching completion records withheld", output)
        self.assertEqual(output.count("record was unreadable"), 10)
        self.assertEqual(sorted(self.inbox.glob("*.json")), paths[10:])
        output, headers = self.deliver()
        self.assertEqual(len(headers), 2, output)
        self.assertNotIn("withheld", output)
        self.assertEqual(list(self.inbox.glob("*.json")), [])
        self.assertEqual(self.deliver(), ("", []))

    def test_mixed_readable_and_unreadable_records_share_order_and_budget(self):
        paths = [self.record(index, "{broken\n" if index % 2 == 0 else None)
                 for index in range(1, 13)]
        output, headers = self.deliver()
        self.assertEqual(len(headers), 10, output)
        self.assertIn("2 matching completion records withheld", output)
        for header, path in zip(headers, paths[:10]):
            self.assertIn("job=" + path.stem + " ", header)
        self.assertEqual(sorted(self.inbox.glob("*.json")), paths[10:])


if __name__ == "__main__":
    unittest.main()
