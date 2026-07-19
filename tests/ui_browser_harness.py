"""Reusable real-Chrome fixture for Live UI behavior tests."""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only on minimal CI hosts
    sync_playwright = None


use_playwright_browser = os.environ.get("OMNILANE_TEST_USE_PLAYWRIGHT_BROWSER") == "1"
requested_browser = os.environ.get("OMNILANE_TEST_BROWSER", "")
if use_playwright_browser:
    browser_executable = None
elif requested_browser:
    browser_executable = Path(requested_browser)
else:
    browser_executable = Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
browser_available = sync_playwright is not None and (
    use_playwright_browser
    or (browser_executable is not None and browser_executable.is_file())
)


class BrowserHarness:
    """Owns an isolated JobStore, loopback server, and Chrome page per test."""

    root = None
    ui_module = None

    @classmethod
    def setUpClass(cls):
        launch_options = {
            "headless": True,
            "args": ["--disable-background-networking"],
        }
        if browser_executable is not None:
            launch_options["executable_path"] = str(browser_executable)
        # Chrome launch is occasionally flaky under load (resource/timing). Retry
        # a few times, then skip rather than let a transient launch failure surface
        # as a spurious class error. Real page and assertion failures still fail.
        cls.playwright = None
        cls.browser = None
        last_error = None
        for attempt in range(3):
            try:
                cls.playwright = sync_playwright().start()
                cls.browser = cls.playwright.chromium.launch(**launch_options)
                return
            except Exception as error:  # noqa: BLE001 - launch raises varied types
                last_error = error
                if cls.playwright is not None:
                    try:
                        cls.playwright.stop()
                    except Exception:
                        pass
                    cls.playwright = None
                time.sleep(0.5 * (attempt + 1))
        # The skip keeps transient launch flakes from failing the suite, but it
        # would also hide a persistently broken environment; set
        # OMNILANE_TEST_REQUIRE_BROWSER=1 (e.g. on a host known to have Chrome)
        # to surface the launch failure instead.
        if os.environ.get("OMNILANE_TEST_REQUIRE_BROWSER") == "1":
            raise last_error
        raise unittest.SkipTest(
            "Chrome/Playwright did not launch after 3 attempts: {}".format(last_error)
        )

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.close()
        playwright = getattr(cls, "playwright", None)
        if playwright is not None:
            playwright.stop()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="omnilane-ui-browser-")
        self.home = Path(self.tempdir.name)
        self.jobs = self.home / "jobs"
        self.jobs.mkdir()
        self.job_ids = []
        for number in range(14):
            job_id = "20260715-090000-900-{:02d}".format(number)
            self.job_ids.append(job_id)
            job = self.jobs / job_id
            job.mkdir()
            (job / "meta.json").write_text(
                json.dumps(
                    {
                        "lane": "hard-judgment" if number % 2 else "triage",
                        "vendor": "codex",
                        "model": "gpt-5.6-sol",
                        "effort": "high",
                        "timeout": 1800,
                        "mode": "advise",
                        "workdir": "/tmp/project-{}".format(number),
                        "candidate": "1/1",
                        "started": "2026-07-15T09:{:02d}:00Z".format(number),
                    }
                ),
                encoding="utf-8",
            )
            (job / "task.txt").write_text(
                "Investigate payment reconciliation task {:02d} and report the exact local evidence.".format(number),
                encoding="utf-8",
            )
            (job / "out.txt").write_text(
                "Result {:02d}\n".format(number) + ("bounded output line\n" * 90),
                encoding="utf-8",
            )
            if number == 13:
                (job / "pid").write_text(str(os.getpid()), encoding="ascii")
            else:
                (job / "exit").write_text("0", encoding="ascii")

        self.token = "browser-test-token"
        self._start_server("browser-test-server")
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)

    def tearDown(self):
        self.context.close()
        self.stop_server()
        self.tempdir.cleanup()

    def _start_server(self, server_id):
        self.server = self.ui_module.LiveHTTPServer(
            ("127.0.0.1", getattr(self, "port", 0)),
            self.ui_module.JobStore(self.jobs),
            self.token,
            server_id,
            self.root / "ui",
            poll_interval=0.05,
            keepalive_interval=0.25,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def stop_server(self):
        if self.server is None:
            return
        self.server.stop()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.server = None

    def restart_server(self):
        self._start_server("browser-test-server-restarted")

    def open_board(self, width, height):
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.goto(
            "http://127.0.0.1:{}/#token={}".format(self.port, self.token),
            wait_until="domcontentloaded",
        )
        self.page.locator(".job-card").first.wait_for(state="visible")
        self.page.locator('#connection-status[data-mode="live"]').wait_for()
