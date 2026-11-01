"""Only codex's own final error can call a vendor logged out, and that call keeps its output."""
import offline_env  # Activate suite isolation for direct file execution.

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import probe
import probe_failure
import probe_sweep
import transport_compat

MCP_401 = ('2026-11-01T04:38:51.174791Z ERROR rmcp::transport::worker: worker quit with fatal: '
           'Transport channel closed, when UnexpectedServerResponse("HTTP 401: Authentication Failed")\n')
# Short enough to survive probe.py's 120-character stderr review with its 401 intact.
MCP_401_SHORT = "ERROR rmcp: HTTP 401: Authentication Failed\n"
TOKEN = probe_sweep.TOKENS["codex"]
OK = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": TOKEN}})


def failed(message, *earlier):
    events = [{"type": "error", "message": text} for text in earlier]
    events.append({"type": "turn.failed", "error": {"message": message}})
    return "\n".join(json.dumps(event) for event in events) + "\n"


UNSUPPORTED = ("stream disconnected before completion: upstream 400: The 'gpt-5.4-mini' model "
               "is not supported when using Codex with a ChatGPT account.")
LOGGED_OUT = "workspace routing discovery unauthorized (401)"


class Harness(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.calls = []

    def fake_probe(self, outputs):
        def run(name, argv, *, root, vendor, expected_token, app_root=None):
            self.calls.append(name)
            stdout, stderr, exit_code = outputs(name)
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

    def sweep(self, outputs, **kwargs):
        with patch.object(probe_sweep.probe_module, "probe", self.fake_probe(outputs)):
            return probe_failure.run_sweep("codex", self.root, log=lambda _: None, **kwargs)


class CodexVerdictTests(Harness):
    def setUp(self):
        super().setUp()
        self.first = probe_sweep.plan("codex")[0]

    def first_fails(self, stdout, stderr=MCP_401):
        return lambda name: (stdout, stderr, 1) if name == self.first["name"] else (OK, MCP_401, 0)

    def test_an_unsupported_model_beside_an_mcp_401_fails_that_row_and_continues(self):
        report = self.sweep(self.first_fails(failed(UNSUPPORTED)))
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(report["failed"], [self.first["config_id"]])
        self.assertEqual(len(self.calls), len(probe_sweep.plan("codex")))
        self.assertTrue((self.root / "evidence" / f"{self.first['name']}.json").is_file())

    def test_an_mcp_401_inside_the_stderr_review_is_not_a_login_verdict(self):
        report = self.sweep(self.first_fails(failed(UNSUPPORTED), stderr=MCP_401_SHORT))
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(report["failed"], [self.first["config_id"]])
        descriptor = json.loads((self.root / "evidence" / f"{self.first['name']}.json").read_text())
        self.assertIn("401", descriptor["verdict_reason"])

    def test_a_login_error_only_on_stderr_is_still_not_logged_in(self):
        report = self.sweep(lambda name: ("", "Error: Not logged in. Run codex login\n", 1))
        self.assertEqual(report["outcome"], "unprobeable")
        self.assertIn("log in", report["detail"])
        self.assertEqual(self.calls, [self.first["name"]])

    def test_an_mcp_401_with_no_final_event_is_not_a_login_verdict(self):
        report = self.sweep(self.first_fails("", stderr=MCP_401_SHORT))
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(report["failed"], [self.first["config_id"]])

    def test_a_transient_final_error_is_retried_once(self):
        for status in ("503", "429"):
            with self.subTest(status=status):
                self.calls.clear()

                def outputs(name, message=f"exceeded retry limit, last status: {status}"):
                    if name == self.first["name"] and self.calls.count(name) == 1:
                        return failed(message), "", 1
                    return OK, "", 0
                # Without retry_now run_sweep's cooldown answers the retry from the first failure.
                report = self.sweep(outputs, retry_now=True)
                self.assertEqual((report["outcome"], report["failed"]), ("done", []))
                self.assertEqual(self.calls.count(self.first["name"]), 2)

    def test_a_final_event_is_read_up_to_its_200_character_limit(self):
        # probe.codex_failure keeps 200 characters, so a status past them goes unseen.
        inside = "x" * 150 + " 401 unauthorized"
        self.assertEqual(self.sweep(self.first_fails(failed(inside), stderr=""))["outcome"], "unprobeable")
        self.calls.clear()
        past = "x" * 210 + " 401 unauthorized"
        report = self.sweep(self.first_fails(failed(past), stderr=""))
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(report["failed"], [self.first["config_id"]])

    def test_an_mcp_401_does_not_cost_a_retry_or_a_login_verdict_on_a_pass(self):
        report = self.sweep(lambda name: (OK, MCP_401, 0))
        self.assertEqual((report["outcome"], report["failed"]), ("done", []))
        self.assertEqual(self.calls.count(self.first["name"]), 1)

    def test_codexs_own_401_means_not_logged_in(self):
        for message in (LOGGED_OUT, "unexpected status 401 Unauthorized: Missing bearer or basic "
                                    "authentication in header"):
            with self.subTest(message=message):
                self.calls.clear()
                report = self.sweep(self.first_fails(failed(message), stderr=""))
                self.assertEqual(report["outcome"], "unprobeable")
                self.assertIn("log in", report["detail"])
                self.assertEqual(self.calls, [self.first["name"]])

    def test_the_final_event_supersedes_a_401_retry_notice(self):
        stdout = failed(UNSUPPORTED, "Reconnecting... 1/5 (unauthorized (401))")
        report = self.sweep(self.first_fails(stdout, stderr=""))
        self.assertEqual(report["outcome"], "done")
        self.assertEqual(report["failed"], [self.first["config_id"]])

    def test_a_late_final_event_past_the_read_limit_still_counts(self):
        stdout = failed(LOGGED_OUT, *["Reconnecting... (upstream hiccup) " + "x" * 200] * 30)
        self.assertGreater(len(stdout), 4000)
        self.assertEqual(self.sweep(self.first_fails(stdout, stderr=""))["outcome"], "unprobeable")

    def test_not_logged_in_keeps_the_output_outside_the_evidence(self):
        stdout, stderr = failed(LOGGED_OUT), "login error detail\n"
        self.sweep(self.first_fails(stdout, stderr=stderr))
        name = self.first["name"]
        kept = self.root / "unprobeable"
        self.assertEqual((kept / f"{name}.unprobeable.stdout").read_text(), stdout)
        self.assertEqual((kept / f"{name}.unprobeable.stderr").read_text(), stderr)
        descriptor = json.loads((kept / f"{name}.unprobeable.json").read_text())
        self.assertEqual(descriptor["verdict"], "fail")
        # Neither the overlay builder's exact paths nor a `<name>.*` evidence copy can see it.
        self.assertEqual(list((self.root / "evidence").glob(name + ".*")), [])

    def test_kept_output_does_not_count_as_an_answer_next_time(self):
        self.sweep(self.first_fails(failed(LOGGED_OUT), stderr=""))
        self.calls.clear()
        report = self.sweep(lambda name: (OK, "", 0), only_missing=True)
        self.assertEqual(report["outcome"], "done")
        self.assertIn(self.first["name"], self.calls)

    def test_an_earlier_answer_stays_evidence_when_a_later_row_finds_no_login(self):
        second = probe_sweep.plan("codex")[1]["name"]
        report = self.sweep(lambda name: (OK, "", 0) if name == self.first["name"]
                            else (failed(LOGGED_OUT), "", 1))
        self.assertEqual(report["outcome"], "unprobeable")
        self.assertTrue((self.root / "evidence" / f"{self.first['name']}.json").is_file())
        self.assertFalse((self.root / "unprobeable" / f"{self.first['name']}.unprobeable.json").exists())
        self.assertTrue((self.root / "unprobeable" / f"{second}.unprobeable.json").is_file())


class OtherVendorTests(Harness):
    def test_other_vendors_still_read_stderr(self):
        entry = probe_sweep.plan("grok")[0]
        record = dict(verdict="fail", verdict_reason="exit-code: 1")
        path = self.root / "stderr"
        path.write_text("Error: Not signed in.\n")
        record["stderr"] = str(path)
        self.assertTrue(probe_failure.not_authenticated("grok", record), entry["name"])


class EngineTests(unittest.TestCase):
    def test_the_engine_stays_the_one_every_proof_pins(self):
        # Editing probe.py or probe_sweep.py would invalidate every verified mapping on every host.
        archive = json.loads(Path(transport_compat.ARCHIVE).read_text())
        latest = max(archive["snapshots"])
        self.assertEqual(archive["snapshots"][latest]["engine"], transport_compat.engine_contract())


if __name__ == "__main__":
    unittest.main()
