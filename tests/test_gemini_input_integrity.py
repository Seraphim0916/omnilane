"""Exercise the dispatch boundary with a recording agy; never call a model."""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
import unittest

from offline_env import isolated_environment, wait_for_job_controller

ROOT = Path(__file__).resolve().parents[1]
GEMINI_MAX_TASK_BYTES = 130000 if platform.system() == "Linux" else 180000
FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, pathlib, re, sys
args = sys.argv[1:]
pathlib.Path(os.environ['AGY_CAPTURE'] + '.called').touch()
if "--input-format" in args and args[args.index("--input-format") + 1] == "stream-json":
    raw_message = sys.stdin.buffer.readline()
    pathlib.Path(os.environ['AGY_CAPTURE'] + '.message').write_bytes(raw_message)
    message = json.loads(raw_message)
    payload = message["message"]["content"][0]["text"].encode("utf-8")
else:
    flag = "--print" if "--print" in args else "-p"
    prompt = args[args.index(flag) + 1]
    if not prompt:
        print('Error: empty prompt. Usage: agy --print "your prompt here"', file=sys.stderr)
        sys.exit(1)
    payload = os.fsencode(prompt)[:192000]
pathlib.Path(os.environ["AGY_CAPTURE"]).write_bytes(payload)
pointer = re.search(r'^Absolute path: (.+)$', payload.decode('utf-8'), re.M)
if pointer:
    brief = pathlib.Path(json.loads(pointer.group(1)))
    pathlib.Path(os.environ['AGY_CAPTURE']+'.brief').write_bytes(brief.read_bytes())
result = {"status": "SUCCESS", "response": "OK", "conversation_id": "integrity-session"}
if "--output-format" in args:
    fmt = args[args.index("--output-format") + 1]
    print(json.dumps({"event": "result", "result": result} if fmt == "stream-json" else result), flush=True)
else:
    print("OK", flush=True)
'''


class GeminiInputIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-gemini-input-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.env, violations = isolated_environment(base / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home, self.work, bins = base / "omnilane", base / "work", base / "bin"
        for directory in (self.home, self.work, bins):
            directory.mkdir()
        (Path(self.env["HOME"]) / ".gemini").mkdir()
        agy = bins / "agy"
        agy.write_text(FAKE_AGY)
        agy.chmod(0o755)
        (self.home / "routing.local.yaml").write_text("consult: gemini gemini-3.7-flash-medium -\n")
        self.capture = base / "received.bin"
        self.env.update(OMNILANE_HOME=str(self.home), AGY_BIN=str(agy),
                        AGY_CAPTURE=str(self.capture), PATH=str(bins) + os.pathsep + self.env["PATH"])

    def dispatch(self, payload, *flags):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--workdir", str(self.work),
             "--timeout", "30", "--vendor", "gemini", *flags, "consult", "-"],
            input=payload, env=self.env, capture_output=True, timeout=90)

    def worker_rules_header(self):
        return subprocess.run(
            ["bash", "-c", 'source "$1/scripts/lib/common.sh"; '
             'printf "%s\\n%s\\n\\n" "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES"',
             "header", str(ROOT)],
            env=self.env, capture_output=True, check=True, timeout=30).stdout

    def assert_intact(self, payload, result):
        errors = "\n".join(p.read_text() for p in (self.home / "jobs").rglob("*.stderr.log"))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-2000:] + errors)
        self.assertTrue(self.capture.exists(), "fake agy was not reached")
        received = self.capture.read_bytes()
        # Both the runner and the live first message (built by job-worker.sh)
        # send the worker rules ahead of the task.
        expected = self.worker_rules_header() + payload
        self.assertTrue(received == expected,
                        f"sent={len(payload)} received={len(received)} "
                        f"sent_sha256={hashlib.sha256(payload).hexdigest()} "
                        f"received_sha256={hashlib.sha256(received).hexdigest()}")
        self.assertNotIn(b"TRUNCATED (original", received)
        self.assertEqual(received.count(b"[omnilane worker rules]"), 1)
        self.assertNotIn("\ufffd", received.decode("utf-8"))
        jobs = list((self.home / "jobs").iterdir())
        self.assertTrue(jobs)
        for job in jobs:
            self.assertTrue((job / "task.txt").read_bytes() == payload, "task.txt was modified")

    @staticmethod
    def large_payload(size=GEMINI_MAX_TASK_BYTES - 1000):
        numbered = "".join(f"{i:05d}: 中文完整任務\n" for i in range(1600)).encode("utf-8")
        return numbered + b"x" * (size - len(numbered))

    def test_foreground_large_input(self):
        payload = self.large_payload()
        self.assertEqual(len(payload), GEMINI_MAX_TASK_BYTES - 1000)
        self.assertGreater(payload.count(b"\n"), 1500)
        self.assert_intact(payload, self.dispatch(payload))

    def test_named_thread_large_input(self):
        payload = self.large_payload()
        for _ in range(2):
            self.assert_intact(payload, self.dispatch(payload, "--thread", "input-integrity"))

    def assert_rejected(self, *flags, payload=None):
        if payload is None:
            payload = self.large_payload(1048577)
        result = self.dispatch(payload, *flags)
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"task is {len(payload)} bytes".encode(), result.stderr)
        self.assertIn("limit is 1048576 bytes".encode(), result.stderr)
        self.assertIn(b"tier 3 refusal", result.stderr)
        self.assertIn(b"Nothing was cut", result.stderr)
        self.assertFalse(Path(str(self.capture) + '.called').exists())
        # Refused before any job directory exists: nothing is left to clean up.
        jobs = self.home / "jobs"
        self.assertEqual(list(jobs.iterdir()) if jobs.exists() else [], [])

    def test_foreground_over_limit(self):
        self.assert_rejected()

    def test_linux_stages_130001_bytes(self):
        if platform.system() != "Linux":
            self.skipTest("Linux-only single argv argument limit; requires Linux CI")
        result=self.dispatch(self.large_payload(130001), "--dry-run")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(b"task_delivery=file",result.stdout)

    def test_darwin_accepts_130001_bytes(self):
        if platform.system() != "Darwin":
            self.skipTest("Darwin-only comparison: no Linux MAX_ARG_STRLEN limit")
        payload = self.large_payload(130001)
        self.assertEqual(len(payload), 130001)
        self.assert_intact(payload, self.dispatch(payload))

    def test_named_thread_over_limit(self):
        self.assert_rejected("--thread", "input-integrity")

    def test_live_over_limit(self):
        self.assert_rejected("--background", "--live", "--idle-timeout", "5")

    def test_live_large_input(self):
        self.assert_live_intact(self.large_payload())

    def test_staged_tasks_across_foreground_background_thread_and_live(self):
        for flags in ((),('--background','--single-shot'),('--thread','staged-input'),('--background','--live','--idle-timeout','5')):
            for size in (GEMINI_MAX_TASK_BYTES-len(self.worker_rules_header())+1,1048576):
                with self.subTest(flags=flags,size=size):
                    payload=self.large_payload(size-2)+b'\n\n'
                    jobs=self.home/'jobs';before=set(jobs.iterdir()) if jobs.exists() else set()
                    result=self.dispatch(payload,*flags)
                    self.assertEqual(result.returncode,0,result.stderr)
                    job,=set(jobs.iterdir())-before
                    try:
                        deadline=time.monotonic()+60
                        while not (job/'exit').exists() and time.monotonic()<deadline:time.sleep(.05)
                        self.assertTrue((job/'exit').exists())
                        self.assertEqual((job/'exit').read_text().strip(),'0')
                        receipt=json.loads((job/'brief.json').read_text())
                        self.assertEqual(receipt['tier'],2)
                        self.assertEqual(receipt['delivery'],'file')
                        self.assertEqual(Path(str(self.capture)+'.brief').read_bytes(),payload)
                        self.assertEqual((job/'task.txt').read_bytes(),payload)
                        self.assertEqual(receipt['sha256'],hashlib.sha256(payload).hexdigest())
                        self.assertFalse(Path(receipt['path']).exists())
                    finally:
                        if not (job/'exit').exists():
                            subprocess.run(['bash',str(ROOT/'scripts/jobs.sh'),'close',job.name],env=self.env,capture_output=True,timeout=30)
                        wait_for_job_controller(self,job)

    def test_live_initial_message_matches_protocol(self):
        payload = '中文 "quote" \\ slash\t tab\r\nnext line'.encode('utf-8')
        self.assert_live_intact(payload)
        encoded = subprocess.run(
            ['bash', '-c', 'source "$1/scripts/lib/common.sh"; '
             'source "$1/scripts/lib/live-protocol.sh"; '
             'live_encode_message gemini job session "$2"; printf "\\n"',
             'protocol-check', str(ROOT), (self.worker_rules_header() + payload).decode('utf-8')],
            env=self.env, capture_output=True, check=True, timeout=30)
        self.assertEqual(Path(str(self.capture) + '.message').read_bytes(), encoded.stdout)

    def test_fake_agy_rejects_empty_print(self):
        for flag in ('--print', '-p'):
            with self.subTest(flag=flag):
                result = subprocess.run([self.env['AGY_BIN'], flag, ''],
                                        env=self.env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b'Error: empty prompt. Usage: agy --print "your prompt here"\n')

    def test_fake_agy_truncates_oversized_argument(self):
        if platform.system() == "Linux":
            self.skipTest("Linux MAX_ARG_STRLEN prevents exec before fake agy can truncate")
        payload = self.large_payload(193000)
        result = subprocess.run([self.env['AGY_BIN'], '--print', payload.decode('utf-8')],
                                env=self.env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.capture.read_bytes(), payload[:192000])

    def assert_live_intact(self, payload):
        result = self.dispatch(payload, "--background", "--live", "--idle-timeout", "5")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-2000:])
        job_id = result.stdout.decode().strip()
        job = self.home / "jobs" / job_id
        try:
            deadline = time.monotonic() + 60
            while not (job / "exit").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue((job / "exit").exists(), "live worker did not finish")
            self.assertEqual((job / "exit").read_text().strip(), "0")
            self.assert_intact(payload, result)
        finally:
            if not (job / "exit").exists():
                subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), "close", job_id],
                               env=self.env, capture_output=True, timeout=30)
            wait_for_job_controller(self, job)


if __name__ == "__main__":
    unittest.main()
