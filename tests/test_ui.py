#!/usr/bin/env python3
"""Tests for the optional local Omnilane Live UI."""

import importlib.util
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.ui_browser_harness import BrowserHarness, browser_available


ROOT = Path(__file__).resolve().parents[1]
UI_SCRIPT = ROOT / "scripts" / "ui.py"
SPEC = importlib.util.spec_from_file_location("omnilane_ui", UI_SCRIPT)
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class BrowserHarnessConfigurationTests(unittest.TestCase):
    def test_ci_can_select_playwright_bundled_chromium(self):
        env = os.environ.copy()
        env["OMNILANE_TEST_USE_PLAYWRIGHT_BROWSER"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from tests import ui_browser_harness as h; "
                    "print(h.browser_executable or 'bundled')"
                ),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("bundled", result.stdout.strip())


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

    def test_head_has_no_response_body(self):
        client = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        request = (
            "HEAD /api/jobs HTTP/1.1\r\n"
            "Host: 127.0.0.1:{}\r\n"
            "Connection: close\r\n\r\n"
        ).format(self.port)
        client.sendall(request.encode("ascii"))
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        client.close()
        headers, body = b"".join(chunks).split(b"\r\n\r\n", 1)
        self.assertIn(b"405 Method Not Allowed", headers)
        self.assertEqual(b"", body)

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
            for connection, response in streams:
                response.close()
                connection.close()
        recovered = False
        deadline = time.time() + 2
        while time.time() < deadline and not recovered:
            connection, response = self.open_sse()
            if response.status == 200:
                recovered = True
            else:
                response.read()
            connection.close()
            if not recovered:
                time.sleep(0.05)
        self.assertTrue(recovered, "SSE capacity did not recover after clients closed")


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

    def test_unresponsive_recorded_server_retains_state(self):
        self.run_ui("start", "--port", "0")
        state_path = self.home / "ui" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        os.kill(state["pid"], signal.SIGSTOP)
        try:
            stopped = self.run_ui("stop", check=False)
            self.assertEqual(1, stopped.returncode)
            self.assertIn("state retained", stopped.stderr)
            self.assertTrue(state_path.exists())
        finally:
            os.kill(state["pid"], signal.SIGCONT)
            deadline = time.time() + 3
            while time.time() < deadline:
                if self.run_ui("status", check=False).returncode == 0:
                    break
                time.sleep(0.05)
            self.run_ui("stop", check=False)

    def test_unresponsive_recorded_server_blocks_replacement(self):
        self.run_ui("start", "--port", "0")
        state_path = self.home / "ui" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        os.kill(state["pid"], signal.SIGSTOP)
        try:
            restarted = self.run_ui("start", "--port", "0", check=False)
            self.assertEqual(1, restarted.returncode)
            self.assertIn("state retained", restarted.stderr)
            retained = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["serverId"], retained["serverId"])
            self.assertEqual(state["pid"], retained["pid"])
        finally:
            os.kill(state["pid"], signal.SIGCONT)
            deadline = time.time() + 3
            while time.time() < deadline:
                if self.run_ui("status", check=False).returncode == 0:
                    break
                time.sleep(0.05)
            self.run_ui("stop", check=False)

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


@unittest.skipUnless(browser_available, "a Playwright browser is required")
class FrontendBrowserBehaviorTests(BrowserHarness, unittest.TestCase):
    root, ui_module = ROOT, ui
    def test_desktop_layout_and_sse_reconcile_preserve_dom_focus_and_scroll(self):
        self.open_board(1440, 900)
        metrics = self.page.evaluate(
            """
            () => {
              const header = document.querySelector('.board-header');
              const index = document.querySelector('.dispatch-index');
              const list = document.querySelector('.job-list');
              const inspector = document.querySelector('.job-inspector');
              return {
                headerHeight: header.getBoundingClientRect().height,
                indexWidth: index.getBoundingClientRect().width,
                listClient: list.clientHeight,
                listScroll: list.scrollHeight,
                inspectorOverflow: getComputedStyle(inspector).overflowY,
                bodyOverflow: getComputedStyle(document.body).overflowY,
              };
            }
            """
        )
        self.assertGreaterEqual(metrics["headerHeight"], 52)
        self.assertLessEqual(metrics["headerHeight"], 56)
        self.assertGreaterEqual(metrics["indexWidth"], 320)
        self.assertLessEqual(metrics["indexWidth"], 400)
        self.assertGreater(metrics["listScroll"], metrics["listClient"])
        self.assertEqual("auto", metrics["inspectorOverflow"])
        self.assertEqual("hidden", metrics["bodyOverflow"])

        first = self.page.locator(".job-card").first
        first.focus()
        self.page.evaluate(
            """
            () => {
              const list = document.querySelector('.job-list');
              list.scrollTop = 96;
              window.__firstJobNode = document.querySelector('.job-card');
              window.__listScrollBefore = list.scrollTop;
            }
            """
        )
        newest = self.jobs / self.job_ids[-1]
        (newest / "exit").write_text("0", encoding="ascii")
        self.page.locator(".job-card").first.locator(".card-state").filter(
            has_text="succeeded"
        ).wait_for()
        preserved = self.page.evaluate(
            """
            () => ({
              sameNode: window.__firstJobNode === document.querySelector('.job-card'),
              focused: document.activeElement === document.querySelector('.job-card'),
              activeTag: document.activeElement.tagName,
              activeClass: document.activeElement.className,
              scrollTop: document.querySelector('.job-list').scrollTop,
              expectedScroll: window.__listScrollBefore,
            })
            """
        )
        self.assertTrue(preserved["sameNode"], preserved)
        self.assertTrue(preserved["focused"], preserved)
        self.assertAlmostEqual(
            preserved["expectedScroll"], preserved["scrollTop"], delta=1
        )

    def test_mobile_master_detail_back_escape_and_restoration(self):
        self.open_board(390, 844)
        self.page.locator('body[data-mobile-view="list"]').wait_for()
        rows = self.page.locator(".job-card")
        target = rows.nth(5)
        target.focus()
        self.page.evaluate(
            """
            () => {
              const list = document.querySelector('.job-list');
              list.scrollTop = 110;
              window.__mobileExpectedScroll = list.scrollTop;
              window.__mobileExpectedJob = document.activeElement.dataset.jobId;
            }
            """
        )
        target.press("Enter")
        self.page.locator('body[data-mobile-view="detail"]').wait_for()
        back = self.page.locator("#mobile-back")
        self.assertTrue(back.is_visible())
        self.assertGreaterEqual(back.evaluate("node => node.getBoundingClientRect().height"), 44)

        self.page.evaluate("history.back()")
        self.page.locator('body[data-mobile-view="list"]').wait_for()
        self.page.wait_for_function(
            """
            () => document.activeElement &&
              document.activeElement.dataset.jobId === window.__mobileExpectedJob
            """
        )
        restored = self.page.evaluate(
            """
            () => ({
              scrollTop: document.querySelector('.job-list').scrollTop,
              expectedScroll: window.__mobileExpectedScroll,
              focusedJob: document.activeElement.dataset.jobId || null,
              expectedJob: window.__mobileExpectedJob,
            })
            """
        )
        self.assertAlmostEqual(restored["expectedScroll"], restored["scrollTop"], delta=1)
        self.assertEqual(restored["expectedJob"], restored["focusedJob"])

        self.page.locator('.job-card[data-job-id="{}"]'.format(restored["expectedJob"])).press("Enter")
        self.page.locator('body[data-mobile-view="detail"]').wait_for()
        self.page.keyboard.press("Escape")
        self.page.locator('body[data-mobile-view="list"]').wait_for()
        for selector in ("#job-search", ".filter-button"):
            self.assertGreaterEqual(
                self.page.locator(selector).first.evaluate(
                    "node => node.getBoundingClientRect().height"
                ),
                44,
            )

    def test_auth_probe_403_clears_snapshot_token_and_reconnect(self):
        deny_health = {"value": False}

        def route_request(route):
            if deny_health["value"] and "/api/health" in route.request.url:
                route.fulfill(
                    status=403,
                    content_type="application/json",
                    body='{"ok":false}',
                )
            else:
                route.continue_()

        self.page.route("**/*", route_request)
        self.open_board(1440, 900)
        self.assertGreater(self.page.locator(".job-card").count(), 0)
        deny_health["value"] = True
        self.stop_server()

        self.page.locator('#connection-status[data-mode="unauthorized"]').wait_for(
            timeout=6000
        )
        self.assertEqual(0, self.page.locator(".job-card").count())
        self.assertIsNone(
            self.page.evaluate(
                "sessionStorage.getItem('omnilane.live-ui.token')"
            )
        )
        self.page.wait_for_timeout(3300)
        self.assertEqual("unauthorized", self.page.locator("#connection-status").get_attribute("data-mode"))

    def test_short_disconnect_keeps_snapshot_and_recovers(self):
        self.open_board(1440, 900)
        visible_jobs = self.page.locator(".job-card").count()
        self.stop_server()
        self.page.locator('#connection-status[data-mode="reconnecting"]').wait_for(
            timeout=5000
        )
        self.assertEqual(visible_jobs, self.page.locator(".job-card").count())

        self.restart_server()
        self.page.locator('#connection-status[data-mode="live"]').wait_for(timeout=7000)
        self.assertEqual(visible_jobs, self.page.locator(".job-card").count())

    def test_detail_cache_bounds_requests_and_rejects_stale_selection(self):
        delayed_id = self.job_ids[1]
        self.page.add_init_script(
            """
            (() => {
              const delayedId = %s;
              const originalFetch = window.fetch.bind(window);
              window.__detailFetchCount = {};
              window.fetch = (input, init) => {
                const url = String(input);
                const marker = "/api/jobs/";
                const markerIndex = url.indexOf(marker);
                if (markerIndex >= 0) {
                  const jobId = decodeURIComponent(
                    url.slice(markerIndex + marker.length).split("?")[0].split("#")[0]
                  );
                  window.__detailFetchCount[jobId] = (window.__detailFetchCount[jobId] || 0) + 1;
                  if (jobId === delayedId) {
                    return new Promise((resolve, reject) => {
                      setTimeout(() => originalFetch(input, init).then(resolve, reject), 450);
                    });
                  }
                }
                return originalFetch(input, init);
              };
            })();
            """ % json.dumps(delayed_id),
        )
        self.open_board(1440, 900)
        self.page.wait_for_function(
            "() => document.querySelector('.card-task').textContent.startsWith('Investigate')"
        )
        newest = self.job_ids[-1]
        second = self.job_ids[-2]
        before = self.page.evaluate(
            "ids => ids.map(id => window.__detailFetchCount[id] || 0)",
            [newest, second],
        )
        for job_id in (newest, second, newest, second):
            self.page.locator('.job-card[data-job-id="{}"]'.format(job_id)).click()
        self.page.wait_for_timeout(200)
        after = self.page.evaluate(
            "ids => ids.map(id => window.__detailFetchCount[id] || 0)",
            [newest, second],
        )
        self.assertEqual(before, after)

        final_id = self.job_ids[0]
        self.page.locator('.job-card[data-job-id="{}"]'.format(delayed_id)).click()
        self.page.locator('.job-card[data-job-id="{}"]'.format(final_id)).click()
        self.page.wait_for_function(
            "id => document.querySelector('#selected-job-id').textContent === id",
            arg=final_id,
        )
        self.page.wait_for_timeout(700)
        self.assertEqual(final_id, self.page.locator("#selected-job-id").text_content())
        self.assertIn("task 00", self.page.locator("#request-content").text_content())
        counts = self.page.evaluate(
            "ids => ids.map(id => window.__detailFetchCount[id] || 0)",
            [delayed_id, final_id],
        )
        self.assertEqual([1, 1], counts)

    def test_output_updates_only_follow_when_reader_was_at_bottom(self):
        self.open_board(1440, 900)
        result = self.page.locator("#result-content")
        result.wait_for(state="visible")
        newest = self.jobs / self.job_ids[-1]

        result.evaluate("node => { node.scrollTop = 0; }")
        with (newest / "out.txt").open("a", encoding="utf-8") as handle:
            handle.write("NONBOTTOM-MARKER\n")
        (newest / "exit").write_text("0", encoding="ascii")
        self.page.wait_for_function(
            "() => document.querySelector('#result-content').textContent.includes('NONBOTTOM-MARKER')"
        )
        self.assertLessEqual(result.evaluate("node => node.scrollTop"), 1)

        (newest / "exit").unlink()
        self.page.locator(".job-card").first.locator(".card-state").filter(
            has_text="running"
        ).wait_for()
        result.evaluate("node => { node.scrollTop = node.scrollHeight; }")
        with (newest / "out.txt").open("a", encoding="utf-8") as handle:
            handle.write("BOTTOM-MARKER\n")
        (newest / "exit").write_text("0", encoding="ascii")
        self.page.wait_for_function(
            "() => document.querySelector('#result-content').textContent.includes('BOTTOM-MARKER')"
        )
        distance = result.evaluate(
            "node => node.scrollHeight - node.clientHeight - node.scrollTop"
        )
        self.assertLessEqual(distance, 2)

    def test_compare_pins_reference_snapshot_and_renders_untrusted_output_as_text(self):
        newest = self.jobs / self.job_ids[-1]
        (newest / "out.txt").write_text(
            '<img src=x onerror="window.__compareInjected=true">COMPARE-CANARY',
            encoding="utf-8",
        )
        self.open_board(1440, 900)

        selected = self.page.locator("#selected-job-id").text_content()
        self.assertEqual(self.job_ids[-1], selected)
        reference_state = self.page.locator("#selected-job-state").text_content()
        compare_button = self.page.locator("#compare-toggle")
        self.page.wait_for_function(
            "() => document.querySelector('#compare-toggle').disabled === false"
        )
        compare_button.click()
        self.assertEqual("true", compare_button.get_attribute("aria-pressed"))
        self.assertIn(selected, self.page.locator("#compare-reference-label").text_content())

        current = self.job_ids[-2]
        self.page.locator('.job-card[data-job-id="{}"]'.format(current)).click()
        panel = self.page.locator("#compare-panel")
        panel.wait_for(state="visible")
        self.assertEqual(selected, self.page.locator("#compare-reference-id").text_content())
        self.assertEqual(current, self.page.locator("#compare-current-id").text_content())
        self.assertEqual("hard-judgment", self.page.locator("#compare-reference-lane").text_content())
        self.assertEqual("triage", self.page.locator("#compare-current-lane").text_content())
        self.assertIn("COMPARE-CANARY", self.page.locator("#compare-reference-output").text_content())
        self.assertEqual(0, panel.locator("img").count())
        self.assertIsNone(self.page.evaluate("window.__compareInjected || null"))

        (newest / "out.txt").write_text("MUTATED-AFTER-PIN", encoding="utf-8")
        (newest / "exit").write_text("7", encoding="ascii")
        self.page.locator('.job-card[data-job-id="{}"]'.format(selected)).locator(
            ".card-state"
        ).filter(has_text="failed").wait_for()
        self.assertEqual(reference_state, self.page.locator("#compare-reference-state").text_content())
        self.assertIn("COMPARE-CANARY", self.page.locator("#compare-reference-output").text_content())
        self.assertNotIn("MUTATED-AFTER-PIN", self.page.locator("#compare-reference-output").text_content())

        self.page.locator("#compare-clear").click()
        self.assertTrue(panel.is_hidden())
        self.assertEqual("false", compare_button.get_attribute("aria-pressed"))


class FrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.ui_root = ROOT / "ui"
        self.html = (self.ui_root / "index.html").read_text(encoding="utf-8")
        self.css = (self.ui_root / "styles.css").read_text(encoding="utf-8")
        self.javascript = (self.ui_root / "app.js").read_text(encoding="utf-8")

    def test_assets_are_local_and_render_untrusted_data_as_text(self):
        combined = self.html + "\n" + self.css + "\n" + self.javascript
        self.assertNotRegex(self.html, r'''(?:src|href)=["'](?:https?:)?//''')
        self.assertNotIn("@import", self.css)
        self.assertNotRegex(self.css, r"\burl\s*\(")
        for forbidden in (
            "innerHTML",
            "insertAdjacentHTML",
            "document.write",
            "eval(",
            "new Function",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertNotRegex(self.html, r"<style(?:\s|>)")
        self.assertNotRegex(self.html, r"<script(?![^>]*\bsrc=)[^>]*>")
        self.assertIn("textContent", self.javascript)

    def test_token_auth_and_live_event_contract_are_literal(self):
        for literal in (
            'fragment.get("token")',
            "window.sessionStorage.setItem",
            "window.history.replaceState",
            'Authorization: "Bearer " + state.token',
            'new EventSource("/api/events?token=" + encodeURIComponent(state.token))',
            'requestJson("/api/jobs")',
        ):
            self.assertIn(literal, self.javascript)
        self.assertNotIn("localStorage", self.javascript)
        self.assertIn("EventSource.CLOSED", self.javascript)
        self.assertIn("window.setTimeout", self.javascript)

    def test_visual_contract_is_quiet_text_first_and_reduced_motion(self):
        self.assertIn('class="routing-list"', self.html)
        self.assertNotIn('class="route-signal"', self.html)
        self.assertNotIn("@keyframes", self.css)
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.css)
        self.assertNotRegex(self.css, r"gradient\s*\(")
        self.assertNotIn("backdrop-filter", self.css)
        self.assertIn("--header-height: 54px", self.css)
        self.assertIn("clamp(320px, 26vw, 400px)", self.css)

    def test_javascript_dom_ids_exist_in_html(self):
        referenced = set(
            re.findall(r'document\.getElementById\("([A-Za-z0-9_-]+)"\)', self.javascript)
        )
        declared = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', self.html))
        self.assertTrue(referenced)
        self.assertEqual(set(), referenced - declared)

    def test_compare_contract_is_read_only_accessible_and_memory_scoped(self):
        for literal in (
            'id="compare-toggle"',
            'aria-controls="compare-panel"',
            'id="compare-panel"',
            'id="compare-clear"',
            'id="compare-reference-output"',
            'id="compare-current-output"',
        ):
            self.assertIn(literal, self.html)
        self.assertIn("compareReference", self.javascript)
        self.assertIn("renderCompare", self.javascript)
        self.assertNotIn("sessionStorage.setItem(\"omnilane.live-ui.compare", self.javascript)
        self.assertNotRegex(self.javascript, r'fetch\([^\n]+(?:POST|PUT|PATCH|DELETE)')


if __name__ == "__main__":
    unittest.main()
