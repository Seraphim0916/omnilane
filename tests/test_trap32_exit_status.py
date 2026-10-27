"""Bash 3.2 EXIT traps must not turn a set -u abort into exit status 0.

macOS /bin/bash 3.2 enters an EXIT trap with $? = 0 after an unbound-variable
abort; a trap whose last command succeeds then exits 0 and a failing script
passes. Guarded scripts mark a real finish with OMNILANE_COMPLETED=1.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

import offline_env  # noqa: F401  (suite isolation)

ROOT = Path(__file__).resolve().parents[1]
BASH = "/bin/bash"
COMMON = ROOT / "scripts/lib/common.sh"

INLINE_GUARD = (
    "trap 'omnilane_rc=$?; trap - EXIT; "
    "[[ \"$omnilane_rc\" -ne 0 || \"${OMNILANE_COMPLETED:-0}\" -eq 1 ]] || omnilane_rc=1; "
    "cleanup; exit \"$omnilane_rc\"' EXIT"
)

GUARDED = (
    "scripts/configure.sh",
    "scripts/lib/goal-loop.sh",
    "scripts/provider-probe.sh",
    "scripts/runners/run-codex.sh",
    "scripts/runners/run-gemini.sh",
    "scripts/runners/run-grok.sh",
    "scripts/runners/run-openai-compat.sh",
    "scripts/runners/run-vote.sh",
    "tests/test_completion_idle_fixes.sh",
    "tests/test_foreman_inbox.sh",
    "tests/test_goal_loop.sh",
    "tests/test_release_audit_usage_doc.sh",
    "tests/test_thread_dispatch.sh",
)

# (body, expected exit status, stderr fragment)
CASES = (
    (': "$TRAP32_UNSET_VARIABLE"', None, "unbound variable"),
    ("false", 1, ""),
    ("exit 23", 23, ""),
    ("OMNILANE_COMPLETED=1", 0, ""),
    ("omnilane_mark_completed", 0, ""),
)

EXIT_TRAP = re.compile(r"^\s*trap\s+(?!-\s)(.+?)\s+EXIT\b", re.M)

# Each runner's own EXIT trap and handler run in isolation; the shell lines
# stand in for the state the runner has built when the trap is installed.
RUNNER_TRAPS = {
    "scripts/runners/run-codex.sh": ("cleanup_codex_tmp", 'CODEX_TMP=""\n'),
    "scripts/runners/run-gemini.sh": (
        "agy_cleanup_workspace_policy",
        'MODE=work WORKDIR=/nonexistent AGY_APP_ROOT=/nonexistent\n'
        'AGY_PREPARE="$TRAP32_PREPARE"\n'),
    "scripts/runners/run-grok.sh": (
        "release_grok_mcp_readiness_dir", 'GROK_MCP_READINESS_DIR="$TRAP32_SCOPE"\n'),
    "scripts/runners/run-openai-compat.sh": (
        "cleanup", 'REQUEST_FILE=/nonexistent RESPONSE_FILE=/nonexistent HEADER_FILE=/nonexistent\n'),
    "scripts/runners/run-vote.sh": ("cleanup_temp_files", "TEMP_FILES=()\n"),
}


def shell_function(text, name):
    """Return the source of NAME() { ... } as written, at any indentation."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        match = re.match(rf"^(\s*){re.escape(name)}\(\) \{{", line)
        if not match:
            continue
        if line.rstrip().endswith("}") and line.count("{") == line.count("}"):
            return line + "\n"
        for end in range(start + 1, len(lines)):
            if lines[end] == match.group(1) + "}":
                return "\n".join(lines[start:end + 1]) + "\n"
    raise AssertionError(f"shell function not found: {name}")


def runner_trap(relative):
    """The runner's EXIT trap line plus its handler, ready to run in /bin/bash."""
    handler, setup = RUNNER_TRAPS[relative]
    text = (ROOT / relative).read_text()
    traps = [line.strip() for line in text.splitlines()
             if EXIT_TRAP.match(line) and handler in line]
    if len(set(traps)) != 1:
        raise AssertionError(f"{relative}: expected one EXIT trap for {handler}: {traps}")
    track = "omnilane_track_completion\n" if "omnilane_track_completion" in text else ""
    return (f'source "{COMMON}"\nset -euo pipefail\n' + setup
            + shell_function(text, handler) + track + traps[0] + "\n")


def run_bash(script, **env):
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, **env))


@unittest.skipUnless(Path(BASH).exists(), "requires /bin/bash")
class Trap32ExitStatusTests(unittest.TestCase):
    def check_cases(self, prologue):
        for body, expected, stderr in CASES:
            with self.subTest(body=body):
                result = run_bash(prologue + body + "\n")
                if expected is None:
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn(stderr, result.stderr)

    def test_common_helper_preserves_exit_status(self):
        self.check_cases(
            f'source "{COMMON}"\nset -euo pipefail\nOMNILANE_COMPLETED=0\n'
            "trap 'omnilane_exit_trap $? true' EXIT\n")

    def test_inline_guard_preserves_exit_status(self):
        self.check_cases(
            "set -euo pipefail\ncleanup() { true; }\nomnilane_mark_completed() { OMNILANE_COMPLETED=1; }\n"
            "OMNILANE_COMPLETED=0\n"
            + INLINE_GUARD + "\n")

    def test_runner_exit_traps_preserve_exit_status(self):
        with tempfile.TemporaryDirectory() as scratch:
            prepare = Path(scratch) / "prepare"
            prepare.write_text("import sys\nsys.exit(0)\n")
            for relative in RUNNER_TRAPS:
                with self.subTest(runner=relative):
                    env = {"TRAP32_PREPARE": str(prepare),
                           "TRAP32_SCOPE": str(Path(scratch) / "scope")}
                    prologue = runner_trap(relative)
                    for body, expected, stderr in CASES:
                        with self.subTest(body=body):
                            result = run_bash(prologue + body + "\n", **env)
                            if expected is None:
                                self.assertNotEqual(result.returncode, 0, result.stderr)
                            else:
                                self.assertEqual(result.returncode, expected, result.stderr)
                            self.assertIn(stderr, result.stderr)

    def test_gemini_cleanup_failure_still_reports_125(self):
        with tempfile.TemporaryDirectory() as scratch:
            prepare = Path(scratch) / "prepare"
            prepare.write_text("import sys\nsys.exit(1)\n")
            prologue = runner_trap("scripts/runners/run-gemini.sh")
            env = {"TRAP32_PREPARE": str(prepare)}
            finished = run_bash(prologue + "omnilane_mark_completed\n", **env)
            self.assertEqual(finished.returncode, 125, finished.stderr)
            self.assertIn("cleanup/integrity check failed", finished.stderr)
            aborted = run_bash(prologue + ': "$TRAP32_UNSET_VARIABLE"\n', **env)
            self.assertNotIn(aborted.returncode, (0, 125), aborted.stderr)
            failed = run_bash(prologue + "exit 23\n", **env)
            self.assertEqual(failed.returncode, 23, failed.stderr)

    def test_cwd_lock_guard_is_opt_in(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
            env = {"OMNILANE_HOME": home}
            base = f'source "{COMMON}"\nset -euo pipefail\n'
            lock = f'acquire_cwd_lock codex "{work}"\n'
            opted = run_bash(base + "OMNILANE_COMPLETED=0\n" + lock
                             + ': "$TRAP32_UNSET_VARIABLE"\n', **env)
            self.assertNotEqual(opted.returncode, 0, opted.stderr)
            self.assertIn("unbound variable", opted.stderr)
            finished = run_bash(base + "OMNILANE_COMPLETED=0\n" + lock
                                + "OMNILANE_COMPLETED=1\n", **env)
            self.assertEqual(finished.returncode, 0, finished.stderr)
            legacy = run_bash(base + lock + "true\n", **env)
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            again = run_bash(base + lock + "printf reacquired\n",
                             OMNILANE_LOCK_TIMEOUT="2", **env)
            self.assertEqual((again.returncode, again.stdout), (0, "reacquired"), again.stderr)

    def test_every_exit_trap_in_scope_is_guarded(self):
        for relative in GUARDED:
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text()
                self.assertRegex(text, r"OMNILANE_COMPLETED=0|omnilane_track_completion")
                self.assertRegex(text, r"OMNILANE_COMPLETED=1|omnilane_mark_completed")
                traps = EXIT_TRAP.findall(text)
                self.assertTrue(traps)
                for handler in traps:
                    if "omnilane_rc" in handler:
                        self.assertIn(INLINE_GUARD, text)
                        continue
                    if "omnilane_exit_trap" in handler:
                        continue
                    self.assertRegex(handler, r"^[a-z_]+$", f"unguarded EXIT trap: {handler}")
                    self.assertIn("OMNILANE_COMPLETED", shell_function(text, handler),
                                  f"EXIT trap handler {handler} ignores the completion mark")


if __name__ == "__main__":
    unittest.main()
