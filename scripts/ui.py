#!/usr/bin/env python3
"""Local, read-only Omnilane job board.

Core dispatch remains Bash-only.  This optional module intentionally uses only
the Python standard library so `omnilane ui` has no package-manager runtime.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import heapq
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import stat
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
        keepalive_interval=15.0,
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
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            version, snapshot = self.server.broadcaster.current()
            self._write_sse_snapshot(snapshot)
            while not self.server.stop_event.is_set():
                next_version, next_snapshot, changed = (
                    self.server.broadcaster.wait_for_change(
                        version, self.server.keepalive_interval
                    )
                )
                if self.server.stop_event.is_set():
                    break
                if changed:
                    version = next_version
                    self._write_sse_snapshot(next_snapshot)
                else:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        finally:
            self.server.sse_slots.release()
            self.close_connection = True

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
        if body:
            self.wfile.write(body)

    def _send_security_headers(self):
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
