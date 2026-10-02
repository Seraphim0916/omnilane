"""Shell/Python executable agreement using isolated homes and fake CLIs only."""

import offline_env  # Activate suite isolation for direct file execution.
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/lib"))

import build_overlay  # noqa: E402
import resign  # noqa: E402

VENDORS = {"codex": "CODEX_BIN", "claude": "CLAUDE_BIN", "grok": "GROK_BIN", "agy": "AGY_BIN"}


class SharedBinResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-shared-bin-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key not in VENDORS.values()}
        self.env.update(OMNILANE_HOME=str(self.home), HOME=str(self.root))
        self.on_path = {name: self.fake("on-path/" + name) for name in VENDORS}
        self.env["PATH"] = str(self.root / "on-path") + os.pathsep + os.environ["PATH"]

    def fake(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def local(self, values):
        (self.home / "local.sh").write_text(
            "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in values.items()),
            encoding="utf-8",
        )

    def shell(self, name):
        vendor = "gemini" if name == "agy" else name
        return subprocess.run(
            ["bash", "-uc", 'source "$1"; b="$(vendor_bin "$2")"; printf "%s\\n" "$b"; command -v "$b"',
             "shared-bin-test", str(REPO / "scripts/lib/common.sh"), vendor],
            env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )

    def assert_agreement(self, name, expected):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        shell = self.shell(name)
        self.assertEqual(shell.returncode, 0, shell.stderr)
        command, path = shell.stdout.splitlines()
        self.assertEqual(Path(path).resolve(), expected)
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(build_overlay.cli_command(name), command)
            self.assertEqual(build_overlay.cli_path(name), expected)
            vendor = "gemini" if name == "agy" else name
            self.assertEqual(resign.current_anchors()[vendor]["cli"], expected)
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(after, before, "sourcing common.sh must not create files")

    def test_local_only_overrides_for_every_probed_vendor(self):
        pinned = {name: self.fake("outside-path/" + name) for name in VENDORS}
        self.local({VENDORS[name]: path for name, path in pinned.items()})
        for name, path in pinned.items():
            with self.subTest(name=name):
                self.assertNotIn(VENDORS[name], self.env)
                self.assert_agreement(name, path)

    def test_no_override_uses_bare_path_names(self):
        self.local({})
        for name, path in self.on_path.items():
            with self.subTest(name=name):
                self.assert_agreement(name, path)

    def test_local_assignment_wins_over_inherited_environment(self):
        inherited = self.fake("environment/codex")
        local = self.fake("local/codex")
        self.env["CODEX_BIN"] = str(inherited)
        self.local({"CODEX_BIN": local})
        self.assert_agreement("codex", local)

    def test_missing_local_file_keeps_path_and_environment_behavior(self):
        for name, path in self.on_path.items():
            with self.subTest(name=name):
                self.assert_agreement(name, path)
        pinned = self.fake("environment/codex")
        self.env["CODEX_BIN"] = str(pinned)
        self.assert_agreement("codex", pinned)

    def test_nonexistent_override_is_missing_not_a_path_fallback(self):
        missing = self.root / "missing/codex"
        for location in ("environment", "local"):
            with self.subTest(location=location):
                if location == "environment":
                    self.env["CODEX_BIN"] = str(missing)
                else:
                    self.env.pop("CODEX_BIN")
                    self.local({"CODEX_BIN": missing})
                shell = self.shell("codex")
                self.assertNotEqual(shell.returncode, 0)
                self.assertEqual(shell.stdout.strip(), str(missing))
                with mock.patch.dict(os.environ, self.env, clear=True):
                    self.assertEqual(build_overlay.cli_command("codex"), str(missing))
                    with self.assertRaises(SystemExit):
                        build_overlay.cli_path("codex")
                    self.assertIsNone(resign.current_anchors()["codex"]["cli"])

    def test_direct_python_caller_loads_local_override(self):
        pinned = self.fake("outside-path/codex")
        self.local({"CODEX_BIN": pinned})
        result = subprocess.run(
            [sys.executable, "-c", 'import sys; sys.path.insert(0, sys.argv[1]); import resign; print(resign.current_anchors()["codex"]["cli"])',
             str(REPO / "scripts/lib")], env=self.env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(pinned))

    def test_shell_lookup_is_cached_but_environment_changes_are_distinct(self):
        pinned = self.fake("environment/codex")
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("build_overlay.subprocess.run", wraps=subprocess.run) as run:
                self.assertEqual(build_overlay.cli_command("codex"), "codex")
                self.assertEqual(build_overlay.cli_command("codex"), "codex")
                self.assertEqual(run.call_count, 1)
                os.environ["CODEX_BIN"] = str(pinned)
                self.assertEqual(build_overlay.cli_command("codex"), str(pinned))
                self.assertEqual(run.call_count, 2)

    def which_bin(self, vendor):
        return subprocess.run(["bash", str(REPO / "bin/omnilane"), "which-bin", vendor],
                              env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)

    def test_which_bin_success_resolves_local_symlink_without_writes(self):
        pinned = self.fake("outside-path/codex")
        link = self.root / "codex-link"
        link.symlink_to(pinned)
        self.local({"CODEX_BIN": link})
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        result = self.which_bin("codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(pinned) + "\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), before)

    def test_which_bin_missing_executable_exits_one(self):
        self.local({"CODEX_BIN": self.root / "absent/codex"})
        result = self.which_bin("codex")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("not found", result.stderr)

    def test_which_bin_unknown_vendor_exits_two(self):
        result = self.which_bin("unknown-vendor")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("unknown", result.stderr)


if __name__ == "__main__":
    unittest.main()
