"""Offline configure-unset publication boundaries with fake utility failures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]


def snapshot(path):
    if not path.exists():
        return None
    stat = path.stat()
    return {"bytes": path.read_text(), "inode": stat.st_ino,
            "mode": stat.st_mode & 0o7777, "mtime_ns": stat.st_mtime_ns}


class ConfigureUnsetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-configure-unset-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "config"
        self.home.mkdir()
        self.bins = self.root / "fake-bin"
        self.bins.mkdir()
        self.routing = self.home / "routing.local.yaml"
        self.env["OMNILANE_HOME"] = str(self.home)
        self.env["PATH"] = str(self.bins) + ":" + self.env["PATH"]
        self.utilities = Path(self.env["OMNILANE_TEST_UTIL_PATH"])
        self.calls = self.root / "unexpected-child-calls"
        self.observation = self.root / "observation.json"
        self.addCleanup(lambda: self.assertFalse(self.calls.exists()))
        # Unset must remain independent of validation and provider execution.
        for name in ("bash", "claude", "codex", "gemini"):
            path = self.bins / name
            path.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                f"with pathlib.Path({str(self.calls)!r}).open('a') as handle:\n"
                f"    handle.write({name!r} + ' ' + repr(sys.argv[1:]) + '\\n')\n"
                "sys.exit(91)\n")
            path.chmod(0o755)

    def existing_routing(self, content=None, mode=0o640):
        if content is None:
            content = ("# first user note\ntriage: off\nhard-judgment: off\n"
                       "# later user note\nbulk-mechanical: off\n")
        self.routing.write_text(content)
        self.routing.chmod(mode)
        os.utime(self.routing, ns=(1650000000123456789, 1650000000123456789))

    def command(self, lane="triage"):
        return subprocess.run([str(self.utilities / "bash"),
                               str(ROOT / "scripts/configure.sh"), "unset", lane],
                              cwd=self.root, env=self.env, text=True,
                              capture_output=True, timeout=20)

    def utility_fixture(self, stage, status=None, output="", lane="triage"):
        if self.observation.exists():
            self.observation.unlink()
        # The old filter reads its backup using grep; the replacement performs
        # one literal-prefix awk pass over the original into a private file.
        for utility in ("grep", "awk"):
            real = str(self.utilities / utility)
            wrapper = self.bins / utility
            wrapper.write_text(
                f"#!{sys.executable}\n"
                "import json, os, pathlib, sys\n"
                "args=sys.argv[1:]\n"
                f"failure_status={status!r}\n"
                f"public=pathlib.Path({str(self.routing)!r})\n"
                "def snap(path):\n"
                "    if not path.exists(): return None\n"
                "    s=path.stat()\n"
                "    return {'bytes':path.read_text(),'inode':s.st_ino,"
                "'mode':s.st_mode & 0o7777,'mtime_ns':s.st_mtime_ns}\n"
                f"presence={utility!r} == 'grep' and args == "
                f"{['-q', '^' + lane + ':', str(self.routing)]!r}\n"
                f"old_filter={utility!r} == 'grep' and args == "
                f"{['-v', '^' + lane + ':', str(self.routing) + '.bak']!r}\n"
                f"new_filter={utility!r} == 'awk' and args[-1:] == "
                f"{[str(self.routing)]!r} and {'lane=' + lane + ':'!r} in args\n"
                f"selected=presence if {stage!r} == 'presence' else (old_filter or new_filter)\n"
                "if selected:\n"
                "    candidates=list(public.parent.glob('routing.local.yaml.tmp.*'))\n"
                "    record={'public':snap(public),'backup':snap(pathlib.Path(str(public)+'.bak')),"
                "'candidates':[{'path':str(p),'snapshot':snap(p)} for p in candidates]}\n"
                f"    pathlib.Path({str(self.observation)!r}).write_text(json.dumps(record))\n"
                "    if failure_status is not None:\n"
                f"        sys.stdout.write({output!r})\n"
                "        sys.stderr.write('fixture read-error detail\\n')\n"
                "        sys.exit(failure_status)\n"
                f"os.execv({real!r}, [{real!r}, *args])\n")
            wrapper.chmod(0o755)

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.home.glob("routing.local.yaml.*")), [])

    def assert_private_filter(self, before):
        observed = json.loads(self.observation.read_text())
        self.assertEqual(observed["public"], before)
        self.assertIsNone(observed["backup"])
        self.assertEqual(len(observed["candidates"]), 1)
        candidate = observed["candidates"][0]
        self.assertEqual(candidate["snapshot"]["mode"], 0o600)
        self.assertEqual(Path(candidate["path"]).parent, self.home)
        self.assertFalse(Path(candidate["path"]).exists())
        self.assert_no_temporary_files()
        return candidate

    def test_presence_read_error_is_not_a_successful_no_match(self):
        for status in (2, 37, 126, 130):
            with self.subTest(status=status):
                self.existing_routing()
                before = snapshot(self.routing)
                self.utility_fixture("presence", status)
                result = self.command()
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr,
                                 f"omnilane: configure unset: failed to inspect existing routing content (exit {status})\n")
                self.assertEqual(snapshot(self.routing), before)
                self.assertEqual(json.loads(self.observation.read_text()),
                                 {"public": before, "backup": None, "candidates": []})
                self.assert_no_temporary_files()

    def test_filter_read_error_preserves_existing_file(self):
        for status in (1, 2, 37, 126, 130):
            for output in ("", "# first user note\nhard-judgment: off\n"):
                with self.subTest(status=status, output=output):
                    self.existing_routing()
                    before = snapshot(self.routing)
                    self.utility_fixture("filter", status, output)
                    result = self.command()
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr,
                                     f"omnilane: configure unset: failed to retain existing routing content (exit {status})\n")
                    self.assertEqual(snapshot(self.routing), before)
                    self.assert_private_filter(before)

    def test_success_publishes_private_candidate_after_filtering(self):
        self.existing_routing()
        before = snapshot(self.routing)
        self.utility_fixture("filter")
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "unset triage (local override removed)\n")
        self.assertEqual(result.stderr, "")
        self.assert_private_filter(before)
        after = snapshot(self.routing)
        self.assertEqual(after["bytes"], before["bytes"].replace("triage: off\n", ""))
        self.assertEqual(after["inode"], before["inode"])
        self.assertNotEqual(after["mtime_ns"], before["mtime_ns"])
        self.assertEqual(after["mode"], before["mode"])

    def test_publication_error_returns_failure_without_claiming_rollback(self):
        self.existing_routing()
        before = snapshot(self.routing)
        wrapper = self.bins / "cat"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            "args=sys.argv[1:]\n"
            f"home=pathlib.Path({str(self.home)!r})\n"
            "if len(args) == 1 and pathlib.Path(args[0]).parent == home and "
            "pathlib.Path(args[0]).name.startswith('routing.local.yaml.tmp.'):\n"
            "    sys.stdout.write('# copied prefix\\n')\n"
            "    sys.stderr.write('fixture publication-error detail\\n')\n"
            "    sys.exit(37)\n"
            f"os.execv({str(self.utilities / 'cat')!r}, "
            f"[{str(self.utilities / 'cat')!r}, *args])\n")
        wrapper.chmod(0o755)
        result = self.command()
        self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr,
                         "omnilane: configure unset: failed to publish routing content (exit 37)\n")
        after = snapshot(self.routing)
        self.assertEqual(after["bytes"], "# copied prefix\n")
        self.assertEqual(after["inode"], before["inode"])
        self.assertEqual(after["mode"], before["mode"])
        self.assert_no_temporary_files()

    def test_candidate_preparation_error_preserves_existing_file(self):
        self.existing_routing()
        before = snapshot(self.routing)
        wrapper = self.bins / "mktemp"
        wrapper.write_text("#!/bin/sh\nprintf 'fixture preparation-error detail\\n' >&2\nexit 37\n")
        wrapper.chmod(0o755)
        result = self.command()
        self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr,
                         "omnilane: configure unset: failed to prepare routing candidate (exit 37)\n")
        self.assertEqual(snapshot(self.routing), before)
        self.assert_no_temporary_files()

    def test_empty_retained_content_is_valid(self):
        for content in ("triage: off\n", "triage: off", "triage: off\ntriage: off\n"):
            with self.subTest(content=content):
                self.existing_routing(content)
                result = self.command()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "unset triage (local override removed)\n")
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.routing.read_bytes(), b"")
                self.assertEqual(snapshot(self.routing)["mode"], 0o640)
                self.assert_no_temporary_files()

    def test_absent_target_is_a_metadata_preserving_no_op(self):
        for content in ("", "# note\n", "hard-judgment: off\n", "triage-other: off\n"):
            with self.subTest(content=content):
                self.existing_routing(content)
                before = snapshot(self.routing)
                result = self.command()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "no local override for 'triage'\n")
                self.assertEqual(result.stderr, "")
                self.assertEqual(snapshot(self.routing), before)
                self.assert_no_temporary_files()

    def test_absent_file_or_directory_remains_absent(self):
        for absent_directory in (False, True):
            with self.subTest(absent_directory=absent_directory):
                if absent_directory:
                    self.home.rmdir()
                result = self.command()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "no local override for 'triage'\n")
                self.assertEqual(result.stderr, "")
                self.assertFalse(self.routing.exists())
                self.assertEqual(self.home.exists(), not absent_directory)

    def test_only_exact_lane_prefix_rows_are_removed(self):
        retained = ("# updated by 'configure set' old stamp\n# user note\n\n"
                    "triage-other: off\nhard-judgment: off\n"
                    "# triage: comment\n  triage: indented row\n"
                    "# updated by 'configure set' later stamp\n# final user note")
        self.existing_routing("triage: off\n" + retained + "\ntriage: off\n")
        for content in (self.routing.read_text(), "triage: off\n" + retained):
            with self.subTest(content=content):
                self.existing_routing(content)
                result = self.command()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.routing.read_text(), retained + "\n")
                self.assert_no_temporary_files()

    def test_valid_custom_lane_can_be_removed_without_known_lane_lookup(self):
        self.existing_routing("custom-lane-2: off\ncustom-lane-20: off\ntriage: off\n")
        result = self.command("custom-lane-2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "unset custom-lane-2 (local override removed)\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.routing.read_text(), "custom-lane-20: off\ntriage: off\n")
        self.assert_no_temporary_files()

    def test_existing_permission_mode_is_preserved(self):
        for mode in (0o600, 0o640, 0o644, 0o660):
            with self.subTest(mode=oct(mode)):
                self.existing_routing(mode=mode)
                result = self.command()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(snapshot(self.routing)["mode"], mode)
                self.assert_no_temporary_files()

    def test_invalid_lane_rejects_before_mutation(self):
        for lane in ("", "Bad_Lane", "two words"):
            with self.subTest(lane=lane):
                self.existing_routing()
                before = snapshot(self.routing)
                result = self.command(lane)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid lane", result.stderr)
                self.assertEqual(snapshot(self.routing), before)
                self.assert_no_temporary_files()


if __name__ == "__main__":
    unittest.main()
