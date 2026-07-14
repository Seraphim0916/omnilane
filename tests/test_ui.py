#!/usr/bin/env python3
"""Tests for the optional local Omnilane Live UI."""

import importlib.util
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI_SCRIPT = ROOT / "scripts" / "ui.py"
SPEC = importlib.util.spec_from_file_location("omnilane_ui", UI_SCRIPT)
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="omnilane-ui-test-")
        self.home = Path(self.tempdir.name)
        self.jobs = self.home / "jobs"
        self.jobs.mkdir()
        self.store = ui.JobStore(self.jobs)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_job(
        self,
        job_id,
        *,
        meta=True,
        task="TASK-CANARY",
        output="OUTPUT-CANARY",
        pid=None,
        exit_code=None,
    ):
        job = self.jobs / job_id
        job.mkdir()
        if meta is True:
            (job / "meta.json").write_text(
                json.dumps(
                    {
                        "lane": "hard-judgment",
                        "vendor": "claude",
                        "model": "claude-opus-4-8",
                        "effort": "high",
                        "timeout": 1800,
                        "mode": "advise",
                        "workdir": "/tmp/project",
                        "candidate": "2/4",
                        "started": "2026-07-14T12:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
        elif isinstance(meta, str):
            (job / "meta.json").write_text(meta, encoding="utf-8")
        if task is not None:
            (job / "task.txt").write_text(task, encoding="utf-8")
        if output is not None:
            (job / "out.txt").write_text(output, encoding="utf-8")
        if pid is not None:
            (job / "pid").write_text(str(pid), encoding="ascii")
        if exit_code is not None:
            (job / "exit").write_text(str(exit_code), encoding="ascii")
        return job

    def test_classifies_job_states_without_exposing_bodies_in_summary(self):
        running = "20260714-120005-100-5"
        succeeded = "20260714-120004-100-4"
        failed = "20260714-120003-100-3"
        dead = "20260714-120002-100-2"
        starting = "20260714-120001-100-1"
        invalid = "20260714-120000-100-0"
        self.make_job(running, pid=os.getpid())
        self.make_job(succeeded, exit_code=0)
        self.make_job(failed, exit_code=7)
        self.make_job(dead, pid=2147483647)
        self.make_job(starting, meta=False, output=None)
        self.make_job(invalid, meta="{not-json", exit_code=0)

        payload = self.store.snapshot()
        by_id = {job["id"]: job for job in payload["jobs"]}

        self.assertEqual("running", by_id[running]["state"])
        self.assertEqual("succeeded", by_id[succeeded]["state"])
        self.assertEqual(0, by_id[succeeded]["exitCode"])
        self.assertEqual("failed", by_id[failed]["state"])
        self.assertEqual(7, by_id[failed]["exitCode"])
        self.assertEqual("dead", by_id[dead]["state"])
        self.assertEqual("starting", by_id[starting]["state"])
        self.assertEqual("invalid", by_id[invalid]["state"])
        encoded = json.dumps(payload)
        self.assertNotIn("TASK-CANARY", encoded)
        self.assertNotIn("OUTPUT-CANARY", encoded)

    def test_newest_fifty_only(self):
        for number in range(55):
            self.make_job(
                "20260714-120000-200-{:02d}".format(number),
                task=None,
                output=None,
                exit_code=0,
            )

        jobs = self.store.snapshot()["jobs"]

        self.assertEqual(50, len(jobs))
        self.assertEqual("20260714-120000-200-54", jobs[0]["id"])
        self.assertEqual("20260714-120000-200-05", jobs[-1]["id"])

    def test_detail_caps_large_text_head_and_tail(self):
        job_id = "20260714-120000-300-1"
        large = "H" * (384 * 1024) + "M" * (100 * 1024) + "T" * (128 * 1024)
        self.make_job(job_id, task="hello", output=large, exit_code=0)

        detail = self.store.detail(job_id)

        self.assertTrue(detail["outputTruncated"])
        self.assertTrue(detail["output"].startswith("H" * 100))
        self.assertIn("TRUNCATED", detail["output"])
        self.assertTrue(detail["output"].endswith("T" * 100))
        self.assertLessEqual(len(detail["output"].encode("utf-8")), 513 * 1024)

    def test_invalid_utf8_is_replaced(self):
        job_id = "20260714-120000-400-1"
        job = self.make_job(job_id, task=None, output=None, exit_code=0)
        (job / "task.txt").write_bytes(b"before\xffafter")

        detail = self.store.detail(job_id)

        self.assertEqual("before\ufffdafter", detail["task"])

    def test_rejects_traversal_and_symlinked_content(self):
        job_id = "20260714-120000-500-1"
        secret = self.home / "secret.txt"
        secret.write_text("DO-NOT-SERVE", encoding="utf-8")
        job = self.make_job(job_id, output=None, exit_code=0)
        (job / "out.txt").symlink_to(secret)
        linked_job = self.jobs / "20260714-120000-500-2"
        linked_job.symlink_to(self.home, target_is_directory=True)

        detail = self.store.detail(job_id)
        encoded = json.dumps(detail)

        self.assertNotIn("DO-NOT-SERVE", encoded)
        self.assertIn("out.txt", detail["invalidFiles"])
        self.assertNotIn(linked_job.name, [item["id"] for item in self.store.snapshot()["jobs"]])
        for bad_id in ("../secret.txt", "%2e%2e", "20260714-120000-1-1/../../x", "not-a-job"):
            with self.assertRaises(ui.JobNotFound):
                self.store.detail(bad_id)

    def test_oversized_control_files_fail_closed(self):
        meta_id = "20260714-120000-600-1"
        pid_id = "20260714-120000-600-2"
        exit_id = "20260714-120000-600-3"
        meta_job = self.make_job(meta_id, meta=False, task=None, output=None)
        with (meta_job / "meta.json").open("wb") as handle:
            handle.seek(128 * 1024)
            handle.write(b"x")
        pid_job = self.make_job(pid_id, pid=None, task=None, output=None)
        (pid_job / "pid").write_text("9" * 1000, encoding="ascii")
        exit_job = self.make_job(exit_id, task=None, output=None)
        (exit_job / "exit").write_text("9" * 1000, encoding="ascii")

        by_id = {job["id"]: job for job in self.store.snapshot()["jobs"]}

        self.assertEqual("invalid", by_id[meta_id]["state"])
        self.assertEqual("invalid", by_id[pid_id]["state"])
        self.assertEqual("invalid", by_id[exit_id]["state"])


class HTTPServerTests(unittest.TestCase):
    REQUIRED_HEADERS = (
        "Cache-Control",
        "Content-Security-Policy",
        "Referrer-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
    )

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="omnilane-ui-http-")
        self.home = Path(self.tempdir.name)
        self.jobs = self.home / "jobs"
        self.static = self.home / "static"
        self.jobs.mkdir()
        self.static.mkdir()
        (self.static / "index.html").write_text("<h1>LOCAL BOARD</h1>", encoding="utf-8")
        (self.static / "styles.css").write_text("body{}", encoding="utf-8")
        (self.static / "app.js").write_text("'use strict';", encoding="utf-8")
        self.job_id = "20260714-130000-700-1"
        job = self.jobs / self.job_id
        job.mkdir()
        (job / "meta.json").write_text(
            json.dumps(
                {
                    "lane": "hard-judgment",
                    "vendor": "claude",
                    "model": "claude-opus-4-8",
                    "effort": "high",
                    "timeout": 1800,
                    "mode": "advise",
                    "workdir": "/tmp/project",
                    "candidate": "2/4",
                    "started": "2026-07-14T13:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        (job / "task.txt").write_text("HTTP-TASK-CANARY", encoding="utf-8")
        (job / "out.txt").write_text("HTTP-OUTPUT-CANARY", encoding="utf-8")
        (job / "exit").write_text("0", encoding="ascii")
        self.token = "test-token-that-is-not-logged"
        self.server = ui.LiveHTTPServer(
            ("127.0.0.1", 0),
            ui.JobStore(self.jobs),
            self.token,
            "test-server-id",
            self.static,
            poll_interval=0.05,
            keepalive_interval=0.5,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.stop()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tempdir.cleanup()

    def request(self, method, path, *, token=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if host is not None:
            headers["Host"] = host
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        return connection, response, body

    def assert_security_headers(self, response):
        for header in self.REQUIRED_HEADERS:
            self.assertTrue(response.getheader(header), header)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    @staticmethod
    def read_sse_event(response):
        event_name = None
        data = None
        while True:
            line = response.fp.readline()
            if not line:
                raise AssertionError("SSE stream ended before an event")
            decoded = line.decode("utf-8").rstrip("\r\n")
            if decoded.startswith("event: "):
                event_name = decoded[7:]
            elif decoded.startswith("data: "):
                data = json.loads(decoded[6:])
            elif decoded == "" and data is not None:
                return event_name, data

    def open_sse(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        path = "/api/events?token=" + self.token
        connection.request("GET", path)
        response = connection.getresponse()
        return connection, response

    def test_static_auth_host_methods_and_detail_contract(self):
        connection, response, body = self.request("GET", "/")
        self.assertEqual(200, response.status)
        self.assertIn(b"LOCAL BOARD", body)
        self.assert_security_headers(response)
        connection.close()

        connection, response, _ = self.request("GET", "/api/jobs")
        self.assertEqual(401, response.status)
        self.assert_security_headers(response)
        connection.close()

        connection, response, body = self.request(
            "GET", "/api/jobs", token=self.token
        )
        self.assertEqual(200, response.status)
        self.assertNotIn(b"HTTP-TASK-CANARY", body)
        self.assertNotIn(b"HTTP-OUTPUT-CANARY", body)
        self.assert_security_headers(response)
        connection.close()

        connection, response, body = self.request(
            "GET", "/api/jobs/" + self.job_id, token=self.token
        )
        self.assertEqual(200, response.status)
        self.assertIn(b"HTTP-TASK-CANARY", body)
        self.assertIn(b"HTTP-OUTPUT-CANARY", body)
        connection.close()

        connection, response, _ = self.request(
            "GET", "/api/jobs/%2e%2e%2fsecret", token=self.token
        )
        self.assertEqual(404, response.status)
        self.assert_security_headers(response)
        connection.close()

        connection, response, _ = self.request(
            "POST", "/api/jobs", token=self.token
        )
        self.assertEqual(405, response.status)
        self.assert_security_headers(response)
        connection.close()

        connection, response, _ = self.request("TRACE", "/api/jobs")
        self.assertEqual(405, response.status)
        self.assert_security_headers(response)
        connection.close()

        connection, response, _ = self.request(
            "GET",
            "/api/jobs",
            token=self.token,
            host="attacker.invalid",
        )
        self.assertEqual(421, response.status)
        self.assert_security_headers(response)
        connection.close()

    def test_health_and_unknown_route(self):
        connection, response, body = self.request(
            "GET", "/api/health", token=self.token
        )
        payload = json.loads(body)
        self.assertEqual(200, response.status)
        self.assertTrue(payload["ok"])
        self.assertEqual(os.getpid(), payload["pid"])
        self.assertEqual(self.port, payload["port"])
        self.assertEqual("test-server-id", payload["serverId"])
        connection.close()

        connection, response, _ = self.request(
            "GET", "/api/no-such-route", token=self.token
        )
        self.assertEqual(404, response.status)
        self.assert_security_headers(response)
        connection.close()

    def test_sse_shares_body_free_changed_snapshots(self):
        first_connection, first_response = self.open_sse()
        second_connection, second_response = self.open_sse()
        self.assertEqual(200, first_response.status)
        self.assertEqual(200, second_response.status)
        self.assert_security_headers(first_response)
        first_initial = self.read_sse_event(first_response)
        second_initial = self.read_sse_event(second_response)
        self.assertEqual(first_initial, second_initial)
        encoded = json.dumps(first_initial)
        self.assertNotIn("HTTP-TASK-CANARY", encoded)
        self.assertNotIn("HTTP-OUTPUT-CANARY", encoded)
        changes_before = self.server.broadcaster.change_count

        with (self.jobs / self.job_id / "out.txt").open("a", encoding="utf-8") as handle:
            handle.write(" changed")

        first_changed = self.read_sse_event(first_response)
        second_changed = self.read_sse_event(second_response)
        self.assertEqual(first_changed, second_changed)
        self.assertEqual(changes_before + 1, self.server.broadcaster.change_count)
        encoded = json.dumps(first_changed)
        self.assertNotIn("HTTP-TASK-CANARY", encoded)
        self.assertNotIn("HTTP-OUTPUT-CANARY", encoded)
        first_connection.close()
        second_connection.close()

    def test_ninth_sse_connection_is_rejected(self):
        streams = []
        try:
            for _ in range(8):
                connection, response = self.open_sse()
                self.assertEqual(200, response.status)
                streams.append((connection, response))
            ninth_connection, ninth_response = self.open_sse()
            self.assertEqual(503, ninth_response.status)
            self.assert_security_headers(ninth_response)
            ninth_response.read()
            ninth_connection.close()
        finally:
            for connection, _response in streams:
                connection.close()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="omnilane-ui-life-")
        self.home = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["OMNILANE_HOME"] = str(self.home)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def tearDown(self):
        self.run_ui("stop", check=False)
        self.tempdir.cleanup()

    def run_ui(self, *arguments, check=True, timeout=12):
        result = subprocess.run(
            [sys.executable, str(UI_SCRIPT)] + list(arguments),
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            self.fail(
                "ui command failed {}: {}".format(
                    result.returncode, result.stderr or result.stdout
                )
            )
        return result

    @staticmethod
    def url_from_output(output):
        match = re.search(r"http://127\.0\.0\.1:[0-9]+/#token=[A-Za-z0-9_-]+", output)
        if not match:
            raise AssertionError("missing Live UI URL: " + output)
        return match.group(0)

    @staticmethod
    def matching_server_processes():
        listing = subprocess.check_output(
            ["ps", "-ax", "-o", "pid=,command="], text=True
        )
        needle = str(UI_SCRIPT)
        return {
            int(line.split(None, 1)[0])
            for line in listing.splitlines()
            if needle in line and " serve " in line
        }

    def test_start_reuse_status_url_stop_and_permissions(self):
        jobs = self.home / "jobs"
        job_id = "20260714-140000-800-1"
        job = jobs / job_id
        job.mkdir(parents=True)
        (job / "meta.json").write_text(
            json.dumps({"lane": "triage", "vendor": "codex", "timeout": 600}),
            encoding="utf-8",
        )
        (job / "task.txt").write_text("LIFECYCLE-TASK-CANARY", encoding="utf-8")
        (job / "out.txt").write_text("LIFECYCLE-OUTPUT-CANARY", encoding="utf-8")
        (job / "exit").write_text("0", encoding="ascii")

        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        busy_port = busy.getsockname()[1]
        try:
            started = self.run_ui("start", "--port", str(busy_port))
        finally:
            busy.close()

        url = self.url_from_output(started.stdout)
        state_path = self.home / "ui" / "state.json"
        log_path = self.home / "ui" / "server.log"
        lock_path = self.home / "ui" / "lifecycle.lock"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotEqual(busy_port, state["port"])
        self.assertEqual(0o700, stat.S_IMODE((self.home / "ui").stat().st_mode))
        for path in (state_path, log_path, lock_path):
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode), str(path))
        command = subprocess.check_output(
            ["ps", "-p", str(state["pid"]), "-o", "command="], text=True
        )
        self.assertNotIn(state["token"], command)

        status_result = self.run_ui("status")
        self.assertIn("running", status_result.stdout)
        self.assertNotIn("http://", status_result.stdout)
        self.assertNotIn(state["token"], status_result.stdout)
        url_result = self.run_ui("url")
        self.assertEqual(url, url_result.stdout.strip())
        reused = self.run_ui("start")
        self.assertEqual(url, self.url_from_output(reused.stdout))
        state_after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["serverId"], state_after["serverId"])

        connection = http.client.HTTPConnection(
            "127.0.0.1", state["port"], timeout=3
        )
        connection.request(
            "GET",
            "/api/jobs/" + job_id,
            headers={"Authorization": "Bearer " + state["token"]},
        )
        response = connection.getresponse()
        self.assertEqual(200, response.status)
        response.read()
        connection.close()
        stream = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=3)
        stream.request("GET", "/api/events?token=" + state["token"])
        stream_response = stream.getresponse()
        self.assertEqual(200, stream_response.status)
        stream.close()

        stopped = self.run_ui("stop")
        self.assertIn("stopped", stopped.stdout)
        self.assertFalse(state_path.exists())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", state["port"]), timeout=0.5)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(state["token"], log_text)
        self.assertNotIn("?token=", log_text)
        self.assertNotIn("LIFECYCLE-TASK-CANARY", log_text)
        self.assertNotIn("LIFECYCLE-OUTPUT-CANARY", log_text)

    def test_concurrent_starts_create_one_server(self):
        before = self.matching_server_processes()
        command = [sys.executable, str(UI_SCRIPT), "start", "--port", "0"]
        first = subprocess.Popen(
            command,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        second = subprocess.Popen(
            command,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        first_out, first_err = first.communicate(timeout=12)
        second_out, second_err = second.communicate(timeout=12)
        self.assertEqual(0, first.returncode, first_err)
        self.assertEqual(0, second.returncode, second_err)
        self.assertEqual(self.url_from_output(first_out), self.url_from_output(second_out))
        new_servers = self.matching_server_processes() - before
        self.assertEqual(1, len(new_servers))

        self.run_ui("stop")
        deadline = time.time() + 3
        while time.time() < deadline and (
            self.matching_server_processes() & new_servers
        ):
            time.sleep(0.05)
        self.assertFalse(self.matching_server_processes() & new_servers)

    def test_stale_state_never_signals_recorded_pid(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        runtime = self.home / "ui"
        runtime.mkdir(mode=0o700)
        state_path = runtime / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "apiVersion": 1,
                    "serverId": "stale-server",
                    "token": "stale-token-value",
                    "pid": sleeper.pid,
                    "port": 65534,
                    "started": "2026-07-14T14:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        try:
            result = self.run_ui("stop")
            self.assertIn("stale", result.stdout)
            self.assertIsNone(sleeper.poll())
            self.assertFalse(state_path.exists())
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=3)

    def test_global_entrypoint_routes_ui(self):
        result = subprocess.run(
            [str(ROOT / "bin" / "omnilane"), "ui", "status"],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("stopped", result.stdout)
        help_result = subprocess.run(
            [str(ROOT / "bin" / "omnilane"), "help"],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        self.assertIn("omnilane ui", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
