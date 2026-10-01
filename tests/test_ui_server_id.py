#!/usr/bin/env python3
"""Regression coverage for generated Live UI server IDs and their argv form."""

import base64
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
UI_SCRIPT = ROOT / "scripts" / "ui.py"
SPEC = importlib.util.spec_from_file_location("omnilane_ui_server_id", UI_SCRIPT)
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)

ALPHABETIC_ID = base64.urlsafe_b64encode(b"\x00" * 18).decode("ascii")
LEADING_DASH_ID = base64.urlsafe_b64encode(b"\xf8" + b"\x00" * 17).decode("ascii")


class GeneratedServerIdLifecycleTests(unittest.TestCase):
    def check_startup(self, server_id, busy_port=False):
        decoded = base64.urlsafe_b64decode(server_id)
        self.assertEqual(18, len(decoded))
        self.assertEqual(server_id, base64.urlsafe_b64encode(decoded).decode("ascii"))
        original_nonce = ui.secrets.token_urlsafe
        original_popen = subprocess.Popen
        nonce_sizes = []
        children = []

        def generated_nonce(nbytes=None):
            nonce_sizes.append(nbytes)
            # Only choose the server ID; authentication keeps real randomness.
            return server_id if nbytes == 18 else original_nonce(nbytes)

        def tracked_popen(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory(prefix="omnilane-ui-server-id-") as home:
            runtime = ui.UIRuntime(home)
            busy = None
            requested_port = 0
            if busy_port:
                busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                busy.bind(("127.0.0.1", 0))
                busy.listen(1)
                requested_port = busy.getsockname()[1]
            try:
                with mock.patch.dict(
                    os.environ,
                    {"OMNILANE_HOME": home, "PYTHONDONTWRITEBYTECODE": "1"},
                ), mock.patch.object(
                    ui.secrets, "token_urlsafe", side_effect=generated_nonce
                ), mock.patch.object(
                    ui.subprocess, "Popen", side_effect=tracked_popen
                ), contextlib.redirect_stdout(io.StringIO()):
                    # Launch the real private child with the product's argv.
                    result = ui.start_ui(runtime, requested_port)

                self.assertEqual(0, result)
                self.assertEqual([18, 32], nonce_sizes)
                self.assertEqual(1, len(children))
                state = runtime.read_state()
                self.assertIsNotNone(state)
                self.assertEqual(server_id, state["serverId"])
                self.assertEqual(children[0].pid, state["pid"])
                self.assertEqual(requested_port, state["requestedPort"])
                self.assertGreater(state["port"], 0)
                self.assertTrue(runtime.health(state))
                self.assertTrue(runtime.recorded_server_process_exists(state))
                if busy is not None:
                    # Keep the original listener bound through startup and health.
                    self.assertEqual(requested_port, busy.getsockname()[1])
                    self.assertNotEqual(requested_port, state["port"])
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, ui.stop_ui(runtime))
                self.assertFalse(runtime.state_path.exists())
                self.assertEqual(0, children[0].wait(timeout=3))
            finally:
                # Only reap processes created by this fixture, including failures.
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=3)
                if busy is not None:
                    busy.close()

    def test_alphabetic_id_with_automatic_port(self):
        self.check_startup(ALPHABETIC_ID)

    def test_alphabetic_id_with_busy_port(self):
        self.check_startup(ALPHABETIC_ID, busy_port=True)

    def test_leading_dash_id_with_automatic_port(self):
        self.check_startup(LEADING_DASH_ID)

    def test_leading_dash_id_with_busy_port(self):
        self.check_startup(LEADING_DASH_ID, busy_port=True)


class RecordedServerIdArgumentTests(unittest.TestCase):
    def check_command(self, server_id, arguments, expected, verb="serve"):
        state = {"pid": 12345, "serverId": server_id}
        command = "{} {} {} {}\n".format(
            sys.executable, UI_SCRIPT, verb, arguments
        )
        result = subprocess.CompletedProcess([], 0, stdout=command)
        with mock.patch.object(ui.os, "kill", return_value=None) as probe, \
                mock.patch.object(ui.subprocess, "run", return_value=result) as ps:
            actual = ui.UIRuntime().recorded_server_process_exists(state)
        self.assertEqual(expected, actual, command)
        probe.assert_called_once_with(12345, 0)
        ps.assert_called_once_with(
            ["ps", "-p", "12345", "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
            check=False,
        )

    def test_recognizes_old_split_and_new_equals_id_arguments(self):
        for server_id in (ALPHABETIC_ID, LEADING_DASH_ID):
            for separator in (" ", "="):
                with self.subTest(server_id=server_id, separator=separator):
                    self.check_command(
                        server_id, "--server-id" + separator + server_id + " --port 0", True
                    )

    def test_rejects_different_same_length_id(self):
        for separator in (" ", "="):
            with self.subTest(separator=separator):
                self.check_command(
                    ALPHABETIC_ID,
                    "--server-id" + separator + LEADING_DASH_ID + " --port 0",
                    False,
                )

    def test_rejects_id_with_extra_suffix(self):
        for separator in (" ", "="):
            with self.subTest(separator=separator):
                self.check_command(
                    ALPHABETIC_ID,
                    "--server-id" + separator + ALPHABETIC_ID + "extra --port 0",
                    False,
                )

    def test_rejects_missing_id_argument(self):
        for arguments in ("--port 0", "--server-id --port 0", "--server-id= --port 0"):
            with self.subTest(arguments=arguments):
                self.check_command(ALPHABETIC_ID, arguments, False)

    def test_rejects_nonserver_command(self):
        for separator in (" ", "="):
            with self.subTest(separator=separator):
                self.check_command(
                    ALPHABETIC_ID,
                    "--server-id" + separator + ALPHABETIC_ID + " --port 0",
                    False,
                    verb="status",
                )


if __name__ == "__main__":
    unittest.main()
