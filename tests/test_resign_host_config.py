#!/usr/bin/env python3
"""Fail closed on host configuration, using the suite's isolated fake host."""
from __future__ import annotations

import offline_env  # Activate suite home isolation before importing resign.
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_resign

resign = test_resign.resign
build_overlay = test_resign.build_overlay
ROOT = test_resign.ROOT
HOST_CONFIG = 40
UNCONFIGURED = (
    "omnilane: no transport overlay is configured (OMNILANE_AA_TRANSPORT_OVERLAY); "
    "there is nothing to re-sign. See the README, 'Let your AI assistant drive omnilane', Step 2.\n"
)


class HostConfigTests(unittest.TestCase):
    def setUp(self):
        self.host = test_resign.ResignTests()
        self.addCleanup(self.host.doCleanups)
        self.host.setUp()
        self.local = self.host.home / "local.sh"

    def invoke(self, flags=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = resign.main(list(flags))
        return code, stdout.getvalue(), stderr.getvalue()

    def host_files(self):
        return {str(p.relative_to(self.host.home)): p.read_bytes()
                for p in self.host.home.rglob("*") if p.is_file()}

    def assert_host_error(self, path, reason, *, before_overlay=False):
        before = self.host_files()
        original_read = Path.read_text

        def read(path, *args, **kwargs):
            if before_overlay and path == self.host.live:
                self.fail("host configuration failed but resign read the overlay")
            return original_read(path, *args, **kwargs)

        modes = ((), ("--check",), ("--record-signers",), ("--trust-adhoc", "grok"))
        for mode in modes:
            for json_mode in ((), ("--json",)):
                with self.subTest(mode=mode, json_mode=json_mode), \
                        patch.object(Path, "read_text", read):
                    code, stdout, stderr = self.invoke(mode + json_mode)
                    self.assertEqual(code, HOST_CONFIG, stderr)
                    self.assertEqual(len(stderr.splitlines()), 1, stderr)
                    self.assertIn(str(path), stderr)
                    self.assertIn(reason, stderr)
                    self.assertNotIn("Traceback", stderr)
                    if stdout:
                        json.loads(stdout)
                    self.assertEqual(self.host_files(), before)

    def test_local_syntax_error_stops_before_overlay_for_all_modes(self):
        self.local.write_text("if then\n")
        self.assert_host_error(self.local, "syntax error", before_overlay=True)

    def test_local_nonzero_exit_stops_before_overlay_for_all_modes(self):
        self.local.write_text("printf 'fixture host failure\\n' >&2\nexit 7\n")
        self.assert_host_error(self.local, "fixture host failure", before_overlay=True)

    def test_local_silent_nonzero_exit_names_status(self):
        self.local.write_text("exit 7\n")
        self.assert_host_error(self.local, "7", before_overlay=True)

    def test_local_nonzero_return_uses_dispatch_shell_settings(self):
        self.local.write_text("return 7\n")
        self.assert_host_error(self.local, "7", before_overlay=True)

    def test_configured_missing_overlay_is_host_error_for_all_modes(self):
        missing = self.host.home / "missing-overlay.json"
        self.local.write_text(f'OMNILANE_AA_TRANSPORT_OVERLAY="{missing}"\n')
        for overlay in (str(missing), ""):
            with self.subTest(overlay=overlay), \
                    patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": overlay}):
                self.assert_host_error(missing, "No such file")

    def test_configured_invalid_json_is_host_error_for_all_modes(self):
        self.host.live.write_text("not json\n")
        self.local.write_text(f'OMNILANE_AA_TRANSPORT_OVERLAY="{self.host.live}"\n')
        for overlay in (str(self.host.live), ""):
            with self.subTest(overlay=overlay), \
                    patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": overlay}):
                self.assert_host_error(self.host.live, "Expecting value")

    def test_common_source_failure_stops_before_overlay_for_all_modes(self):
        library = self.host.base / "bad-library"
        library.mkdir()
        common = library / "common.sh"
        common.write_text("if then\n")
        with patch.object(build_overlay, "__file__", str(library / "build_overlay.py")):
            self.assert_host_error(common, "syntax error", before_overlay=True)

    def test_resolver_source_failure_has_no_traceback_or_overlay_read(self):
        error = subprocess.CalledProcessError(7, ["bash"], stderr="common.sh: fixture source failure\n")
        with patch.object(build_overlay, "_shell_vendor_bin", side_effect=error):
            self.assert_host_error(Path(build_overlay.__file__).with_name("common.sh"),
                                   "fixture source failure", before_overlay=True)

    def test_unconfigured_is_exactly_the_old_code_and_message(self):
        for local in (None, ":\n"):
            if local is not None:
                self.local.write_text(local)
            for flags in ((), ("--check",), ("--json",)):
                with self.subTest(local=local, flags=flags), \
                        patch.dict(os.environ, {"OMNILANE_AA_TRANSPORT_OVERLAY": ""}):
                    self.assertEqual(self.invoke(flags), (2, "", UNCONFIGURED))

    def test_usage_error_still_exits_two(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            resign.main(["--not-a-resign-option"])
        self.assertEqual(error.exception.code, 2)

    def test_healthy_host_output_and_code_stay_identical(self):
        modes = ((), ("--check",), ("--record-signers",), ("--trust-adhoc", "grok"))
        for mode in modes:
            with self.subTest(mode=mode):
                # Bring mutating operator modes to their idempotent state first.
                self.invoke(mode)
                before = self.invoke(mode)
                self.local.write_text("# healthy, silent host configuration\n")
                self.assertEqual(self.invoke(mode), before)
                self.local.unlink()

    def test_missing_cli_keeps_existing_held_reason(self):
        original = resign.shutil.which
        with patch.object(resign.shutil, "which", lambda name: None if name == "grok" else original(name)):
            code, stdout, stderr = self.invoke(("--vendor", "grok"))
        self.assertEqual(code, resign.EXIT_OPERATOR)
        self.assertEqual(stdout, "")
        self.assertIn("grok is not on PATH", stderr)
        self.assertIn("the CLI is not installed", stderr)

    def test_bin_local_failure_is_reported_once_on_stderr(self):
        fake_bin = self.host.base / "bin"
        fake_bin.mkdir()
        (fake_bin / "python3").symlink_to(sys.executable)
        self.local.write_text("printf 'fixture host failure\\n' >&2\nexit 7\n")
        before = self.host_files()
        for flags in ((), ("--check",), ("--json",), ("--check", "--json")):
            for overlay in ("", str(self.host.live)):
                with self.subTest(flags=flags, overlay=overlay):
                    environment = dict(os.environ, PATH=f"{fake_bin}:/usr/bin:/bin",
                                       OMNILANE_AA_TRANSPORT_OVERLAY=overlay)
                    result = subprocess.run(["/bin/bash", str(ROOT / "bin/omnilane"), "resign", *flags],
                                            env=environment, text=True, capture_output=True,
                                            stdin=subprocess.DEVNULL, timeout=30)
                    self.assertEqual(result.returncode, HOST_CONFIG, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                    self.assertIn(str(self.local), result.stderr)
                    self.assertIn("fixture host failure", result.stderr)
                    self.assertEqual(self.host_files(), before)


if __name__ == "__main__":
    unittest.main()
