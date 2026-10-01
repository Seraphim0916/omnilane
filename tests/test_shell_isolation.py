"""Provider-free regression coverage for the shell-suite environment boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tests/offline_env.py"
VENDORS = ("codex", "claude", "grok", "agy", "gemini", "kimi", "qwen", "opencode")


class ShellIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-isolation-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bins = self.root / "host-bin"
        self.bins.mkdir()
        self.marker = self.root / "host-tools-used"
        for vendor in VENDORS:
            path = self.bins / vendor
            path.write_text("#!/bin/sh\n" +
                            f"printf '%s\\n' {shlex.quote(vendor)} >> {shlex.quote(str(self.marker))}\n"
                            "exit 97\n")
            path.chmod(0o755)
        self.home = self.root / "host-home"
        (self.home / ".omnilane").mkdir(parents=True)
        self.overlay_marker = self.root / "host-overlay-used"
        (self.home / ".omnilane/local.sh").write_text(
            f"touch {shlex.quote(str(self.overlay_marker))}\n")
        self.environment = {
            "HOME": str(self.home),
            "PATH": str(self.bins) + os.pathsep + os.environ["PATH"],
            "CODEX_BIN": str(self.bins / "codex"), "GROK_BIN": str(self.bins / "grok"),
            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "CODEX_HOME": str(self.home / ".codex"),
            "OMNILANE_HOME": str(self.home / ".omnilane"),
            "OMNILANE_PROVIDER_PROBE_SCRIPT": str(self.bins / "codex"),
            "OMNILANE_TEST_UTIL_PATH": str(self.bins),
            "OPENAI_API_KEY": "synthetic-secret", "OPENROUTER_API_KEY": "synthetic-secret",
            "PYTHONPATH": str(self.home), "NODE_OPTIONS": "--trace-warnings",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "host-configuration",
        }

    def run_isolated(self, command):
        return subprocess.run([sys.executable, "-I", str(LAUNCHER), *command],
                              env=self.environment, cwd=ROOT, text=True,
                              capture_output=True, timeout=20)

    def test_default_doctor_cannot_start_host_vendors_or_source_host_overlay(self):
        result = self.run_isolated(["/bin/bash", str(ROOT / "scripts/doctor.sh"), "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.overlay_marker.exists())

    def test_child_environment_excludes_host_configuration_and_secrets(self):
        result = self.run_isolated(["python3", "-c", "import os,json; print(json.dumps(dict(os.environ)))"])
        self.assertEqual(result.returncode, 0, result.stderr)
        child = json.loads(result.stdout)
        for name in ("CODEX_BIN", "GROK_BIN", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
                     "OMNILANE_HOME", "OMNILANE_PROVIDER_PROBE_SCRIPT", "OPENAI_API_KEY",
                     "OPENROUTER_API_KEY", "PYTHONPATH", "NODE_OPTIONS", "GIT_CONFIG_COUNT"):
            self.assertNotIn(name, child)
        self.assertNotEqual(child["HOME"], str(self.home))
        self.assertNotEqual(child["OMNILANE_TEST_UTIL_PATH"], str(self.bins))
        self.assertEqual(child["PATH"], child["OMNILANE_TEST_UTIL_PATH"])

    def test_vendor_names_are_absent_instead_of_falsely_available_stubs(self):
        code = "import json,shutil; print(json.dumps({v:shutil.which(v) for v in " + repr(VENDORS) + "}))"
        result = self.run_isolated(["python3", "-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict.fromkeys(VENDORS))

    def test_explicit_fixture_binary_still_works(self):
        result = self.run_isolated(["/bin/bash", "-c",
                                   f"CODEX_BIN={shlex.quote(str(self.bins / 'codex'))} "
                                   f"/bin/bash {shlex.quote(str(ROOT / 'scripts/doctor.sh'))} --json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().splitlines(), ["codex"])
        self.assertFalse(self.overlay_marker.exists())

    def test_swallowed_unmocked_network_command_fails_the_outer_run(self):
        for command in ("curl", "wget"):
            with self.subTest(command=command):
                result = self.run_isolated(["/bin/bash", "-c", command + " --version >/dev/null 2>&1 || true"])
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("unmocked network commands were refused: " + command, result.stderr)

    def test_fake_curl_can_be_supplied_without_a_network_violation(self):
        fake = self.root / "fake-curl"
        fake.mkdir()
        (fake / "curl").write_text("#!/bin/sh\nprintf 'fixture response\\n'\n")
        (fake / "curl").chmod(0o755)
        result = self.run_isolated(["/bin/bash", "-c",
                                   f"PATH={shlex.quote(str(fake))}:$PATH curl fixture.invalid"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "fixture response\n")

    def test_utf8_job_metadata_survives_shell_json_encoding(self):
        state = self.root / "fixture-state"
        job = state / "jobs/20260717-120005-123-5"
        job.mkdir(parents=True)
        (job / "exit").write_text("0\n")
        (job / "meta.json").write_text('{"lane":"triage","vendor":"codex","model":"模型"}\n', encoding="utf-8")
        result = self.run_isolated(["/bin/bash", "-c",
                                   f"OMNILANE_HOME={shlex.quote(str(state))} "
                                   f"/bin/bash {shlex.quote(str(ROOT / 'scripts/jobs.sh'))} --json list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)["jobs"][0]["metadata"]
        self.assertEqual(json.loads(metadata)["model"], "模型")

    def test_shell_entrypoints_bootstrap_and_do_not_restore_system_path(self):
        for path in (ROOT / "tests").glob("*.sh"):
            with self.subTest(path=path.name):
                source = path.read_text()
                self.assertIn("offline_env.py", source)
                self.assertLess(source.index("offline_env.py"), source.index("set -"))
                self.assertNotIn(":/usr/bin:/bin", source)
                self.assertNotIn('PATH="/usr/bin:/bin"', source)
        aggregate = (ROOT / "tests/run.sh").read_text()
        self.assertIn('--omnilane-offline-child', aggregate)

    def test_stale_isolation_marker_cannot_skip_any_entrypoint_bootstrap(self):
        copied = self.root / "entrypoints"
        copied.mkdir()
        shutil.copyfile(LAUNCHER, copied / "offline_env.py")
        for source in (ROOT / "tests").glob("*.sh"):
            with self.subTest(path=source.name):
                # Exercise only the real bootstrap prefix, never the full suite.
                prefix = source.read_text().split("set -", 1)[0]
                script = copied / source.name
                script.write_text(prefix + "python3 -c 'import os,json; print(json.dumps(dict(os.environ)))'\n")
                result = subprocess.run(["/bin/bash", str(script)], env=self.environment,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                child = json.loads(result.stdout)
                self.assertNotIn("CODEX_BIN", child)
                self.assertNotEqual(child["OMNILANE_TEST_UTIL_PATH"], str(self.bins))


if __name__ == "__main__":
    unittest.main()
