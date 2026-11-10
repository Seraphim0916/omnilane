"""A claude probe that answers without the token is asked once more; nothing else is."""
import offline_env  # Activate suite isolation for direct file execution.

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import probe
import probe_failure
import probe_sweep

TOKEN = probe_sweep.TOKENS["claude"]


def answer(model, result, *, is_error=False):
    return json.dumps({"result": result, "is_error": is_error, "modelUsage": {model: {}}})


class ClaudeRetryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.entries = probe_sweep.plan("claude")
        self.first = self.entries[0]
        self.calls = []
        self.lines = []

    def fake_probe(self, replies):
        """replies(name, attempt) -> (stdout, stderr, exit_code)"""
        def run(name, argv, *, root, vendor, expected_token, app_root=None):
            attempt = sum(1 for call in self.calls if call == name) + 1
            self.calls.append(name)
            stdout, stderr, exit_code = replies(name, attempt)
            evidence = root / "evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            record = dict(command=argv, exit_code=exit_code, timed_out=False,
                          stdout=str(evidence / f"{name}.stdout"), stderr=str(evidence / f"{name}.stderr"))
            record["verdict"], record["verdict_reason"], record["observed_model"], record["evidence_tier"] = \
                probe.verdict(record, stdout, stderr, vendor, expected_token, {})
            Path(record["stdout"]).write_text(stdout)
            Path(record["stderr"]).write_text(stderr)
            (evidence / f"{name}.json").write_text(json.dumps(record))
            return record
        return run

    def sweep(self, replies):
        with patch.object(probe_sweep.probe_module, "probe", self.fake_probe(replies)), \
                patch.dict(os.environ, {"OMNILANE_PROBE_ANY_SESSION": "1"}):
            return probe_failure.run_sweep("claude", self.root, log=self.lines.append)

    def first_row(self, first, second=None):
        """The first row answers `first`, then `second`; every other row passes."""
        def replies(name, attempt):
            entry = next(e for e in self.entries if e["name"] == name)
            if name != self.first["name"]:
                return answer(entry["model"], TOKEN), "", 0
            return (first if attempt == 1 else second)(entry["model"])
        return replies

    def injected(self, model):
        return answer(model, "<reasoning_effort>15</reasoning_effort>"), "", 0

    def token(self, model):
        return answer(model, TOKEN), "", 0

    def kept(self, suffix):
        return self.root / "retried" / f"{self.first['name']}.first-attempt.{suffix}"

    def test_a_missing_token_then_the_token_passes_and_keeps_the_first_answer(self):
        report = self.sweep(self.first_row(self.injected, self.token))
        self.assertEqual(report["outcome"], "done")
        self.assertIn(self.first["config_id"], report["passed"])
        self.assertNotIn(self.first["config_id"], report["failed"])
        self.assertEqual(self.calls.count(self.first["name"]), 2)
        self.assertIn("<reasoning_effort>", self.kept("stdout").read_text())
        self.assertEqual(json.loads(self.kept("json").read_text())["verdict_reason"], "missing-expected-token")
        evidence = json.loads((self.root / "evidence" / f"{self.first['name']}.json").read_text())
        self.assertEqual(evidence["verdict"], "pass")
        # The evidence a retry leaves is the request the proof engine expects.
        self.assertEqual(evidence["command"], probe_sweep.command("claude", self.first["model"], self.first["effort"]))
        self.assertTrue(any("passed on retry" in line for line in self.lines), self.lines)

    def test_a_missing_token_twice_fails_the_row(self):
        report = self.sweep(self.first_row(self.injected, self.injected))
        self.assertIn(self.first["config_id"], report["failed"])
        self.assertNotIn(self.first["config_id"], report["passed"])
        self.assertEqual(self.calls.count(self.first["name"]), 2)
        self.assertTrue(self.kept("stdout").is_file())
        self.assertEqual(json.loads((self.root / "evidence" / f"{self.first['name']}.json").read_text())
                         ["verdict_reason"], "missing-expected-token")
        self.assertTrue(any("failed twice" in line for line in self.lines), self.lines)

    def test_other_claude_failures_are_not_retried(self):
        cases = {
            "quota": lambda model: (answer(model, "Weekly usage limit reached", is_error=True), "", 1),
            "model-mismatch": lambda model: (answer("claude-haiku-5-5", TOKEN), "", 0),
            "exit-code": lambda model: (answer(model, "CLAUDE"), "", 1),
            "invalid-json": lambda model: ("not json", "", 0),
        }
        for label, reply in cases.items():
            with self.subTest(label):
                self.calls.clear()
                self.lines.clear()
                for path in (self.root / "evidence").glob("*"):
                    path.unlink()
                report = self.sweep(self.first_row(reply, self.token))
                self.assertEqual(self.calls.count(self.first["name"]), 1, label)
                self.assertIn(self.first["config_id"], report["failed"])
                self.assertFalse(self.kept("stdout").exists())

    def test_the_retry_happens_at_most_once_per_row_even_if_the_engine_asks_again(self):
        # A reply that also reads as transient makes the engine call the wrapper a second time.
        def overloaded(model):
            return answer(model, "overloaded"), "", 0
        report = self.sweep(self.first_row(overloaded, overloaded))
        self.assertIn(self.first["config_id"], report["failed"])
        self.assertEqual(self.calls.count(self.first["name"]), 3)  # first, our retry, the engine's transient retry

    def test_other_vendors_are_not_retried_on_a_missing_token(self):
        entry = probe_sweep.plan("grok")[0]
        calls = []

        def run(name, argv, *, root, vendor, expected_token, app_root=None):
            calls.append(name)
            evidence = root / "evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            stdout = json.dumps({"result": "no token", "modelUsage": {entry["model"]: {}}})
            record = dict(command=argv, exit_code=0, timed_out=False,
                          stdout=str(evidence / f"{name}.stdout"), stderr=str(evidence / f"{name}.stderr"))
            record["verdict"], record["verdict_reason"], record["observed_model"], record["evidence_tier"] = \
                probe.verdict(record, stdout, "", vendor, expected_token, {})
            Path(record["stdout"]).write_text(stdout)
            Path(record["stderr"]).write_text("")
            (evidence / f"{name}.json").write_text(json.dumps(record))
            return record
        with patch.object(probe_sweep.probe_module, "probe", run), \
                patch.dict(os.environ, {"OMNILANE_PROBE_ANY_SESSION": "1"}):
            probe_failure.run_sweep("grok", self.root, log=lambda _: None)
        self.assertEqual(calls.count(entry["name"]), 1)
        self.assertFalse((self.root / "retried").exists())


if __name__ == "__main__":
    unittest.main()
