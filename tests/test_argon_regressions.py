"""Reproduce Argon's findings against the real helpers and close phase."""

import offline_env  # noqa: F401
import ast
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import cli_provenance as cp
import origin_policy as op


def fixture_tree():
    path = Path(os.environ.get("ARGON_FIXTURE_UNDER_TEST", ROOT / "tests/test_live_close_deadline.sh"))
    source = path.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    return ast.parse(source)


def helpers():
    tree = fixture_tree()
    # Helpers read the fixture's module-level hang guard (GENEROUS) as a default argument.
    guard = [node for node in tree.body if isinstance(node, ast.Assign)
             and [getattr(t, 'id', None) for t in node.targets] == ['GENEROUS']]
    definitions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   and node.name in {"wait_for_stopped", "wait_for_exit", "record_exit", "cleanup_fixture"}]
    for node in definitions:
        if node.name == "record_exit":
            node.args.defaults = []
    namespace = dict(os=os, time=time, signal=signal, sys=sys, shutil=shutil)
    exec(compile(ast.Module(body=guard + definitions, type_ignores=[]), "close-helpers", "exec"), namespace)
    return namespace


class PathRegressions(unittest.TestCase):
    def test_II1_bare_filenames_and_windows_paths_are_not_identifiers(self):
        for value in ("tool-1.2.3", "grok-1.0.25-macos-aarch64", "python3.9",
                      r"C:\work\verify-0.55\cli.exe"):
            with self.subTest(value=value):
                self.assertEqual(cp.family(value), value)

    def test_II1_identifier_channel_still_accepts_version_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cli"
            path.touch()
            channel = op._channel("developer-id", path, dict(signer="TEST000001", identifier="vendor.1.2.3"))
            newer = op._channel("developer-id", path, dict(signer="TEST000001", identifier="vendor.1.2.4"))
            self.assertEqual(channel["identifier_family"], "vendor.<version>")
            self.assertEqual(channel["identifier_family"], newer["identifier_family"])

    def test_II2_grok_platform_suffix_is_preserved(self):
        for suffix in ("arm64", "aarch64", "x86_64", "amd64", "win-x64", "freebsd-amd64",
                       "linux-x86_64", "macos-aarch64"):
            with self.subTest(suffix=suffix):
                self.assertEqual(cp.family("/work/verify-0.55/.grok/downloads/grok-1.0.25-" + suffix),
                                 "/work/verify-0.55/.grok/downloads/grok-<version>-" + suffix)
        self.assertEqual(cp.family("/u/.grok/downloads/grok-1.0.25-beta.1+build.2-linux-amd64"),
                         "/u/.grok/downloads/grok-<version>-linux-amd64")

    def test_II3_full_layout_accepts_redundant_separators_and_v_prefix(self):
        for value, expected in (
            ("/u/.local/share/claude/versions//2.1.276/", "/u/.local/share/claude/versions/<version>"),
            ("/u/.local/share/claude/versions/./2.1.276", "/u/.local/share/claude/versions/<version>"),
            (".codex/packages/standalone/releases/v0.55.0/bin/codex", ".codex/packages/standalone/releases/<version>/bin/codex"),
            ("/u/.codex/packages/standalone/releases/0.55.0-beta.1/bin/codex", "/u/.codex/packages/standalone/releases/<version>/bin/codex"),
        ):
            with self.subTest(value=value):
                self.assertEqual(cp.family(value), expected)

    def test_II3_ambiguous_short_layouts_do_not_grant_location_trust(self):
        for value in ("claude/versions/2.1.276", "releases/0.55.0/bin/codex",
                      "/u/.local/share/claude/versions/../2.1.276"):
            self.assertEqual(cp.family(value), value)


class FixtureRegressions(unittest.TestCase):
    def test_I1_V1_stop_receipt_without_waitid(self):
        child = subprocess.Popen([sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGSTOP)"])
        try:
            with patch.object(os, "waitid", side_effect=AttributeError("Python 3.9 has no waitid"), create=True):
                helpers()["wait_for_stopped"](child.pid)
            os.kill(child.pid, signal.SIGCONT)
            self.assertEqual(child.wait(timeout=2), 0)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def test_I4_exit_receipt_is_atomically_published(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "exit"
            original = Path.write_text

            def write(path, data, *args, **kwargs):
                self.assertNotEqual(path, destination, "reader can observe an empty exit file")
                return original(path, data, *args, **kwargs)

            with patch.object(Path, "write_text", write):
                helpers()["record_exit"](Mock(wait=Mock(return_value=7)), destination)
            self.assertEqual(destination.read_text(), "7\n")
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_I4_cleanup_preserves_original_error_and_removes_scratch(self):
        scratch = Path(tempfile.mkdtemp())
        finalbody = next(node.finalbody for node in fixture_tree().body if isinstance(node, ast.Try))
        namespace = helpers()
        namespace.update(scratch=scratch, processes=[], opened_fds=set(),
                         exit_recorders=[Mock(is_alive=Mock(return_value=True))])
        try:
            with self.assertRaisesRegex(ValueError, "original failure"):
                try:
                    raise ValueError("original failure")
                finally:
                    exec(compile(ast.Module(body=finalbody, type_ignores=[]), "cleanup", "exec"), namespace)
            self.assertFalse(scratch.exists(), "failed recorder skipped scratch cleanup")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_IV1_close_receipt_rejects_even_briefly_surviving_pid(self):
        with patch.object(os, "kill", side_effect=[None, ProcessLookupError]):
            with self.assertRaisesRegex(AssertionError, "survived close"):
                helpers()["wait_for_exit"](12345)


class ShutdownRegressions(unittest.TestCase):
    def shutdown_source(self):
        path = Path(os.environ.get("ARGON_WORKER_UNDER_TEST", ROOT / "scripts/lib/job-worker.sh"))
        source = path.read_text()
        start = source.index('set +e\nif [[ "$close_requested" -eq 1 ]]')
        return source[start:source.index('\nset -e', start)]

    def run_shutdown(self, mode, delayed_wait=False):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            runner = case / "runner.py"
            runner.write_text("""import os, pathlib, signal, sys, time
case = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
def terminate(*args):
    (case / 'term').touch()
    if mode != 'kill':
        sys.exit(0)
signal.signal(signal.SIGTERM, terminate)
(case / 'ready').touch()
if mode == 'grace':
    while not (case / 'timer-started').exists(): time.sleep(.01)
    time.sleep(.1)
else:
    while True: time.sleep(.02)
""")
            perl = case / "perl"
            perl.write_text('#!/bin/bash\nprintf "perl\\n" >> "$CASE/perl-calls"\n'
                            ': > "$CASE/timer-started"\n'
                            'exec ' + shutil.which("perl") + ' "$@"\n')
            perl.chmod(0o755)
            shell = r'''
"$PYTHON" "$CASE/runner.py" "$CASE" "$MODE" &
runner_pid=$!
while [[ ! -e "$CASE/ready" ]]; do sleep .01; done
close_requested=1
close_drain_failed=0
CLOSE_RUNNER_GRACE=0.6
CLOSE_TERM_GRACE=0.3
CLOSE_KILL_GRACE=0.3
'''
            close_source = self.shutdown_source()
            if delayed_wait:
                # Force the deadline before the parent enters its wait/read.
                # The historical single alarm loses it; pipe EOF persists.
                if '      wait "$runner_pid" 2>/dev/null' in close_source:
                    close_source = close_source.replace('      wait "$runner_pid" 2>/dev/null', r'''
      if [[ ! -e "$CASE/wait-delayed" ]]; then
        : > "$CASE/wait-delayed"
        command sleep .8
      fi
      wait "$runner_pid" 2>/dev/null''')
                else:
                    self.assertIn('    while IFS= read -r _tick <&8;', close_source)
                    close_source = close_source.replace('    while IFS= read -r _tick <&8;', r'''
    if [[ ! -e "$CASE/wait-delayed" ]]; then
      : > "$CASE/wait-delayed"
      command sleep .8
    fi
    while IFS= read -r _tick <&8;''')
            shell += close_source + r'''
if kill -0 "$runner_pid" 2>/dev/null; then kill -KILL "$runner_pid"; wait "$runner_pid"; exit 9; fi
printf 'result=%s,%s\n' "$runner_rc" "$close_drain_failed"
'''
            env = dict(os.environ, CASE=str(case), PYTHON=sys.executable, MODE=mode,
                       PATH=str(case) + os.pathsep + os.environ["PATH"])
            proc = subprocess.Popen(["/bin/bash", "-c", shell], env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            result = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (case / "perl-calls").read_text().splitlines()
            return result.stdout, (case / "term").exists(), len(calls)

    def test_VI1_graceful_exit_is_reaped_without_term(self):
        output, terminated, _ = self.run_shutdown("grace")
        self.assertIn("result=0,0", output)
        self.assertFalse(terminated)

    def test_VI1_term_cooperative_exit_is_reaped(self):
        output, terminated, _ = self.run_shutdown("term")
        self.assertIn("result=0,0", output)
        self.assertTrue(terminated)

    def test_VI2_graceful_exit_skips_term_and_kill_timers(self):
        _, _, calls = self.run_shutdown("grace")
        self.assertEqual(calls, 1, "unconditional KILL phase or per-poll process launches")

    def test_VI2_kill_required_uses_at_most_one_timer_per_phase(self):
        output, terminated, calls = self.run_shutdown("kill")
        self.assertIn("result=137,0", output)
        self.assertTrue(terminated)
        self.assertLessEqual(calls, 3)

    def test_deadline_cannot_be_lost_before_builtin_wait(self):
        output, terminated, calls = self.run_shutdown("kill", delayed_wait=True)
        self.assertIn("result=137,0", output)
        self.assertTrue(terminated)
        self.assertLessEqual(calls, 3)

    def test_I3_surviving_runner_marks_drain_failed(self):
        source = self.shutdown_source()
        outcome = source[source.index('if [[ "$close_requested" -eq 1 ]] && kill -0'):]
        result = subprocess.run(["/bin/bash", "-c", 'close_requested=1; close_drain_failed=0; runner_pid=123; '
                                 'kill() { return 0; };\n' + outcome +
                                 '\nprintf "%s,%s" "$runner_rc" "$close_drain_failed"'],
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.stdout, "1,1")


class RetirementRegressions(unittest.TestCase):
    def test_III2_documentation_covers_all_removed_strengths(self):
        for name in ("CHANGELOG.md", "docs/transport-overlay.md"):
            with self.subTest(name=name):
                self.assertIn("Gemini 3.6/3.7 Flash high/medium/low", (ROOT / name).read_text())


if __name__ == "__main__":
    unittest.main()
