#!/usr/bin/env python3
"""Long-lived Grok ACP bridge for omnilane's live mailbox."""

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


def grok_acp_argv(grok_bin: str, model: str, mode: str) -> list[str]:
    """Build the only supported ACP mode: explicit unrestricted sysops."""
    if mode != "sysops":
        raise ValueError(
            f"Grok {mode} live is unavailable: ACP has no enforceable restricted-mode policy"
        )
    return [
        grok_bin,
        "--no-memory",
        "--no-subagents",
        "--no-plan",
        "--verbatim",
        "--sandbox",
        "off",
        "agent",
        "--always-approve",
        "--model",
        model,
        "stdio",
    ]


class GrokLiveClient:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.events: Any = None
        self.inbox: Any = None
        self.messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.next_request_id = 1
        self.active_prompt_request_id: int | None = None
        self.session_id: str | None = None
        self.turn_running = False
        self.last_turn_succeeded = False
        self.stop_requested = False
        self.session_close_sent = False

    def request_stop(self, _signum: int, _frame: Any) -> None:
        self.stop_requested = True

    def start_server(self) -> None:
        self.process = subprocess.Popen(
            grok_acp_argv(self.args.grok_bin, self.args.model, self.args.mode),
            cwd=self.args.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            # The outer watchdog owns this process group. Do not split the
            # ACP agent into a session that survives if this client is
            # SIGKILLed before its finally block can run.
            start_new_session=False,
        )
        threading.Thread(
            target=self.read_server, name="grok-acp-agent", daemon=True
        ).start()

    def read_server(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                self.messages.put(("message", raw))
        finally:
            self.messages.put(("eof", None))

    def write_message(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        try:
            self.process.stdin.write(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProtocolError(f"Grok ACP stdin closed: {exc}") from exc

    def send_request(self, method: str, params: dict[str, Any]) -> int:
        request_id = self.next_request_id
        self.next_request_id += 1
        self.write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        return request_id

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        # session/cancel must be an ACP notification. Adding an id changes it
        # into a request, which Grok rejects with -32601 Method not found.
        self.write_message({"jsonrpc": "2.0", "method": method, "params": params})

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

        if (
            self.active_prompt_request_id is not None
            and message.get("id") == self.active_prompt_request_id
        ):
            if "error" in message:
                error = message.get("error")
                if isinstance(error, dict):
                    detail = (
                        f"code={error.get('code')!r} "
                        f"message={error.get('message')!r}"
                    )
                else:
                    detail = repr(error)
                self.active_prompt_request_id = None
                self.turn_running = False
                self.last_turn_succeeded = False
                raise ProtocolError(
                    f"Grok ACP rejected session/prompt: {detail}"
                )
            self.active_prompt_request_id = None

        if message.get("method") == "session/update":
            params = message.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            if isinstance(update, dict):
                update_type = update.get("sessionUpdate")
                if update_type == "agent_message_chunk":
                    content = update.get("content")
                    text = content.get("text") if isinstance(content, dict) else None
                    if isinstance(text, str):
                        self.append_agent_text(text)
                # agent_thought_chunk is private reasoning and is deliberately
                # journaled only; it must never reach OUTPUT_FILE.

        if message.get("method") == "_x.ai/session/prompt_complete":
            params = message.get("params")
            stop_reason = params.get("stopReason") if isinstance(params, dict) else None
            self.active_prompt_request_id = None
            self.turn_running = False
            self.last_turn_succeeded = stop_reason == "end_turn"

        return message

    def append_agent_text(self, text: str) -> None:
        with open(self.args.output, "a", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            kind, raw = self.messages.get(timeout=timeout)
        except queue.Empty:
            return None
        if kind == "eof":
            raise ProtocolError("Grok ACP stdout closed")
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
                raise ProtocolError(f"Grok ACP rejected {method}: {error!r}")
            return result
        raise ProtocolError(f"Grok ACP timed out waiting for {method}")

    def initialize(self) -> None:
        request_id = self.send_request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False}
                },
            },
        )
        self.wait_response(request_id, "initialize")

        request_id = self.send_request(
            "session/new", {"cwd": self.args.cwd, "mcpServers": []}
        )
        result = self.wait_response(request_id, "session/new")
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ProtocolError("Grok session/new response omitted sessionId")
        self.session_id = session_id
        self.record_session_id(session_id)

        # The verified ACP surface has no permission-mode or model-selection
        # field equivalent to single-shot --permission-mode/--model. Do not
        # invent one. run-grok.sh keeps its single-shot mapping unchanged.
        print(
            "omnilane: Grok ACP live exposes no permission-mode field; "
            f"MODE={self.args.mode} uses the ACP default. jobs.sh send cancels "
            "an active Grok turn before starting the new prompt; conversation "
            "context survives, but in-progress work is lost.",
            file=sys.stderr,
        )

    def record_session_id(self, session_id: str) -> None:
        session_path = pathlib.Path(self.args.session_id_file)
        session_path.write_text(session_id + "\n", encoding="utf-8")
        session_path.chmod(0o600)
        progress = {
            "event": "session.started",
            "session_id": session_id,
            "vendor": "grok",
        }
        pathlib.Path(self.args.progress).write_text(
            json.dumps(progress, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def decode_mailbox(self, raw: str) -> str:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError("Grok mailbox line is not JSON") from exc
        if not isinstance(payload, dict) or payload.get("type") != "grok-user":
            raise ProtocolError("Grok mailbox line has the wrong type")
        text = payload.get("text")
        if not isinstance(text, str):
            raise ProtocolError("Grok mailbox line omitted text")
        return text

    def start_prompt(self, text: str) -> None:
        assert self.session_id is not None
        self.active_prompt_request_id = self.send_request(
            "session/prompt",
            {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        )
        self.turn_running = True
        self.last_turn_succeeded = False

    def cancel_active_turn(self) -> None:
        assert self.session_id is not None
        if not self.turn_running:
            return
        self.send_notification("session/cancel", {"sessionId": self.session_id})
        # A cancelled request may answer late after the replacement prompt has
        # started. Only an error for the current active request is fatal.
        self.active_prompt_request_id = None
        deadline = time.monotonic() + self.args.rpc_timeout
        while self.turn_running and not self.stop_requested:
            if time.monotonic() >= deadline:
                raise ProtocolError("Grok ACP timed out waiting for cancelled turn")
            self.receive(min(0.1, max(0.0, deadline - time.monotonic())))

    def close_session(self, deadline: float) -> None:
        if self.session_id is None or self.session_close_sent:
            return
        if self.turn_running:
            self.send_notification("session/cancel", {"sessionId": self.session_id})
            while self.turn_running and time.monotonic() < deadline:
                self.receive(min(0.1, max(0.0, deadline - time.monotonic())))
        request_id = self.send_request("session/close", {"sessionId": self.session_id})
        self.session_close_sent = True
        while time.monotonic() < deadline:
            try:
                message = self.receive(
                    min(0.1, max(0.0, deadline - time.monotonic()))
                )
            except ProtocolError:
                if self.process is not None and self.process.poll() is not None:
                    return
                raise
            if message is not None and message.get("id") == request_id:
                if "error" in message:
                    raise ProtocolError(
                        f"Grok ACP rejected session/close: {message['error']!r}"
                    )
                return

    def drain_server_messages(self) -> None:
        while True:
            try:
                kind, raw = self.messages.get_nowait()
            except queue.Empty:
                return
            if kind == "eof":
                raise ProtocolError("Grok ACP stdout closed")
            assert raw is not None
            self.record_event(raw)

    def run_mailbox(self) -> None:
        self.inbox = open(self.args.inbox, "r", encoding="utf-8")
        while not self.stop_requested:
            self.drain_server_messages()
            if self.process is not None and self.process.poll() is not None:
                raise ProtocolError(f"Grok ACP exited with {self.process.returncode}")

            readable, _, _ = select.select([self.inbox], [], [], 0.1)
            if not readable:
                continue
            raw = self.inbox.readline()
            if raw == "":
                # Coupled to job-worker.sh's 50 * 0.1s outer grace. The entire
                # ACP cancel/close grace plus close()'s one-second TERM wait
                # must expire before the worker escalates at five seconds.
                self.close_session(time.monotonic() + self.args.close_grace)
                return

            text = self.decode_mailbox(raw)
            if self.turn_running:
                self.cancel_active_turn()
            self.start_prompt(text)

    def close(self) -> None:
        if self.inbox is not None:
            self.inbox.close()
        if self.process is None:
            return

        if self.process.poll() is None and self.session_id is not None:
            try:
                self.close_session(time.monotonic() + min(0.2, self.args.close_grace))
            except (ProtocolError, OSError):
                pass
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                except OSError:
                    pass
                self.process.wait()
        if self.process.stdout is not None:
            self.process.stdout.close()

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
    parser.add_argument("--grok-bin", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("advise", "work", "sysops"), required=True)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--progress", required=True)
    parser.add_argument("--session-id-file", required=True)
    parser.add_argument("--rpc-timeout", type=float, default=10.0)
    # Coupled to job-worker.sh's 50 * 0.1s outer grace: leave time for the
    # one-second ACP process TERM wait before the worker escalates.
    parser.add_argument("--close-grace", type=float, default=3.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode != "sysops":
        print(
            f"omnilane: Grok {args.mode} live is unavailable because ACP exposes no "
            "enforceable restricted-mode policy",
            file=sys.stderr,
        )
        return 2
    client = GrokLiveClient(args)
    signal.signal(signal.SIGTERM, client.request_stop)
    signal.signal(signal.SIGHUP, client.request_stop)
    signal.signal(signal.SIGINT, client.request_stop)
    return client.run()


if __name__ == "__main__":
    raise SystemExit(main())
