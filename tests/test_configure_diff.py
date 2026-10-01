"""Offline configure diff coverage using only explicit fake providers."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_SWITCH = "OMNILANE_CONFIGURE_DEFAULTS_ONLY"


class ConfigureDiffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-configure-diff-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "home"
        self.home.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.bins = self.root / "fake-bin"
        self.bins.mkdir()
        self.calls = self.root / "provider-calls"
        self.provider = self.bins / "claude"
        self.provider.write_text(
            "#!/bin/sh\n"
            "printf 'unexpected provider call\\n' >> " + shlex.quote(str(self.calls)) + "\n"
            "exit 91\n")
        self.provider.chmod(0o755)
        self.local = self.home / "local.sh"
        self.routing = self.home / "routing.local.yaml"
        self.routing.write_text("triage: off\n")
        self.env["OMNILANE_HOME"] = str(self.home)

    def command(self, script, *args, defaults=None):
        environment = dict(self.env)
        if defaults is not None:
            environment[DEFAULTS_SWITCH] = defaults
        return subprocess.run(["bash", str(ROOT / "scripts" / script), *args],
                              cwd=self.work, env=environment, text=True,
                              capture_output=True, timeout=20)

    def local_binary(self):
        # Match local.sh.example: this machine-specific setting is not exported.
        self.local.write_text('CLAUDE_BIN="${CLAUDE_BIN:-' + str(self.provider) + '}"\n')

    def home_snapshot(self):
        return {str(path.relative_to(self.home)): (path.stat().st_mtime_ns, path.read_bytes())
                for path in self.home.rglob("*") if path.is_file()}

    def assert_read_only(self, before):
        self.assertEqual(self.home_snapshot(), before)
        self.assertFalse((self.home / "jobs").exists(), "inspection created job state")
        self.assertFalse(self.calls.exists(), "inspection invoked a provider")

    def inspect_fixture(self, effective, defaults):
        # Intercept only the two ordinary --list children. Start configure with
        # the allowlisted real Bash so the fixture cannot replace the caller.
        real_bash = str(Path(self.env["OMNILANE_TEST_UTIL_PATH"]) / "bash")
        inspection_calls = self.root / "inspection-calls"
        inspection_calls.write_text("")
        wrapper = self.bins / "bash"
        script = (
            "#!/bin/bash\n"
            'if [[ "$#" -eq 2 && "$1" == ' + shlex.quote(str(ROOT / "scripts/dispatch.sh")) +
            ' && "$2" == --list ]]; then\n'
            '  stage=effective\n'
            '  [[ "${OMNILANE_CONFIGURE_DEFAULTS_ONLY:-0}" == 1 ]] && stage=defaults\n'
            "  printf '%s\\n' \"$stage\" >> " + shlex.quote(str(inspection_calls)) + "\n"
            '  case "$stage" in\n')
        for stage, (status, stdout, stderr) in (("effective", effective), ("defaults", defaults)):
            script += (
                "    " + stage + ")\n"
                "      printf '%s' " + shlex.quote(stdout) + "\n"
                "      printf '%s' " + shlex.quote(stderr) + " >&2\n"
                "      exit " + str(status) + ";;\n")
        script += "  esac\nfi\nexec " + shlex.quote(real_bash) + ' "$@"\n'
        wrapper.write_text(script)
        wrapper.chmod(0o755)
        environment = dict(self.env)
        environment["PATH"] = str(self.bins) + ":" + self.env["PATH"]
        result = subprocess.run([real_bash, str(ROOT / "scripts/configure.sh"), "diff"],
                                cwd=self.work, env=environment, text=True,
                                capture_output=True, timeout=20)
        return result, inspection_calls.read_text().splitlines()

    def list_lines(self, *, defaults=None, json_position=None):
        args = ["--list"]
        if json_position == "before":
            args.insert(0, "--json")
        elif json_position == "after":
            args.append("--json")
        result = self.command("dispatch.sh", *args, defaults=defaults)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = json.loads(result.stdout)["lines"] if json_position else result.stdout.splitlines()
        return {line.split(":", 1)[0]: line for line in lines}

    def assert_triage_only_diff(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = [line for line in result.stdout.splitlines() if line.startswith("default> ")]
        local = [line for line in result.stdout.splitlines() if line.startswith("local  > ")]
        self.assertEqual(len(changed), 1, result.stdout)
        self.assertEqual(len(local), 1, result.stdout)
        self.assertTrue(changed[0].startswith("default> triage:"), result.stdout)
        self.assertIn("claude ", changed[0])
        self.assertIn("# fallback (", changed[0])
        self.assertEqual(local[0].split(), ["local", ">", "triage:", "off"])

    def test_diff_retains_documented_unexported_provider_binary(self):
        self.local_binary()
        before = self.home_snapshot()
        result = self.command("configure.sh", "diff")
        self.assert_read_only(before)
        self.assert_triage_only_diff(result)

    def test_list_defaults_retains_local_binary_without_other_changes(self):
        self.local_binary()
        before = self.home_snapshot()
        for json_position in (None, "before", "after"):
            with self.subTest(json_position=json_position):
                effective = self.list_lines(json_position=json_position)
                defaults = self.list_lines(defaults="1", json_position=json_position)
                self.assertEqual(set(effective), set(defaults))
                self.assertEqual([lane for lane in effective if effective[lane] != defaults[lane]],
                                 ["triage"])
                self.assertIn("claude ", defaults["triage"])
        self.assert_read_only(before)

    def test_list_defaults_and_diff_retain_non_binary_local_path_configuration(self):
        # PATH is another documented kind of local configuration; all entries
        # here remain explicit fixture tools or the offline utility allowlist.
        self.local.write_text("PATH=" + shlex.quote(str(self.bins)) + ':"$PATH"\n')
        before = self.home_snapshot()
        effective = self.list_lines()
        defaults = self.list_lines(defaults="1")
        self.assertEqual([lane for lane in effective if effective[lane] != defaults[lane]],
                         ["triage"])
        self.assertIn("claude ", defaults["triage"])
        self.assert_triage_only_diff(self.command("configure.sh", "diff"))
        self.assert_read_only(before)

    def test_configure_effective_inspection_ignores_inherited_defaults_switch(self):
        self.local_binary()
        before = self.home_snapshot()
        self.assert_triage_only_diff(self.command("configure.sh", "diff", defaults="1"))
        self.assert_read_only(before)

    def test_failed_effective_inspection_preserves_status_and_skips_defaults(self):
        before = self.home_snapshot()
        for stdout in ("", "triage: fixture-effective-stdout -\n"):
            with self.subTest(stdout=stdout):
                result, calls = self.inspect_fixture(
                    (37, stdout, "fixture-effective-stderr\n"),
                    (0, "triage: claude fixture-default -\n", ""))
                self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
                self.assertEqual(calls, ["effective"])
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr,
                                 "omnilane: configure diff: failed to inspect effective routing table (exit 37)\n")
                self.assertNotIn("fixture-", result.stdout + result.stderr)
                self.assert_read_only(before)

    def test_failed_defaults_inspection_preserves_status_without_partial_diff(self):
        before = self.home_snapshot()
        for stdout in ("", "triage: fixture-defaults-stdout -\n"):
            with self.subTest(stdout=stdout):
                result, calls = self.inspect_fixture(
                    (0, "triage: off\n", ""), (38, stdout, "fixture-defaults-stderr\n"))
                self.assertEqual(result.returncode, 38, result.stdout + result.stderr)
                self.assertEqual(calls, ["effective", "defaults"])
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr,
                                 "omnilane: configure diff: failed to inspect defaults routing table (exit 38)\n")
                self.assertNotIn("fixture-", result.stdout + result.stderr)
                self.assert_read_only(before)

    def test_successful_empty_inspection_is_not_an_error(self):
        before = self.home_snapshot()
        for effective, defaults in (("", ""), ("", "triage: off\n"), ("triage: off\n", "")):
            with self.subTest(effective=effective, defaults=defaults):
                result, calls = self.inspect_fixture((0, effective, ""), (0, defaults, ""))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, ["effective", "defaults"])
                self.assertEqual(result.stderr, "")
                if effective:
                    self.assertEqual(result.stdout,
                                     "default> (triage not in defaults)\nlocal  > triage: off\n\n")
                else:
                    self.assertEqual(result.stdout,
                                     "local overrides present, but the effective table matches the defaults\n")
                self.assert_read_only(before)

    def test_switch_leaves_explain_validate_and_help_unchanged(self):
        self.local_binary()
        before = self.home_snapshot()
        for args in (("--explain", "triage"), ("--json", "--explain", "triage"),
                     ("--validate",), ("--validate", "--json"), ("--help",)):
            with self.subTest(args=args):
                ordinary = self.command("dispatch.sh", *args)
                with_switch = self.command("dispatch.sh", *args, defaults="1")
                self.assertEqual((with_switch.returncode, with_switch.stdout, with_switch.stderr),
                                 (ordinary.returncode, ordinary.stdout, ordinary.stderr))
        self.assertIn("vendor=off", self.command("dispatch.sh", "--explain", "triage").stdout)
        self.assert_read_only(before)

    def test_only_exact_defaults_value_changes_explicit_list(self):
        self.local_binary()
        before = self.home_snapshot()
        effective = self.list_lines()
        for value in ("", "0", "true"):
            with self.subTest(value=value):
                self.assertEqual(self.list_lines(defaults=value), effective)
        self.assert_read_only(before)

    def test_ordinary_fake_dispatch_ignores_switch_and_workers_do_not_inherit_it(self):
        worker_calls = self.root / "worker-calls.jsonl"
        worker = self.root / "fake-worker.py"
        worker.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            f"with open({str(worker_calls)!r}, 'a') as handle:\n"
            "    handle.write(json.dumps({'switch': os.environ.get('" + DEFAULTS_SWITCH + "'),\n"
            "                             'task': pathlib.Path(sys.argv[4]).read_text()}) + '\\n')\n"
            "pathlib.Path(sys.argv[5]).write_text('ordinary fake worker completed\\n')\n")
        worker.chmod(0o755)
        self.routing.write_text(f'triage: exec "{worker}" -\n')
        for background in (False, True):
            with self.subTest(background=background):
                args = ["--background", "--single-shot"] if background else []
                result = self.command("dispatch.sh", *args, "triage", "ordinary fixture task",
                                      defaults="1")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if background:
                    job_id = result.stdout.strip()
                    self.assertRegex(job_id, r"^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$")
                    waited = self.command("jobs.sh", "wait", job_id, "--timeout", "10")
                    self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
                    self.assertEqual(waited.stdout, "done exit=0\n")
                    deadline = time.monotonic() + 10
                    while not (self.home / "inbox" / (job_id + ".json")).exists():
                        self.assertLess(time.monotonic(), deadline, "fake worker publication unfinished")
                        time.sleep(.02)
                else:
                    self.assertIn("ordinary fake worker completed", result.stdout)
                record = json.loads(worker_calls.read_text().splitlines()[-1])
                self.assertEqual(record, {"switch": None, "task": "ordinary fixture task\n"})
        self.assertEqual(len(worker_calls.read_text().splitlines()), 2)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
