#!/usr/bin/env python3
"""Exercise the real runner offline; only its curl transport is replaced."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/runners/run-openai-compat.sh"
DUMMY_KEY = "offline-only-oai-secret-marker"

# POSIX shell works with macOS bash 3.2 and Linux. Python supplies portable
# stat/argv serialization, avoiding GNU/BSD stat flags and lossy shell quoting.
FAKE_CURL = r'''#!/bin/sh
set -eu
python3 - "$@" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

argv = sys.argv[1:]
headers = [Path(argv[i + 1][1:]) for i, arg in enumerate(argv[:-1])
           if arg == "-H" and argv[i + 1].startswith("@")]
request = Path(argv[argv.index("--data-binary") + 1][1:])
if os.environ.get("FAKE_CURL_STATUS") and "-D" in argv:
    Path(argv[argv.index("-D") + 1]).write_text(
        "HTTP/1.1 " + os.environ["FAKE_CURL_STATUS"] + " Fixture\r\n\r\n")
Path(os.environ["FAKE_CURL_RECORD"]).write_text(json.dumps({
    "argv": argv,
    "headers": [{"path": str(path),
                 "mode": stat.S_IMODE(path.stat().st_mode),
                 "authorization_matches": path.read_text() ==
                 "Authorization: Bearer " + os.environ["GROQ_API_KEY"] + "\n"}
                for path in headers],
    "request": json.loads(request.read_text()),
}), encoding="utf-8")
PY
printf '%s\n' 'offline curl fixture' >&2
printf '%s' "$FAKE_CURL_RESPONSE"
exit "${FAKE_CURL_RC:-0}"
'''


class OpenAICompatRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-oai-offline-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(
            self.root / "isolated", os.environ.get("PATH", "")
        )
        self.addCleanup(lambda: self.assertFalse(
            violations.exists(), "unexpected unmocked network command"
        ))
        self.utilities = Path(self.env["PATH"])
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.curl = self.fake_bin / "curl"
        self.curl.write_text(FAKE_CURL, encoding="utf-8")
        self.curl.chmod(0o755)
        self.record = self.root / "curl-argv.json"
        # Spaces also exercise quoting through run_with_timeout and @file args.
        self.job = self.root / "private job"
        self.job.mkdir(mode=0o700)
        self.output = self.job / "result.txt"
        self.prompt = self.job / "prompt.txt"
        self.prompt.write_text('Offline prompt: "quoted"\n第二行\n', encoding="utf-8")
        self.env.update(
            PATH=str(self.fake_bin) + os.pathsep + self.env["PATH"],
            OMNILANE_OAI_VENDOR="groq",
            OMNILANE_TIMEOUT="10",
            GROQ_API_KEY=DUMMY_KEY,
            FAKE_CURL_RECORD=str(self.record),
            FAKE_CURL_RESPONSE=json.dumps({
                "choices": [{"message": {"content": "offline answer\n"}}]
            }),
        )

    def run_runner(self, expected_rc, keyword, *, mode="advise", model="offline-model",
                   called=True):
        result = subprocess.run(
            ["bash", str(RUNNER), mode, str(self.job), model, "-",
             str(self.prompt), str(self.output)],
            env=self.env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, expected_rc, (result.stdout, result.stderr))
        log = Path(str(self.output) + ".stderr.log")
        self.assertTrue(log.exists(), result.stderr)
        self.assertIn(keyword, log.read_text(encoding="utf-8"))
        self.assertEqual(self.record.exists(), called)
        for suffix in (".request.json", ".response.json", ".headers"):
            self.assertFalse(Path(str(self.output) + suffix).exists(), suffix)
        return json.loads(self.record.read_text()) if called else None

    def test_work_mode_is_refused(self):
        self.run_runner(2, "inference-only", mode="work", called=False)

    def test_missing_api_key(self):
        self.env.pop("GROQ_API_KEY")
        self.run_runner(2, "GROQ_API_KEY is not set", called=False)

    def test_empty_model_is_refused(self):
        self.run_runner(2, "explicit model slug", model="", called=False)

    def test_dash_model_is_refused(self):
        self.run_runner(2, "explicit model slug", model="-", called=False)

    def test_unknown_vendor_is_refused(self):
        self.env["OMNILANE_OAI_VENDOR"] = "unknown-offline-vendor"
        self.run_runner(2, "unknown OpenAI-compatible vendor", called=False)

    def test_missing_curl(self):
        self.curl.unlink()
        (self.utilities / "curl").unlink()
        self.run_runner(2, "curl not found", called=False)

    def test_missing_python3(self):
        (self.utilities / "python3").unlink()
        self.run_runner(2, "python3 not found", called=False)

    def test_success_writes_message_content(self):
        record = self.run_runner(0, "offline curl fixture")
        self.assertEqual(self.output.read_text(), "offline answer\n")
        self.assertEqual(record["request"], {
            "model": "offline-model",
            "messages": [{"role": "user", "content": self.prompt.read_text()}],
        })
        self.assertIn("https://api.groq.com/openai/v1/chat/completions", record["argv"])

    def test_whole_task_above_old_transport_limit_and_at_runaway_ceiling(self):
        for vendor in ('cerebras','deepseek','groq','mistral','openrouter','zai'):
            self.env.update(OMNILANE_OAI_VENDOR=vendor)
            self.env[vendor.upper()+'_API_KEY']=DUMMY_KEY
            for size in (140000,140001,1048576):
                with self.subTest(vendor=vendor,size=size):
                    payload=b'x'*(size-4)+b'\r\n\n\n'
                    self.prompt.write_bytes(payload)
                    record=self.run_runner(0,"offline curl fixture")
                    self.assertEqual(record['request']['messages'][0]['content'].encode(),payload)

    def test_provider_context_error_is_one_line_exit_two(self):
        for rc in ('0','22'):
            with self.subTest(curl_rc=rc):
                self.env['FAKE_CURL_RC']=rc
                self.env['FAKE_CURL_RESPONSE']=json.dumps({'error':{'code':'context_length_exceeded','message':'maximum context length'}})
                self.run_runner(2,'model context limit exceeded')
                self.assertEqual(Path(str(self.output)+'.stderr.log').read_text().count('\n'),1)

    def test_context_wording_needs_400_or_413_and_429_is_never_context(self):
        worded = json.dumps({"error": {"message": "Request has too many tokens for this model"}})
        for status, expected in (("429", 22), ("400", 2), ("413", 2), ("", 22)):
            with self.subTest(status=status):
                self.env.update(FAKE_CURL_RC="22", FAKE_CURL_RESPONSE=worded, FAKE_CURL_STATUS=status)
                if expected == 2:
                    self.run_runner(2, "model context limit exceeded")
                else:
                    # Same exit and body report as before the context check existed.
                    self.run_runner(22, "too many tokens")
                    self.assertNotIn("model context limit exceeded",
                                     Path(str(self.output) + ".stderr.log").read_text())

    def test_429_with_structured_context_code_is_still_a_rate_limit(self):
        self.env.update(FAKE_CURL_RC="22", FAKE_CURL_STATUS="429", FAKE_CURL_RESPONSE=json.dumps(
            {"error": {"code": "context_length_exceeded", "message": "too many tokens per minute"}}))
        self.run_runner(22, "too many tokens per minute")

    def test_non_object_error_bodies_are_not_context_errors(self):
        for body in ('[1, 2]', '"too many tokens"', '{"error": "context length too many tokens"}'):
            with self.subTest(body=body):
                self.env.update(FAKE_CURL_RC="22", FAKE_CURL_RESPONSE=body, FAKE_CURL_STATUS="")
                self.run_runner(22, "offline curl fixture")
                self.assertNotIn("Traceback", Path(str(self.output) + ".stderr.log").read_text())

    def test_runaway_input_never_reaches_curl(self):
        self.prompt.write_bytes(b'x'*1048577)
        result=subprocess.run(['bash',str(RUNNER),'advise',str(self.job),'offline-model','-',str(self.prompt),str(self.output)],env=self.env,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIn(b'runaway-input',result.stderr)
        self.assertFalse(self.record.exists())

    def test_api_error_is_reported(self):
        self.env["FAKE_CURL_RESPONSE"] = json.dumps({"error": {"message": "offline rejected"}})
        self.run_runner(1, "groq API error")
        self.assertIn("offline rejected", Path(str(self.output) + ".stderr.log").read_text())

    def test_curl_failure_preserves_status_and_response_body(self):
        self.env.update(FAKE_CURL_RC="22", FAKE_CURL_RESPONSE='{"error":"offline HTTP failure"}')
        self.run_runner(22, "offline HTTP failure")
        log = Path(str(self.output) + ".stderr.log").read_text()
        self.assertIn("offline curl fixture", log)
        self.assertIn(self.env["FAKE_CURL_RESPONSE"], log)

    def test_whitespace_content_is_failure(self):
        self.env["FAKE_CURL_RESPONSE"] = json.dumps({
            "choices": [{"message": {"content": " \t\r\n"}}]
        })
        self.run_runner(1, "groq produced no output")

    def test_vendor_base_url_override(self):
        self.env["GROQ_BASE_URL"] = "https://offline.invalid/custom/v1"
        record = self.run_runner(0, "offline curl fixture")
        self.assertIn("https://offline.invalid/custom/v1/chat/completions", record["argv"])
        self.assertNotIn("https://api.groq.com/openai/v1/chat/completions", record["argv"])

    def test_key_uses_private_header_file_and_temporary_files_are_removed(self):
        record = self.run_runner(0, "offline curl fixture")
        self.assertNotIn(DUMMY_KEY, json.dumps(record["argv"]))
        header = str(self.output) + ".headers"
        self.assertEqual(record["headers"], [{
            "path": header, "mode": 0o600, "authorization_matches": True,
        }])
        self.assertIn("@" + header, record["argv"])
        self.assertFalse(Path(header).exists())
        self.assertNotIn(DUMMY_KEY, Path(str(self.output) + ".stderr.log").read_text())


if __name__ == "__main__":
    unittest.main()
