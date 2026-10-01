"""Offline configure-set publication boundaries with explicit fake children."""
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
CANDIDATE_INPUT = "OMNILANE_CONFIGURE_VALIDATE_FILE"


def snapshot(path):
    if not path.exists():
        return None
    stat = path.stat()
    return {"bytes": path.read_text(), "inode": stat.st_ino,
            "mode": stat.st_mode & 0o7777, "mtime_ns": stat.st_mtime_ns}


class ConfigureSetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-configure-set-")
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
        self.routing = self.home / "routing.local.yaml"
        self.env["OMNILANE_HOME"] = str(self.home)
        self.real_bash = str(Path(self.env["OMNILANE_TEST_UTIL_PATH"]) / "bash")

    def existing_routing(self, mode=0o640):
        self.routing.write_text("# existing user note\ntriage: off\nhard-judgment: off\n")
        self.routing.chmod(mode)
        os.utime(self.routing, ns=(1650000000123456789, 1650000000123456789))

    def command(self, script, *args, candidate=None, umask=0o022, wrapper=False):
        environment = dict(self.env)
        if candidate is not None:
            environment[CANDIDATE_INPUT] = str(candidate)
        if wrapper:
            environment["PATH"] = str(self.bins) + ":" + self.env["PATH"]
        return subprocess.run([self.real_bash, str(ROOT / "scripts" / script), *args],
                              cwd=self.work, env=environment, text=True,
                              capture_output=True, timeout=20, umask=umask)

    def validation_fixture(self, status, stdout="", stderr="fixture validator output\n"):
        observation = self.root / "observation.json"
        observer = self.root / "observe.py"
        observer.write_text(
            "import json, os, pathlib, sys\n"
            "def snapshot(p):\n"
            "    if not p or not pathlib.Path(p).exists(): return None\n"
            "    p=pathlib.Path(p); s=p.stat()\n"
            "    return {'bytes':p.read_text(),'inode':s.st_ino,'mode':s.st_mode & 0o7777,'mtime_ns':s.st_mtime_ns}\n"
            "candidate=os.environ.get('" + CANDIDATE_INPUT + "')\n"
            "public=pathlib.Path(os.environ['OMNILANE_HOME'])/'routing.local.yaml'\n"
            "pathlib.Path(sys.argv[1]).write_text(json.dumps({'public':snapshot(public),"
            "'candidate':snapshot(candidate),'candidate_path':candidate}))\n")
        wrapper = self.bins / "bash"
        wrapper.write_text(
            "#!/bin/bash\n"
            'if [[ "$#" -eq 2 && "$1" == ' + shlex.quote(str(ROOT / "scripts/dispatch.sh")) +
            ' && "$2" == --validate ]]; then\n'
            "  python3 " + shlex.quote(str(observer)) + " " + shlex.quote(str(observation)) + "\n"
            "  printf '%s' " + shlex.quote(stdout) + "\n"
            "  printf '%s' " + shlex.quote(stderr) + " >&2\n"
            "  exit " + str(status) + "\n"
            "fi\nexec " + shlex.quote(self.real_bash) + ' "$@"\n')
        wrapper.chmod(0o755)
        result = self.command("configure.sh", "set", "triage", "claude sonnet high", wrapper=True)
        return result, json.loads(observation.read_text())

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.home.glob("routing.local.yaml.*")), [])

    def assert_validation_before_publication(self, observed, before):
        self.assertEqual(observed["public"], before)
        self.assertIsNotNone(observed["candidate"])
        self.assertIn("triage: claude sonnet high\n", observed["candidate"]["bytes"])
        self.assertEqual(observed["candidate"]["mode"], 0o600)
        self.assertFalse(Path(observed["candidate_path"]).exists())
        self.assert_no_temporary_files()

    def retained_filter_failure(self, status, output):
        # Match only the retained-content read, using fixture utilities for
        # both the former grep implementation and the one-pass awk filter.
        filter_calls = self.root / "filter-calls"
        validation_calls = self.root / "validation-calls"
        for path in (filter_calls, validation_calls):
            if path.exists():
                path.unlink()
        grep_args = ["-v", "^# updated by 'configure set'", str(self.routing)]
        for utility in ("grep", "awk"):
            real_utility = str(Path(self.env["OMNILANE_TEST_UTIL_PATH"]) / utility)
            wrapper = self.bins / utility
            wrapper.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                f"old_filter = {utility!r} == 'grep' and args == "
                f"{grep_args!r}\n"
                f"new_filter = {utility!r} == 'awk' and "
                f"args[-1:] == {[str(self.routing)]!r} and "
                '"stamp=# updated by \'configure set\'" in args and "lane=triage:" in args\n'
                "if old_filter or new_filter:\n"
                f"    pathlib.Path({str(filter_calls)!r}).write_text('called\\n')\n"
                f"    sys.stdout.write({output!r})\n"
                "    sys.stderr.write('fixture read-error detail\\n')\n"
                f"    sys.exit({status})\n"
                f"os.execv({real_utility!r}, [{real_utility!r}, *args])\n")
            wrapper.chmod(0o755)
        validator = self.bins / "bash"
        validator.write_text(
            "#!/bin/bash\n"
            'if [[ "$#" -eq 2 && "$1" == ' + shlex.quote(str(ROOT / "scripts/dispatch.sh")) +
            ' && "$2" == --validate ]]; then\n'
            "  printf 'called\\n' >> " + shlex.quote(str(validation_calls)) + "\n"
            "fi\nexec " + shlex.quote(self.real_bash) + ' "$@"\n')
        validator.chmod(0o755)
        result = self.command("configure.sh", "set", "triage", "off", wrapper=True)
        return result, filter_calls, validation_calls

    def test_retained_content_read_failure_preserves_existing_file(self):
        for status in (1, 2, 37, 126, 130):
            for output in ("", "# first user note\nhard-judgment: off\n"):
                with self.subTest(status=status, output=output):
                    self.routing.write_text("# first user note\ntriage: off\nhard-judgment: off\n"
                                            "# later user note\nbulk-mechanical: off\n")
                    self.routing.chmod(0o640)
                    os.utime(self.routing, ns=(1650000000123456789, 1650000000123456789))
                    before = snapshot(self.routing)
                    result, filter_calls, validation_calls = self.retained_filter_failure(status, output)
                    self.assertEqual(filter_calls.read_text(), "called\n")
                    self.assertEqual(snapshot(self.routing), before)
                    self.assertFalse(validation_calls.exists(), "partial candidate reached validation")
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr,
                                     f"omnilane: configure set: failed to retain existing routing content (exit {status})\n")
                    self.assert_no_temporary_files()

    def test_successful_empty_retained_content_is_valid(self):
        for content in ("", "# updated by 'configure set' old stamp\n", "triage: off\n",
                        "# updated by 'configure set' old stamp\ntriage: off\n"):
            with self.subTest(content=content):
                self.routing.write_text(content)
                self.routing.chmod(0o640)
                result = self.command("configure.sh", "set", "triage", "off")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "set triage -> off\n")
                self.assertEqual(result.stderr, "")
                lines = self.routing.read_text().splitlines()
                self.assertTrue(lines[0].startswith("# updated by 'configure set' on "))
                self.assertEqual(lines[1:], ["triage: off"])
                self.assertEqual(snapshot(self.routing)["mode"], 0o640)
                self.assert_no_temporary_files()

    def test_retained_content_keeps_literal_prefix_semantics_and_order(self):
        # Preserve ordinary comments, spacing, and a missing final newline in
        # the same line-oriented way as grep, while removing all exact prefixes.
        retained = ("# user note about triage: off\n\n"
                    "hard-judgment: off\n  # updated by 'configure set' is a user note\n"
                    "# updated by 'configure setter' is a different comment\n"
                    "# triage: keep this comment\nbulk-mechanical: off\n# final user note")
        self.routing.write_text("# updated by 'configure set' old stamp\ntriage: off\n" +
                                retained + "\ntriage: off\n# updated by 'configure set' another stamp\n")
        # Also exercise the ordinary unterminated final-line behavior.
        for content in (self.routing.read_text(), retained):
            with self.subTest(content=content):
                self.routing.write_text(content)
                result = self.command("configure.sh", "set", "triage", "off")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                lines = self.routing.read_text().splitlines(keepends=True)
                self.assertTrue(lines[0].startswith("# updated by 'configure set' on "))
                self.assertEqual("".join(lines[1:]), "triage: off\n" + retained + "\n")
                self.assert_no_temporary_files()

    def test_unexpected_validation_failure_preserves_existing_file(self):
        for status in (1, 37, 126, 130):
            for stdout in ("", "PASS triage selected=1/1 vendor=claude\n",
                           "FAIL triage fixture-partial-output\n"):
                with self.subTest(status=status, stdout=stdout):
                    self.existing_routing()
                    before = snapshot(self.routing)
                    result, observed = self.validation_fixture(status, stdout)
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr,
                                     f"omnilane: configure set: failed to validate routing candidate (exit {status})\n")
                    self.assertEqual(snapshot(self.routing), before)
                    self.assert_validation_before_publication(observed, before)

    def test_unexpected_validation_failure_does_not_create_routing(self):
        result, observed = self.validation_fixture(37)
        self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.routing.exists())
        self.assert_validation_before_publication(observed, None)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_known_validation_statuses_accept_only_after_validation(self):
        for status, stdout in ((0, "PASS triage selected=1/1 vendor=claude\n"),
                               (2, "FAIL hard-judgment duplicate-lane\n"),
                               (4, "WARN triage no-candidate-available\n")):
            for existing in (True, False):
                with self.subTest(status=status, existing=existing):
                    if self.routing.exists():
                        self.routing.unlink()
                    if existing:
                        self.existing_routing()
                    before = snapshot(self.routing)
                    result, observed = self.validation_fixture(status, stdout)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "set triage -> claude sonnet high\n")
                    self.assertEqual(result.stderr, "")
                    self.assert_validation_before_publication(observed, before)
                    after = snapshot(self.routing)
                    self.assertEqual(after["inode"], observed["candidate"]["inode"])
                    self.assertEqual(after["bytes"], observed["candidate"]["bytes"])
                    self.assertEqual(after["mode"], 0o640 if existing else 0o644)
                    if existing:
                        self.assertIn("# existing user note\n", after["bytes"])
                        self.assertIn("hard-judgment: off\n", after["bytes"])

    def test_target_lane_failure_rejects_without_publication(self):
        for status in (0, 2, 4):
            for existing in (True, False):
                with self.subTest(status=status, existing=existing):
                    if self.routing.exists():
                        self.routing.unlink()
                    if existing:
                        self.existing_routing()
                    before = snapshot(self.routing)
                    result, observed = self.validation_fixture(status, "FAIL triage fixture-invalid\n")
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "omnilane: rejected — FAIL triage fixture-invalid\n")
                    self.assertEqual(snapshot(self.routing), before)
                    self.assert_validation_before_publication(observed, before)

    def test_real_validator_keeps_unrelated_errors_and_warnings_acceptable(self):
        self.routing.write_text("hard-judgment: off\nhard-judgment: off\n")
        result = self.command("configure.sh", "set", "triage", "claude sonnet high")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        validation = self.command("dispatch.sh", "--validate")
        self.assertEqual(validation.returncode, 2, validation.stdout + validation.stderr)
        self.assertIn("WARN triage no-candidate-available\n", validation.stdout)
        self.assertIn("FAIL hard-judgment duplicate-lane\n", validation.stdout)
        self.assert_no_temporary_files()

    def test_new_file_mode_honors_umask(self):
        for umask in (0o000, 0o002, 0o022, 0o027, 0o077):
            with self.subTest(umask=oct(umask)):
                if self.routing.exists():
                    self.routing.unlink()
                result = self.command("configure.sh", "set", "triage", "off", umask=umask)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(snapshot(self.routing)["mode"], 0o666 & ~umask)
                self.assert_no_temporary_files()

    def test_existing_file_mode_is_preserved_on_success(self):
        for mode in (0o400, 0o600, 0o640):
            with self.subTest(mode=oct(mode)):
                if self.routing.exists():
                    self.routing.chmod(0o600)
                    self.routing.unlink()
                self.existing_routing(mode)
                before = snapshot(self.routing)
                result = self.command("configure.sh", "set", "triage", "off")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                after = snapshot(self.routing)
                self.assertEqual(after["mode"], mode)
                self.assertNotEqual(after["inode"], before["inode"])
                self.assertNotEqual(after["mtime_ns"], before["mtime_ns"])
                self.assert_no_temporary_files()

    def test_private_candidate_validation_keeps_real_local_configuration(self):
        provider_calls = self.root / "provider-calls"
        provider = self.bins / "claude"
        provider.write_text("#!/bin/sh\nprintf 'unexpected call\\n' >> " +
                            shlex.quote(str(provider_calls)) + "\nexit 91\n")
        provider.chmod(0o755)
        (self.home / "local.sh").write_text("CLAUDE_BIN=" + shlex.quote(str(provider)) + "\n")
        self.existing_routing()
        candidate = self.root / "candidate.yaml"
        candidate.write_text("triage: claude sonnet high\n")
        before = snapshot(self.routing)
        candidate_before = snapshot(candidate)
        for args in (("--validate",), ("--validate", "--json"), ("--json", "--validate")):
            with self.subTest(args=args):
                result = self.command("dispatch.sh", *args, candidate=candidate)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                lines = json.loads(result.stdout)["lines"] if "--json" in args else result.stdout.splitlines()
                self.assertIn("PASS triage selected=1/1 vendor=claude", lines)
        self.assertEqual(snapshot(self.routing), before)
        self.assertEqual(snapshot(candidate), candidate_before)
        self.assertFalse(provider_calls.exists())
        self.assertFalse((self.home / "jobs").exists())

    def test_candidate_input_leaves_other_inspections_unchanged(self):
        self.existing_routing()
        candidate = self.root / "candidate.yaml"
        candidate.write_text("triage: claude sonnet high\n")
        before = snapshot(self.routing)
        for args in (("--list",), ("--json", "--list"), ("--list", "--json"),
                     ("--explain", "triage"), ("--json", "--explain", "triage"), ("--help",)):
            with self.subTest(args=args):
                ordinary = self.command("dispatch.sh", *args)
                with_candidate = self.command("dispatch.sh", *args, candidate=candidate)
                self.assertEqual((with_candidate.returncode, with_candidate.stdout, with_candidate.stderr),
                                 (ordinary.returncode, ordinary.stdout, ordinary.stderr))
        self.assertEqual(snapshot(self.routing), before)

    def test_execution_ignores_candidate_and_workers_do_not_inherit_input(self):
        calls = self.root / "worker-calls.jsonl"
        worker = self.root / "fake-worker.py"
        worker.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            f"with open({str(calls)!r}, 'a') as handle:\n"
            "    handle.write(json.dumps({'candidate':os.environ.get('" + CANDIDATE_INPUT + "'),"
            "'task':pathlib.Path(sys.argv[4]).read_text()})+'\\n')\n"
            "pathlib.Path(sys.argv[5]).write_text('ordinary fake worker completed\\n')\n")
        worker.chmod(0o755)
        self.routing.write_text(f'triage: exec "{worker}" -\n')
        candidate = self.root / "candidate.yaml"
        candidate.write_text("triage: off\n")
        for background in (False, True):
            with self.subTest(background=background):
                args = ["--background", "--single-shot"] if background else []
                result = self.command("dispatch.sh", *args, "triage", "ordinary fixture task", candidate=candidate)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if background:
                    job_id = result.stdout.strip()
                    waited = self.command("jobs.sh", "wait", job_id, "--timeout", "10")
                    self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
                    self.assertEqual(waited.stdout, "done exit=0\n")
                    deadline = time.monotonic() + 10
                    while not (self.home / "inbox" / (job_id + ".json")).exists():
                        self.assertLess(time.monotonic(), deadline, "fake worker publication unfinished")
                        time.sleep(.02)
                else:
                    self.assertIn("ordinary fake worker completed", result.stdout)
                record = json.loads(calls.read_text().splitlines()[-1])
                self.assertEqual(record, {"candidate": None, "task": "ordinary fixture task\n"})
        self.assertEqual(len(calls.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
