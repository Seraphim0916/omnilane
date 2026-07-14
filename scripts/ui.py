#!/usr/bin/env python3
"""Local, read-only Omnilane job board.

Core dispatch remains Bash-only.  This optional module intentionally uses only
the Python standard library so `omnilane ui` has no package-manager runtime.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import heapq
import json
import os
from pathlib import Path
import re
import stat


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

