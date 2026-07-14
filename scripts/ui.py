#!/usr/bin/env python3
"""Local, read-only Omnilane job board.

Core dispatch remains Bash-only.  This optional module intentionally uses only
the Python standard library so `omnilane ui` has no package-manager runtime.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import argparse
from datetime import datetime, timezone
import errno
import fcntl
import heapq
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit


JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$")
META_LIMIT = 64 * 1024
INTEGER_LIMIT = 64
TEXT_LIMIT = 512 * 1024
TEXT_HEAD = 384 * 1024
TEXT_TAIL = 128 * 1024
PID_MAX = 2**31 - 1
INT_MIN = -(2**31)
INT_MAX = 2**31 - 1
ALLOWED_FILES = ("meta.json", "task.txt", "pid", "exit", "out.txt")
STRING_META_FIELDS = (
    "lane",
    "vendor",
    "model",
    "effort",
    "mode",
    "workdir",
    "candidate",
    "started",
)


class JobNotFound(Exception):
    """The requested job ID is invalid or no longer names a safe directory."""


@dataclass
class FileRead:
    status: str
    data: bytes = b""
    signal: object = None
    truncated: bool = False


class JobStore:
    """Read the canonical jobs directory without following job-owned links."""

    def __init__(self, jobs_root):
        self.jobs_root = Path(jobs_root).expanduser().absolute()
        self._directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        self._file_flags = (
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

    def snapshot(self):
        jobs = []
        for job_id in self._newest_job_ids():
            try:
                with self._job_fd(job_id) as job_fd:
                    jobs.append(self._summary_from_fd(job_id, job_fd))
            except JobNotFound:
                # A runner or cleanup may remove a directory between scan/open.
                continue
        return {"ok": True, "jobs": jobs}

    def detail(self, job_id):
        if not JOB_ID_RE.fullmatch(job_id or ""):
            raise JobNotFound(job_id)
        with self._job_fd(job_id) as job_fd:
            summary = self._summary_from_fd(job_id, job_fd)
            task = self._read_text(job_fd, "task.txt")
            output = self._read_text(job_fd, "out.txt")
        invalid_files = []
        if task.status not in ("ok", "missing"):
            invalid_files.append("task.txt")
        if output.status not in ("ok", "missing"):
            invalid_files.append("out.txt")
        return {
            "summary": summary,
            "task": self._decode_text(task),
            "output": self._decode_text(output),
            "taskTruncated": task.truncated,
            "outputTruncated": output.truncated,
            "invalidFiles": invalid_files,
        }

    def _newest_job_ids(self):
        try:
            entries = os.scandir(self.jobs_root)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return []
        with entries:
            names = (
                entry.name
                for entry in entries
                if JOB_ID_RE.fullmatch(entry.name)
                and entry.is_dir(follow_symlinks=False)
            )
            return heapq.nlargest(50, names)

    @contextmanager
    def _job_fd(self, job_id):
        if not JOB_ID_RE.fullmatch(job_id or ""):
            raise JobNotFound(job_id)
        root_fd = None
        job_fd = None
        try:
            root_fd = os.open(str(self.jobs_root), self._directory_flags)
            job_flags = self._directory_flags | getattr(os, "O_NOFOLLOW", 0)
            job_fd = os.open(job_id, job_flags, dir_fd=root_fd)
            if not stat.S_ISDIR(os.fstat(job_fd).st_mode):
                raise JobNotFound(job_id)
            yield job_fd
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            if isinstance(exc, JobNotFound):
                raise
            raise JobNotFound(job_id) from None
        finally:
            if job_fd is not None:
                os.close(job_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _open_regular(self, job_fd, filename):
        try:
            fd = os.open(filename, self._file_flags, dir_fd=job_fd)
        except FileNotFoundError:
            return None, FileRead("missing")
        except OSError:
            return None, FileRead("invalid")
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(fd)
                return None, FileRead("invalid")
            signal = {
                "size": info.st_size,
                "mtimeNs": info.st_mtime_ns,
                "ctimeNs": info.st_ctime_ns,
                "inode": info.st_ino,
            }
            return fd, FileRead("ok", signal=signal)
        except OSError:
            os.close(fd)
            return None, FileRead("invalid")

    def _read_small(self, job_fd, filename, limit):
        fd, result = self._open_regular(job_fd, filename)
        if fd is None:
            return result
        try:
            if result.signal["size"] > limit:
                return FileRead("oversized", signal=result.signal)
            data = self._read_up_to(fd, limit + 1)
            if len(data) > limit:
                return FileRead("oversized", signal=result.signal)
            return FileRead("ok", data=data, signal=result.signal)
        except OSError:
            return FileRead("invalid", signal=result.signal)
        finally:
            os.close(fd)

    def _read_text(self, job_fd, filename):
        fd, result = self._open_regular(job_fd, filename)
        if fd is None:
            return result
        try:
            size = result.signal["size"]
            if size <= TEXT_LIMIT:
                data = self._read_up_to(fd, TEXT_LIMIT + 1)
                if len(data) <= TEXT_LIMIT:
                    return FileRead("ok", data=data, signal=result.signal)
                size = max(size, len(data))

            marker = (
                "\n\n--- TRUNCATED (original: {} bytes) ---\n\n".format(size)
            ).encode("ascii")
            tail_budget = max(0, TEXT_LIMIT - TEXT_HEAD - len(marker))
            os.lseek(fd, 0, os.SEEK_SET)
            head = self._read_up_to(fd, TEXT_HEAD)
            os.lseek(fd, max(0, size - tail_budget), os.SEEK_SET)
            tail = self._read_up_to(fd, tail_budget)
            return FileRead(
                "ok",
                data=head + marker + tail,
                signal=result.signal,
                truncated=True,
            )
        except OSError:
            return FileRead("invalid", signal=result.signal)
        finally:
            os.close(fd)

    @staticmethod
    def _read_up_to(fd, count):
        chunks = []
        remaining = count
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _decode_text(result):
        if result.status != "ok":
            return ""
        return result.data.decode("utf-8", errors="replace")

    def _summary_from_fd(self, job_id, job_fd):
        signals = {}
        for filename in ALLOWED_FILES:
            fd, result = self._open_regular(job_fd, filename)
            if fd is not None:
                os.close(fd)
            if result.signal is not None:
                signals[filename] = result.signal
            elif result.status == "invalid":
                signals[filename] = {"invalid": True}

        metadata_result = self._read_small(job_fd, "meta.json", META_LIMIT)
        if metadata_result.status == "missing":
            return self._summary(job_id, "starting", None, {}, signals)
        if metadata_result.status != "ok":
            return self._summary(job_id, "invalid", None, {}, signals)
        try:
            raw_metadata = json.loads(metadata_result.data.decode("utf-8"))
            metadata = self._validated_metadata(raw_metadata)
        except (UnicodeDecodeError, ValueError, TypeError):
            return self._summary(job_id, "invalid", None, {}, signals)

        exit_result = self._read_small(job_fd, "exit", INTEGER_LIMIT)
        if exit_result.status == "ok":
            exit_code = self._parse_integer(exit_result.data, INT_MIN, INT_MAX)
            if exit_code is None:
                return self._summary(job_id, "invalid", None, metadata, signals)
            state = "succeeded" if exit_code == 0 else "failed"
            return self._summary(job_id, state, exit_code, metadata, signals)
        if exit_result.status not in ("missing",):
            return self._summary(job_id, "invalid", None, metadata, signals)

        pid_result = self._read_small(job_fd, "pid", INTEGER_LIMIT)
        if pid_result.status == "missing":
            return self._summary(job_id, "dead", None, metadata, signals)
        if pid_result.status != "ok":
            return self._summary(job_id, "invalid", None, metadata, signals)
        pid = self._parse_integer(pid_result.data, 1, PID_MAX)
        if pid is None:
            return self._summary(job_id, "invalid", None, metadata, signals)
        return self._summary(
            job_id,
            "running" if self._pid_exists(pid) else "dead",
            None,
            metadata,
            signals,
        )

    @staticmethod
    def _summary(job_id, state_name, exit_code, metadata, signals):
        return {
            "id": job_id,
            "state": state_name,
            "exitCode": exit_code,
            "meta": metadata,
            "signals": signals,
        }

    @staticmethod
    def _validated_metadata(value):
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        result = {}
        for field in STRING_META_FIELDS:
            if field not in value:
                continue
            item = value[field]
            limit = 8192 if field == "workdir" else 1024
            if not isinstance(item, str) or len(item) > limit:
                raise ValueError("invalid metadata field")
            result[field] = item
        if "timeout" in value:
            timeout = value["timeout"]
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout < 1
                or timeout > 2**63 - 1
            ):
                raise ValueError("invalid timeout")
            result["timeout"] = timeout
        return result

    @staticmethod
    def _parse_integer(data, minimum, maximum):
        try:
            text = data.decode("ascii").strip()
        except UnicodeDecodeError:
            return None
        if not re.fullmatch(r"-?[0-9]+", text):
            return None
        try:
            value = int(text, 10)
        except (ValueError, OverflowError):
            return None
        return value if minimum <= value <= maximum else None

    @staticmethod
    def _pid_exists(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            return exc.errno != errno.ESRCH
        except (OverflowError, ValueError):
            return False


SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; connect-src 'self'; script-src 'self'; "
        "style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class SnapshotBroadcaster:
    """Poll once and fan one cached, body-free snapshot out to every client."""

    def __init__(self, store, poll_interval=1.0):
        self.store = store
        self.poll_interval = poll_interval
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread = None
        self._snapshot = self.store.snapshot()
        self._encoded = self._encode(self._snapshot)
        self._version = 1
        self.scan_count = 1
        self.change_count = 1

    @staticmethod
    def _encode(snapshot):
        return json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="omnilane-ui-snapshots", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def current(self):
        with self._condition:
            return self._version, self._snapshot

    def wait_for_change(self, version, timeout):
        with self._condition:
            self._condition.wait_for(
                lambda: self._version != version or self._stop_event.is_set(),
                timeout=timeout,
            )
            return self._version, self._snapshot, self._version != version

    def _run(self):
        while not self._stop_event.wait(self.poll_interval):
            try:
                snapshot = self.store.snapshot()
                encoded = self._encode(snapshot)
            except Exception:
                # A transient filesystem race keeps the last known-good view.
                continue
            with self._condition:
                self.scan_count += 1
                if encoded == self._encoded:
                    continue
                self._snapshot = snapshot
                self._encoded = encoded
                self._version += 1
                self.change_count += 1
                self._condition.notify_all()


class LiveHTTPServer(ThreadingHTTPServer):
    """Bounded local HTTP service for static assets, JSON, and SSE."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16

    def __init__(
        self,
        server_address,
        job_store,
        token,
        server_id,
        static_root,
        *,
        poll_interval=1.0,
        keepalive_interval=3.0,
    ):
        host, _port = server_address
        if host != "127.0.0.1":
            raise ValueError("Live UI must bind to 127.0.0.1")
        self.job_store = job_store
        self.token = token
        self.server_id = server_id
        self.static_root = Path(static_root).absolute()
        self.keepalive_interval = keepalive_interval
        self.stop_event = threading.Event()
        self.sse_slots = threading.BoundedSemaphore(8)
        self.broadcaster = SnapshotBroadcaster(job_store, poll_interval=poll_interval)
        super().__init__(server_address, LiveRequestHandler)
        self.expected_host = "127.0.0.1:{}".format(self.server_address[1])
        self.broadcaster.start()

    def stop(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.broadcaster.stop()
        self.shutdown()

    def handle_error(self, request, client_address):
        # Default socketserver tracebacks can include handler internals. The
        # lifecycle log intentionally records no requests, tokens, or bodies.
        return


class LiveRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "omnilane"
    sys_version = ""

    def log_message(self, format_string, *args):
        return

    def log_request(self, code="-", size="-"):
        return

    def log_error(self, format_string, *args):
        return

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler's stock HTML errors omit our security headers
        # and echo request details. Keep parser errors generic and turn unknown
        # but syntactically valid HTTP methods into the documented 405.
        self._response_started = False
        if code == 501:
            self._send_json(
                405,
                {"ok": False, "error": "method not allowed"},
                extra_headers={"Allow": "GET"},
            )
        else:
            self._send_json(code, {"ok": False, "error": "request error"})

    def do_GET(self):
        self._response_started = False
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            self.close_connection = True
        except Exception:
            if not self._response_started:
                self._send_json(500, {"ok": False, "error": "internal error"})
            self.close_connection = True

    def do_POST(self):
        self._method_not_allowed()

    def do_PUT(self):
        self._method_not_allowed()

    def do_PATCH(self):
        self._method_not_allowed()

    def do_DELETE(self):
        self._method_not_allowed()

    def do_OPTIONS(self):
        self._method_not_allowed()

    def do_HEAD(self):
        self._method_not_allowed()

    def _method_not_allowed(self):
        self._response_started = False
        if not self._valid_host():
            self._send_json(421, {"ok": False, "error": "misdirected request"})
            return
        self._send_json(
            405,
            {"ok": False, "error": "method not allowed"},
            extra_headers={"Allow": "GET"},
        )

    def _do_get(self):
        if not self._valid_host():
            self._send_json(421, {"ok": False, "error": "misdirected request"})
            return
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc:
            self._send_json(400, {"ok": False, "error": "invalid request target"})
            return

        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if parts.path in static_files:
            filename, content_type = static_files[parts.path]
            try:
                content = (self.server.static_root / filename).read_bytes()
            except (FileNotFoundError, PermissionError, OSError):
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            self._send_bytes(200, content, content_type)
            return

        if not parts.path.startswith("/api/"):
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        is_sse = parts.path == "/api/events"
        if not self._authorized(parts, allow_query_token=is_sse):
            self._send_json(
                401,
                {"ok": False, "error": "unauthorized"},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return

        if parts.path == "/api/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "apiVersion": 1,
                    "pid": os.getpid(),
                    "port": self.server.server_address[1],
                    "serverId": self.server.server_id,
                },
            )
        elif parts.path == "/api/jobs":
            self._send_json(200, self.server.job_store.snapshot())
        elif parts.path.startswith("/api/jobs/"):
            job_id = unquote(parts.path[len("/api/jobs/") :])
            try:
                detail = self.server.job_store.detail(job_id)
            except JobNotFound:
                self._send_json(404, {"ok": False, "error": "job not found"})
                return
            self._send_json(200, {"ok": True, "job": detail})
        elif is_sse:
            self._serve_events()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def _valid_host(self):
        values = self.headers.get_all("Host") or []
        return len(values) == 1 and values[0] == self.server.expected_host

    def _authorized(self, parts, allow_query_token=False):
        candidate = None
        values = self.headers.get_all("Authorization") or []
        if len(values) == 1 and values[0].startswith("Bearer "):
            candidate = values[0][7:]
        if candidate is None and allow_query_token:
            query = parse_qs(parts.query, keep_blank_values=True)
            if set(query) == {"token"} and len(query["token"]) == 1:
                candidate = query["token"][0]
        if candidate is None:
            return False
        return hmac.compare_digest(
            candidate.encode("utf-8"), self.server.token.encode("utf-8")
        )

    def _serve_events(self):
        if not self.server.sse_slots.acquire(blocking=False):
            self._send_json(503, {"ok": False, "error": "too many event streams"})
            return
        try:
            self._response_started = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self._send_security_headers()
            self.send_header("Connection", "close")
            self.end_headers()
            version, snapshot = self.server.broadcaster.current()
            self._write_sse_snapshot(snapshot)
            next_keepalive = time.monotonic() + self.server.keepalive_interval
            while not self.server.stop_event.is_set():
                if self._client_disconnected():
                    break
                wait_timeout = min(
                    0.25,
                    max(0.0, next_keepalive - time.monotonic()),
                )
                next_version, next_snapshot, changed = (
                    self.server.broadcaster.wait_for_change(
                        version, wait_timeout
                    )
                )
                if self.server.stop_event.is_set():
                    break
                if changed:
                    version = next_version
                    self._write_sse_snapshot(next_snapshot)
                    next_keepalive = (
                        time.monotonic() + self.server.keepalive_interval
                    )
                elif time.monotonic() >= next_keepalive:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    next_keepalive = (
                        time.monotonic() + self.server.keepalive_interval
                    )
        finally:
            self.server.sse_slots.release()
            self.close_connection = True

    def _client_disconnected(self):
        try:
            readable, _writable, _exceptional = select.select(
                [self.connection], [], [], 0
            )
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False
        except (OSError, ValueError):
            return True

    def _write_sse_snapshot(self, snapshot):
        payload = json.dumps(
            snapshot, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.wfile.write(b"event: snapshot\n")
        self.wfile.write(b"data: " + payload + b"\n\n")
        self.wfile.flush()

    def _send_json(self, status_code, value, extra_headers=None):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_bytes(
            status_code,
            body,
            "application/json; charset=utf-8",
            extra_headers=extra_headers,
        )

    def _send_bytes(self, status_code, body, content_type, extra_headers=None):
        self._response_started = True
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self._send_security_headers()
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_security_headers(self):
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)


class UIRuntimeError(Exception):
    """A safe lifecycle operation could not be completed."""


class UIRuntime:
    """Own one protected loopback listener for one OMNILANE_HOME."""

    def __init__(self, home=None):
        selected_home = home or os.environ.get("OMNILANE_HOME") or "~/.omnilane"
        self.home = Path(selected_home).expanduser().absolute()
        self.runtime_dir = self.home / "ui"
        self.state_path = self.runtime_dir / "state.json"
        self.lock_path = self.runtime_dir / "lifecycle.lock"
        self.log_path = self.runtime_dir / "server.log"
        self.jobs_path = self.home / "jobs"
        self.static_root = Path(__file__).resolve().parents[1] / "ui"

    def ensure_runtime_dir(self):
        try:
            self.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = os.lstat(self.runtime_dir)
        except OSError as exc:
            raise UIRuntimeError("cannot create protected runtime directory") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UIRuntimeError("runtime path is not a safe directory")
        os.chmod(self.runtime_dir, 0o700)

    @contextmanager
    def lifecycle_lock(self):
        self.ensure_runtime_dir()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = None
        try:
            fd = os.open(self.lock_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise UIRuntimeError("lifecycle lock is not a regular file")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (OSError, UIRuntimeError) as exc:
            if fd is not None:
                os.close(fd)
            raise UIRuntimeError("cannot acquire lifecycle lock") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read_state(self):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.state_path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise UIRuntimeError("state path is unsafe") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                raise UIRuntimeError("state file is invalid")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise UIRuntimeError("state file permissions are too open")
            data = self._read_fd(fd, 32769)
        finally:
            os.close(fd)
        if len(data) > 32768:
            raise UIRuntimeError("state file is too large")
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def write_state(self, state_value):
        self.ensure_runtime_dir()
        data = json.dumps(
            state_value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        temp_path = self.runtime_dir / (
            ".state-{}-{}.tmp".format(os.getpid(), secrets.token_hex(8))
        )
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = None
        try:
            fd = os.open(temp_path, flags, 0o600)
            os.fchmod(fd, 0o600)
            self._write_fd(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.replace(temp_path, self.state_path)
            os.chmod(self.state_path, 0o600)
        except OSError as exc:
            raise UIRuntimeError("cannot write protected state") from exc
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    def clear_state(self, expected_server_id=None):
        try:
            info = os.lstat(self.state_path)
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise UIRuntimeError("state path is unsafe")
        if expected_server_id is not None:
            current = self.read_state()
            if not current or current.get("serverId") != expected_server_id:
                return False
        os.unlink(self.state_path)
        return True

    def open_log(self):
        self.ensure_runtime_dir()
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = None
        try:
            fd = os.open(self.log_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise UIRuntimeError("server log is not a regular file")
            os.fchmod(fd, 0o600)
            return fd
        except (OSError, UIRuntimeError) as exc:
            if fd is not None:
                os.close(fd)
            raise UIRuntimeError("cannot open protected server log") from exc

    @staticmethod
    def complete_state(state_value):
        if not isinstance(state_value, dict):
            return False
        required = ("serverId", "token", "pid", "port")
        if any(key not in state_value for key in required):
            return False
        if not isinstance(state_value["serverId"], str) or len(state_value["serverId"]) < 8:
            return False
        if not isinstance(state_value["token"], str) or len(state_value["token"]) < 16:
            return False
        pid = state_value["pid"]
        port = state_value["port"]
        return (
            not isinstance(pid, bool)
            and isinstance(pid, int)
            and 1 <= pid <= PID_MAX
            and not isinstance(port, bool)
            and isinstance(port, int)
            and 1 <= port <= 65535
            and state_value.get("apiVersion") == 1
        )

    def health(self, state_value, timeout=0.6):
        if not self.complete_state(state_value):
            return None
        connection = http.client.HTTPConnection(
            "127.0.0.1", state_value["port"], timeout=timeout
        )
        try:
            connection.request(
                "GET",
                "/api/health",
                headers={
                    "Authorization": "Bearer " + state_value["token"],
                    "Host": "127.0.0.1:{}".format(state_value["port"]),
                },
            )
            response = connection.getresponse()
            body = response.read(65537)
            if response.status != 200 or len(body) > 65536:
                return None
            payload = json.loads(body.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, http.client.HTTPException):
            return None
        finally:
            connection.close()
        if not isinstance(payload, dict) or not payload.get("ok"):
            return None
        if (
            payload.get("serverId") != state_value["serverId"]
            or payload.get("pid") != state_value["pid"]
            or payload.get("port") != state_value["port"]
        ):
            return None
        return payload

    def recorded_server_process_exists(self, state_value):
        pid = state_value.get("pid") if isinstance(state_value, dict) else None
        server_id = (
            state_value.get("serverId") if isinstance(state_value, dict) else None
        )
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid < 1
            or not isinstance(server_id, str)
        ):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except (OSError, ValueError) as exc:
            return getattr(exc, "errno", None) != errno.ESRCH
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # A live but uninspectable PID is ambiguous. Retaining state is
            # safer than orphaning a possibly healthy authenticated server.
            return True
        command = result.stdout
        return (
            result.returncode == 0
            and str(Path(__file__).resolve()) in command
            and " serve " in command
            and "--server-id " + server_id in command
        )

    @staticmethod
    def url(state_value):
        return "http://127.0.0.1:{}/#token={}".format(
            state_value["port"], state_value["token"]
        )

    @staticmethod
    def _read_fd(fd, count):
        chunks = []
        remaining = count
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _write_fd(fd, data):
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def start_ui(runtime, requested_port):
    with runtime.lifecycle_lock():
        state_value = runtime.read_state()
        if state_value is not None and runtime.health(state_value):
            print("Live UI already running: {}".format(runtime.url(state_value)))
            return 0
        if state_value is not None and runtime.recorded_server_process_exists(
            state_value
        ):
            raise UIRuntimeError(
                "recorded server process is alive but health failed; "
                "state retained and no replacement started"
            )
        runtime.clear_state()

        server_id = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        initial_state = {
            "apiVersion": 1,
            "serverId": server_id,
            "token": token,
            "pid": None,
            "port": None,
            "requestedPort": requested_port,
            "started": utc_now(),
        }
        runtime.write_state(initial_state)
        try:
            log_fd = runtime.open_log()
        except UIRuntimeError:
            runtime.clear_state(expected_server_id=server_id)
            raise
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "serve",
            "--server-id",
            server_id,
            "--port",
            str(requested_port),
        ]
        child = None
        try:
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
                close_fds=True,
                env=os.environ.copy(),
            )
        except OSError:
            runtime.clear_state(expected_server_id=server_id)
            raise
        finally:
            os.close(log_fd)

        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and child.poll() is None:
            candidate = runtime.read_state()
            if (
                candidate
                and candidate.get("serverId") == server_id
                and candidate.get("pid") == child.pid
                and runtime.health(candidate)
            ):
                print("Live UI started: {}".format(runtime.url(candidate)))
                return 0
            time.sleep(0.05)

        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        runtime.clear_state(expected_server_id=server_id)
        raise UIRuntimeError("server failed to start; inspect {}".format(runtime.log_path))


def status_ui(runtime):
    with runtime.lifecycle_lock():
        state_value = runtime.read_state()
        if state_value is not None and runtime.health(state_value):
            print("running pid={} port={}".format(state_value["pid"], state_value["port"]))
            return 0
        print("stopped")
        return 1


def url_ui(runtime):
    with runtime.lifecycle_lock():
        state_value = runtime.read_state()
        if state_value is None or not runtime.health(state_value):
            raise UIRuntimeError("Live UI is not running")
        print(runtime.url(state_value))
        return 0


def stop_ui(runtime):
    with runtime.lifecycle_lock():
        state_value = runtime.read_state()
        if state_value is None:
            if runtime.clear_state():
                print("stale state cleared; no process signalled")
            else:
                print("stopped")
            return 0
        first_health = runtime.health(state_value)
        second_health = runtime.health(state_value) if first_health else None
        if not first_health or not second_health:
            if runtime.recorded_server_process_exists(state_value):
                raise UIRuntimeError(
                    "recorded server process is alive but health failed; "
                    "state retained and no process signalled"
                )
            runtime.clear_state()
            print("stale state cleared; no process signalled")
            return 0
        try:
            os.kill(state_value["pid"], signal.SIGTERM)
        except ProcessLookupError:
            runtime.clear_state(expected_server_id=state_value["serverId"])
            print("stopped")
            return 0
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if runtime.health(state_value, timeout=0.2) is None:
                runtime.clear_state(expected_server_id=state_value["serverId"])
                print("stopped")
                return 0
            time.sleep(0.05)
        raise UIRuntimeError("server did not stop; state retained")


def serve_ui(runtime, server_id, requested_port):
    state_value = runtime.read_state()
    if not state_value or state_value.get("serverId") != server_id:
        raise UIRuntimeError("startup state does not match this server")
    token = state_value.get("token")
    if not isinstance(token, str) or len(token) < 16:
        raise UIRuntimeError("startup token is invalid")
    try:
        server = LiveHTTPServer(
            ("127.0.0.1", requested_port),
            JobStore(runtime.jobs_path),
            token,
            server_id,
            runtime.static_root,
        )
    except OSError as exc:
        if requested_port == 0 or exc.errno != errno.EADDRINUSE:
            raise
        server = LiveHTTPServer(
            ("127.0.0.1", 0),
            JobStore(runtime.jobs_path),
            token,
            server_id,
            runtime.static_root,
        )

    current = runtime.read_state()
    if not current or current.get("serverId") != server_id:
        server.broadcaster.stop()
        server.server_close()
        raise UIRuntimeError("startup state changed before bind completed")
    current.update(
        {
            "apiVersion": 1,
            "pid": os.getpid(),
            "port": server.server_address[1],
        }
    )
    runtime.write_state(current)

    def request_shutdown(_signum, _frame):
        threading.Thread(target=server.stop, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGHUP, request_shutdown)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.stop_event.set()
        server.broadcaster.stop()
        server.server_close()
        with runtime.lifecycle_lock():
            runtime.clear_state(expected_server_id=server_id)
    return 0


def parse_port(value):
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def main(argv=None):
    if sys.version_info < (3, 9):
        print("omnilane ui: Python 3.9 or newer is required", file=sys.stderr)
        return 1
    arguments = list(sys.argv[1:] if argv is None else argv)
    runtime = UIRuntime()
    try:
        if arguments[:1] == ["serve"]:
            private = argparse.ArgumentParser(prog="omnilane ui serve")
            private.add_argument("serve")
            private.add_argument("--server-id", required=True)
            private.add_argument("--port", type=parse_port, required=True)
            parsed = private.parse_args(arguments)
            return serve_ui(runtime, parsed.server_id, parsed.port)

        parser = argparse.ArgumentParser(prog="omnilane ui")
        commands = parser.add_subparsers(dest="command", required=True)
        start_parser = commands.add_parser("start")
        start_parser.add_argument("--port", type=parse_port, default=8765)
        commands.add_parser("status")
        commands.add_parser("url")
        commands.add_parser("stop")
        parsed = parser.parse_args(arguments)
        if parsed.command == "start":
            return start_ui(runtime, parsed.port)
        if parsed.command == "status":
            return status_ui(runtime)
        if parsed.command == "url":
            return url_ui(runtime)
        return stop_ui(runtime)
    except UIRuntimeError as exc:
        print("omnilane ui: {}".format(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("omnilane ui: lifecycle operation failed: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
