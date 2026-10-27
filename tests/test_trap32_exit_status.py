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
                    body = re.search(rf"^{handler}\(\) \{{\n(.*?)^\}}", text, re.M | re.S)
                    self.assertIsNotNone(body, f"EXIT trap handler not found: {handler}")
                    self.assertIn("OMNILANE_COMPLETED", body.group(1),
                                  f"EXIT trap handler {handler} ignores the completion mark")


if __name__ == "__main__":
    unittest.main()
