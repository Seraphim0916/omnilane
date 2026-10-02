#!/usr/bin/env python3
"""Run trusted repository tests with no inherited provider tools or configuration.

This is test-fixture isolation, not an OS/network sandbox. Explicit fixtures may
prepend their fake tools. Unmocked curl/wget calls fail the outer run even if a
negative test swallows the command's status.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


# Only these utilities are discoverable. Never add provider CLIs or a general
# package launcher here; those must be supplied explicitly by a fixture.
UTILITIES = (
    "awk", "basename", "bash", "cat", "chmod", "cmp", "comm", "cp", "cut",
    "date", "dd", "diff", "dirname", "env", "expr", "false", "file", "find",
    "fish", "git", "grep", "gtimeout", "head", "hostname", "iconv", "id", "kill", "ln",
    "ls", "mkdir", "mkfifo", "mktemp", "mv", "node", "od", "perl", "pgrep",
    "pkill", "ps", "pwd", "readlink", "realpath", "rm", "rmdir", "sed", "seq", "sh", "shellcheck",
    "sha256sum", "shasum", "sleep", "sort", "stat", "tail", "tee", "test",
    "timeout", "touch", "tr", "true", "uname", "uniq", "wc", "which", "xargs",
    "zsh",
)

# Prefer OS utilities over caller PATH wrappers that may depend on the caller's
# real HOME. Homebrew/local tools remain available where the OS supplies none;
# the inherited path is only a final fallback for allowlisted optional tools.
SYSTEM_UTILITY_PATH = os.pathsep.join(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))
LOCAL_UTILITY_PATH = os.pathsep.join(("/opt/homebrew/bin", "/usr/local/bin"))


def resolve_utility(name: str, search_path: str) -> str | None:
    for path in (SYSTEM_UTILITY_PATH, LOCAL_UTILITY_PATH, search_path):
        source = shutil.which(name, path=path)
        if source:
            return source
    return None


def isolated_environment(root: Path, search_path: str) -> tuple[dict[str, str], Path]:
    utilities = root / "utilities"
    home = root / "home"
    temporary = root / "tmp"
    for directory in (utilities, home / ".omnilane", temporary):
        directory.mkdir(parents=True)
    for name in UTILITIES:
        source = resolve_utility(name, search_path)
        if source:
            (utilities / name).symlink_to(Path(source).resolve())
    # Keep the running interpreter, including its installed standard/runtime
    # dependencies, rather than accidentally selecting another host Python.
    for name in ("python", "python3"):
        (utilities / name).symlink_to(Path(sys.executable).resolve())
    violations = root / "unexpected-network-command.log"
    for name in ("curl", "wget"):
        guard = utilities / name
        guard.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' {shlex.quote(name)} >> {shlex.quote(str(violations))}\n"
            f"printf '%s\\n' 'offline test: unmocked {name} refused' >&2\n"
            "exit 97\n", encoding="utf-8")
        guard.chmod(0o755)
    # Deliberately do not copy os.environ: *_BIN, API keys, local configuration,
    # proxy settings, startup hooks and language/Git injection stay outside.
    environment = {
        "PATH": str(utilities), "HOME": str(home), "TMPDIR": str(temporary),
        "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "dumb",
        "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "OMNILANE_TEST_UTIL_PATH": str(utilities),
        "OMNILANE_AA_OPERATOR_ASSERTED_HUMAN": "1",
    }
    return environment, violations


def fixture_environment_with_isolated_tools(test_case, environment=None) -> dict[str, str]:
    """Keep a fixture's environment, replacing only its inherited tool PATH.

    Legacy fixtures deliberately control HOME and other inputs themselves.
    Give them the same utility resolution, Python pin and network guards as the
    launcher, with test-owned lifetime and cleanup assertions. Explicit fake
    vendor directories may still be prepended by the fixture afterwards.
    """
    result = dict(os.environ if environment is None else environment)
    temporary = tempfile.TemporaryDirectory(prefix="omnilane-fixture-tools-")
    test_case.addCleanup(temporary.cleanup)
    isolated, violations = isolated_environment(Path(temporary.name), result.get("PATH", ""))
    test_case.addCleanup(
        lambda: test_case.assertFalse(violations.exists(), "unmocked fixture network command")
    )
    result["PATH"] = isolated["PATH"]
    return result


def main(argv: list[str] | None = None) -> int:
    command = sys.argv[1:] if argv is None else argv
    if not command:
        print("usage: python3 -I tests/offline_env.py COMMAND [ARG ...]", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="omnilane-offline-") as directory:
        environment, violations = isolated_environment(Path(directory), os.environ.get("PATH", ""))
        result = subprocess.run(command, env=environment, check=False)
        if violations.exists():
            names = ", ".join(sorted(set(violations.read_text().splitlines())))
            print(f"offline test: unmocked network commands were refused: {names}", file=sys.stderr)
            return 1
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
else:
    # Import-time bootstrap works for plain discovery and direct test files.
    from home_isolation import activate
    SUITE_ROOT = activate()
