#!/usr/bin/env python3
"""Long-lived Codex app-server bridge for omnilane's live mailbox."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from typing import Any


class ProtocolError(RuntimeError):
    pass


def codex_app_server_argv(codex_bin: str, sandbox: str) -> list[str]:
    """Pin every mode-relevant control before app-server starts."""
    config = ['approval_policy="never"', f'sandbox_mode="{sandbox}"']
    if sandbox == "workspace-write":
        config.extend(
            [
                'web_search="disabled"',
                "sandbox_workspace_write.network_access=false",
                "sandbox_workspace_write.exclude_slash_tmp=true",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "sandbox_workspace_write.writable_roots=[]",
            ]
        )
    elif sandbox in {"read-only", "danger-full-access"}:
        config.append('web_search="live"')
    else:
        raise ValueError(f"unsupported Codex sandbox: {sandbox}")

    argv = [codex_bin]
    for value in config:
        argv.extend(["-c", value])
    argv.append("app-server")
    return argv


class CodexLiveClient:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.events: Any = None
        self.inbox: Any = None
        self.messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.next_request_id = 1
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.last_turn_succeeded = False
        self.stop_requested = False
        self.server_stdout_eof = threading.Event()

    def request_stop(self, _signum: int, _frame: Any) -> None:
        self.stop_requested = True

    def start_server(self) -> None:
        self.process = subprocess.Popen(
            codex_app_server_argv(self.args.codex_bin, getattr(self.args, "sandbox", "workspace-write")),
            cwd=self.args.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            # The outer watchdog owns this process group. Do not split the
            # app-server into a session that survives if this client is
            # SIGKILLed before its finally block can run.
            start_new_session=False,
        )
        threading.Thread(target=self.read_server, name="codex-app-server", daemon=True).start()

    def read_server(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                self.messages.put(("message", raw))
        finally:
            self.server_stdout_eof.set()
            self.messages.put(("eof", None))

    def send(self, method: str, params: dict[str, Any]) -> int:
        assert self.process is not None and self.process.stdin is not None
        request_id = self.next_request_id
        self.next_request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            self.process.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProtocolError(f"Codex app-server stdin closed during {method}") from exc
        return request_id

    def record_event(self, raw: str) -> dict[str, Any] | None:
        assert self.events is not None
        self.events.write(raw if raw.endswith("\n") else raw + "\n")
        self.events.flush()
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(message, dict):
            return None

        if message.get("method") == "item/completed":
            params = message.get("params")
            item = params.get("item") if isinstance(params, dict) else None
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text")
                if isinstance(text, str):
                    self.append_agent_text(text)

        if message.get("method") == "turn/completed":
            params = message.get("params")
            turn = params.get("turn") if isinstance(params, dict) else None
            if isinstance(turn, dict) and turn.get("id") == self.turn_id:
                self.last_turn_succeeded = turn.get("status") == "completed" and turn.get("error") is None
                self.turn_id = None
        return message

    def append_agent_text(self, text: str) -> None:
        with open(self.args.output, "a", encoding="utf-8") as output:
            output.write(text)
            if not text.endswith("\n"):
                output.write("\n")
            output.flush()
            os.fsync(output.fileno())

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            kind, raw = self.messages.get(timeout=timeout)
        except queue.Empty:
            return None
        if kind == "eof":
            raise ProtocolError("Codex app-server stdout closed")
        assert raw is not None
        return self.record_event(raw)

    def wait_response(self, request_id: int, method: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.args.rpc_timeout
        while not self.stop_requested and time.monotonic() < deadline:
            message = self.receive(min(0.2, max(0.0, deadline - time.monotonic())))
            if message is None or message.get("id") != request_id:
                continue
            result = message.get("result")
            if not isinstance(result, dict):
                error = message.get("error")
                raise ProtocolError(f"Codex app-server rejected {method}: {error!r}")
            return result
        raise ProtocolError(f"Codex app-server timed out during {method}")

    def initialize(self) -> None:
        request_id = self.send(
            "initialize",
            {"clientInfo": {"name": "omnilane", "version": self.args.version}},
        )
        self.wait_response(request_id, "initialize")

        params: dict[str, Any] = {
            "cwd": self.args.cwd,
            "model": self.args.model,
            "sandbox": self.args.sandbox,
        }
        request_id = self.send("thread/start", params)
        result = self.wait_response(request_id, "thread/start")
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ProtocolError("Codex thread/start response omitted thread.id")
        self.thread_id = thread_id
        self.record_thread_id(thread_id)

    def record_thread_id(self, thread_id: str) -> None:
        session_path = pathlib.Path(self.args.session_id_file)
        session_path.write_text(thread_id + "\n", encoding="utf-8")
        session_path.chmod(0o600)
        progress = {
            "type": "thread.started",
            "thread_id": thread_id,
        }
        pathlib.Path(self.args.progress).write_text(
            json.dumps(progress, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def decode_mailbox(raw: str) -> str:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError("Codex live mailbox received invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("type") != "codex-user":
            raise ProtocolError("Codex live mailbox received an invalid payload type")
        text = payload.get("text")
        if not isinstance(text, str):
            raise ProtocolError("Codex live mailbox payload omitted text")
        return text

    def start_turn(self, text: str) -> None:
        assert self.thread_id is not None
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if self.args.effort and self.args.effort != "-":
            params["effort"] = self.args.effort
        request_id = self.send("turn/start", params)
        result = self.wait_response(request_id, "turn/start")
        turn = result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise ProtocolError("Codex turn/start response omitted turn.id")
        self.turn_id = turn_id
        self.last_turn_succeeded = False

    def steer_turn(self, text: str) -> None:
        assert self.thread_id is not None and self.turn_id is not None
        expected_turn_id = self.turn_id
        params = {
            "threadId": self.thread_id,
            "expectedTurnId": expected_turn_id,
            "input": [{"type": "text", "text": text}],
        }
        request_id = self.send("turn/steer", params)
        result = self.wait_response(request_id, "turn/steer")
        returned_turn_id = result.get("turnId")
        if returned_turn_id != expected_turn_id:
            raise ProtocolError("Codex turn/steer response changed the active turn id")

    def run_mailbox(self) -> None:
        self.inbox = open(self.args.inbox, "r", encoding="utf-8")
        eof_deadline: float | None = None
        while not self.stop_requested:
            while True:
                try:
                    kind, raw = self.messages.get_nowait()
                except queue.Empty:
                    break
                if kind == "eof":
                    raise ProtocolError("Codex app-server stdout closed")
                assert raw is not None
                self.record_event(raw)

            if eof_deadline is not None:
                if self.turn_id is None:
                    return
                if time.monotonic() >= eof_deadline:
                    raise ProtocolError("Codex turn did not finish before live close deadline")
                self.receive(0.1)
                continue

            ready, _, _ = select.select([self.inbox], [], [], 0.1)
            if not ready:
                if self.process is not None and self.process.poll() is not None:
                    raise ProtocolError(f"Codex app-server exited {self.process.returncode}")
                continue
            raw = self.inbox.readline()
            if raw == "":
                eof_deadline = time.monotonic() + self.args.close_grace
                continue
            text = self.decode_mailbox(raw)
            if self.turn_id is None:
                self.start_turn(text)
            else:
                self.steer_turn(text)

    @staticmethod
    def process_identity(pid: int) -> str | None:
        """Return a stable start-time identity so a recycled PID is never signalled."""
        try:
            result = subprocess.run(
                ["ps", "-o", "state=,lstart=", "-p", str(pid)],
                text=True,
                capture_output=True,
                check=False,
                timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        value = result.stdout.strip()
        if result.returncode != 0 or not value:
            return None
        state, _, identity = value.partition(" ")
        if state.startswith("Z") or not identity.strip():
            return None
        return " ".join(identity.split())

    @classmethod
    def snapshot_descendants(cls, root_pid: int) -> dict[int, str]:
        """Snapshot descendants before app-server EOF can reparent its helpers."""
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,lstart="],
                text=True,
                capture_output=True,
                check=False,
                timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode != 0:
            return {}
        children: dict[int, list[int]] = {}
        identities: dict[int, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 7:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            children.setdefault(ppid, []).append(pid)
            identities[pid] = " ".join(parts[2:7])
        found: dict[int, str] = {}
        pending = [root_pid]
        while pending:
            parent = pending.pop()
            for pid in children.get(parent, []):
                if pid in found:
                    continue
                identity = identities.get(pid)
                if identity:
                    found[pid] = identity
                pending.append(pid)
        return found

    @classmethod
    def terminate_tracked_descendants(cls, tracked: dict[int, str]) -> None:
        """Reap app-server helpers that remain after their parent exits."""
        alive: list[int] = []
        for pid, identity in tracked.items():
            if cls.process_identity(pid) != identity:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                alive.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if alive:
            time.sleep(0.05)
        survivors: list[int] = []
        for pid in alive:
            identity = tracked[pid]
            if cls.process_identity(pid) != identity:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                survivors.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if survivors:
            # Give init a bounded chance to reap detached grandchildren.
            time.sleep(0.05)

    def wait_for_server_exit(self, timeout: float) -> bool:
        assert self.process is not None
        try:
            self.process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def close(self) -> None:
        if self.inbox is not None:
            self.inbox.close()
        if self.process is None:
            return
        tracked_descendants = self.snapshot_descendants(self.process.pid)
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None and not self.wait_for_server_exit(
            self.args.app_server_eof_grace
        ):
            try:
                self.process.terminate()
            except OSError:
                pass
            if self.process.poll() is None and not self.wait_for_server_exit(
                self.args.app_server_term_grace
            ):
                try:
                    self.process.kill()
                except OSError:
                    pass
                if self.process.poll() is None:
                    self.wait_for_server_exit(self.args.app_server_kill_grace)
        # The reader thread owns stdout. A sidecar may inherit that pipe even
        # after app-server exits, so closing it here can block on the reader's
        # file-object lock and defeat the bounded shutdown contract.
        if self.process.stdout is not None and self.server_stdout_eof.is_set():
            self.process.stdout.close()
        self.terminate_tracked_descendants(tracked_descendants)

    def run(self) -> int:
        pathlib.Path(self.args.output).write_text("", encoding="utf-8")
        pathlib.Path(self.args.progress).write_text("", encoding="utf-8")
        self.events = open(self.args.events, "a", encoding="utf-8", buffering=1)
        try:
            self.start_server()
            self.initialize()
            self.run_mailbox()
            return 0 if self.last_turn_succeeded else 1
        except ProtocolError as exc:
            print(f"omnilane: {exc}", file=sys.stderr)
            return 1
        finally:
            self.close()
            self.events.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-bin", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--progress", required=True)
    parser.add_argument("--session-id-file", required=True)
    parser.add_argument("--version", default="0.40.0")
    parser.add_argument("--rpc-timeout", type=float, default=10.0)
    # Coupled to job-worker.sh's 100 * 0.1s outer grace. The default turn,
    # EOF, TERM, and KILL waits total seven seconds below that boundary.
    parser.add_argument("--close-grace", type=float, default=3.0)
    parser.add_argument("--app-server-eof-grace", type=float, default=2.0)
    parser.add_argument("--app-server-term-grace", type=float, default=1.0)
    parser.add_argument("--app-server-kill-grace", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CodexLiveClient(args)
    signal.signal(signal.SIGTERM, client.request_stop)
    signal.signal(signal.SIGHUP, client.request_stop)
    signal.signal(signal.SIGINT, client.request_stop)
    return client.run()


if __name__ == "__main__":
    raise SystemExit(main())
