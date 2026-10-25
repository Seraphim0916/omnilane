"""Self-execution records cite a stored refusal; stats count them against dispatched jobs."""

from __future__ import annotations
import offline_env  # noqa: F401
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import refusal_evidence
import self_exec

REFUSAL = {"allowed": False, "code": "missing-caller-context", "failed_gate": "caller-identity",
           "reason": "no caller identity reached the gate",
           "message": "no caller identity: a model caller runs `omnilane whoami` and passes the file"}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        env = patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OMNILANE_REFUSAL_EVIDENCE", None)

    def evidence(self, text=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(text or json.dumps(REFUSAL) + "\n")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = refusal_evidence.main(["--home", str(self.home), "--lane", "triage",
                                        "--headline", "omnilane: no eligible available target"])
        return rc, stdout.getvalue().strip(), stderr.getvalue().strip()

    def cli(self, *args):
        return subprocess.run(["bash", str(ROOT / "bin/omnilane"), *args], text=True, capture_output=True,
                              env=dict(os.environ), timeout=20)

    def job(self, name, started, executor="cli"):
        directory = self.home / "jobs" / name
        directory.mkdir(parents=True)
        (directory / "meta.json").write_text(json.dumps({"started": started, "executor": executor}))


class RefusalEvidenceTests(Fixture):
    def test_the_summary_is_one_pasteable_line_and_the_refusal_is_kept(self):
        rc, path, summary = self.evidence()
        self.assertEqual(rc, 0)
        self.assertEqual(summary.count("\n"), 0)
        self.assertTrue(summary.startswith("omnilane refusal: failed_gate=caller-identity "
                                           "code=missing-caller-context "))
        self.assertIn('reason="no caller identity reached the gate"', summary)
        self.assertIn(f"evidence={path}", summary)
        stored = json.loads(Path(path).read_text())
        self.assertEqual((stored["lane"], stored["refusal"]), ("triage", REFUSAL))
        self.assertEqual(stored["headline"], "omnilane: no eligible available target")

    def test_the_last_refusal_line_wins_and_other_lines_are_ignored(self):
        text = "\n".join(["plain text", json.dumps({"allowed": True}),
                          json.dumps(dict(REFUSAL, code="first")), "{not json", json.dumps(REFUSAL)])
        _, path, _ = self.evidence(text)
        self.assertEqual(json.loads(Path(path).read_text())["refusal"]["code"], "missing-caller-context")

    def test_nothing_is_stored_without_a_refusal(self):
        rc, path, summary = self.evidence(json.dumps({"allowed": True}))
        self.assertEqual((rc, path, summary), (1, "", ""))
        self.assertFalse((self.home / "refusals").exists())

    def test_storing_can_be_switched_off_but_the_summary_stays(self):
        os.environ["OMNILANE_REFUSAL_EVIDENCE"] = "0"
        rc, path, summary = self.evidence()
        self.assertEqual((rc, path), (0, ""))
        self.assertIn("code=missing-caller-context", summary)
        self.assertNotIn("evidence=", summary)
        self.assertFalse((self.home / "refusals").exists())


class SelfExecTests(Fixture):
    def test_a_record_cites_the_refusal_file_and_its_hash(self):
        _, path, _ = self.evidence()
        result = self.cli("self-exec", "record", "--refusal", path, "--reason", "  sandbox   blocked\nwhoami ",
                          "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(result.stdout)
        self.assertEqual((entry["lane"], entry["reason"]), ("triage", "sandbox blocked whoami"))
        self.assertEqual(entry["refusal"]["path"], str(Path(path).resolve()))
        self.assertEqual((entry["refusal"]["code"], entry["refusal"]["failed_gate"]),
                         ("missing-caller-context", "caller-identity"))
        log = (self.home / "self-exec.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["id"] for line in log], [entry["id"]])

    def test_a_record_without_a_real_refusal_is_rejected(self):
        not_refusal = Path(self.tmp.name) / "allowed.json"
        not_refusal.write_text(json.dumps({"allowed": True}))
        for path in (str(not_refusal), str(Path(self.tmp.name) / "missing.json")):
            with self.subTest(path=path):
                result = self.cli("self-exec", "record", "--refusal", path, "--reason", "x")
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "self-exec.jsonl").exists())

    def test_reason_and_refusal_are_both_required(self):
        _, path, _ = self.evidence()
        self.assertNotEqual(self.cli("self-exec", "record", "--refusal", path).returncode, 0)
        self.assertNotEqual(self.cli("self-exec", "record", "--reason", "x").returncode, 0)
        self.assertNotEqual(self.cli("self-exec", "record", "--refusal", path, "--reason", " ").returncode, 0)

    def test_stats_share_counts_records_against_jobs_started_in_the_window(self):
        _, path, _ = self.evidence()
        for _ in range(2):
            self.assertEqual(self.cli("self-exec", "record", "--refusal", path, "--reason", "r").returncode, 0)
        now = datetime.datetime.now(datetime.timezone.utc)
        recent = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.job("a", recent)
        self.job("b", recent, "native")
        self.job("c", old)
        (self.home / "jobs" / "broken").mkdir()
        (self.home / "jobs" / "broken" / "meta.json").write_text("{")
        result = self.cli("stats", "self-exec", "--since", "7d", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["self_exec"], report["dispatched"], report["self_exec_share"]), (2, 2, 0.5))
        self.assertEqual(report["dispatched_by_executor"], {"cli": 1, "native": 1})
        self.assertEqual(report["by_code"], {"missing-caller-context": 2})
        text = self.cli("stats", "self-exec").stdout
        self.assertIn("2 of 4 tasks (50.0%)", text)

    def test_an_empty_home_has_no_share(self):
        report = json.loads(self.cli("stats", "self-exec", "--json").stdout)
        self.assertEqual((report["self_exec"], report["dispatched"], report["self_exec_share"]), (0, 0, None))

    def test_since_accepts_days_hours_and_a_date_only(self):
        moment = datetime.datetime(2026, 10, 20, 12, tzinfo=datetime.timezone.utc)
        self.assertEqual(self_exec.since_cutoff("7d", moment), moment - datetime.timedelta(days=7))
        self.assertEqual(self_exec.since_cutoff("12h", moment), moment - datetime.timedelta(hours=12))
        self.assertEqual(self_exec.since_cutoff("2026-10-13", moment).day, 13)
        for bad in ("0d", "7w", "yesterday"):
            with self.subTest(since=bad), self.assertRaises(SystemExit):
                self_exec.since_cutoff(bad, moment)

    def test_the_subcommands_print_usage_on_a_wrong_verb(self):
        self.assertEqual(self.cli("self-exec", "list").returncode, 2)
        self.assertEqual(self.cli("stats").returncode, 2)


if __name__ == "__main__":
    unittest.main()
