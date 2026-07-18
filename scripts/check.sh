#!/usr/bin/env bash
set -uo pipefail
# omnilane self-check: run the CONTRIBUTING required checks in one command.
# Usage: check.sh [--quick] [REPO_DIR]
#   --quick    skip the two slow suite runs (python unittest, tests/run.sh)
# A SKIP (tool unavailable or target absent) is not a failure; the script exits
# non-zero only when a check actually FAILs. No -e: checks may fail and are
# tallied rather than aborting the run.

QUICK=0
REPO=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) QUICK=1; shift ;;
    -h|--help) echo "usage: check.sh [--quick] [REPO_DIR]"; exit 0 ;;
    --) shift; break ;;
    -*) echo "check.sh: unknown option: $1" >&2; exit 2 ;;
    *) if [[ -n "$REPO" ]]; then echo "check.sh: too many arguments" >&2; exit 2; fi
       REPO="$1"; shift ;;
  esac
done
if [[ $# -gt 0 ]]; then
  if [[ -n "$REPO" ]]; then echo "check.sh: too many arguments" >&2; exit 2; fi
  REPO="$1"
fi
[[ -n "$REPO" ]] || REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS_N=0; FAIL_N=0; SKIP_N=0
_pass() { PASS_N=$((PASS_N + 1)); printf 'PASS %s\n' "$1"; }
_skip() { SKIP_N=$((SKIP_N + 1)); printf 'SKIP %s (%s)\n' "$1" "$2"; }
_fail() { FAIL_N=$((FAIL_N + 1)); printf 'FAIL %s\n' "$1"; [[ -z "${2:-}" ]] || printf '  %s\n' "$2"; }

# Shell files present in the repo (CONTRIBUTING's bash -n / shellcheck set).
shell_files=()
for pattern in 'bin/omnilane' 'scripts/*.sh' 'scripts/lib/*.sh' 'scripts/runners/*.sh' 'install.sh'; do
  # shellcheck disable=SC2086  # $pattern is an intentional glob against $REPO
  for f in $REPO/$pattern; do
    [[ -f "$f" ]] && shell_files+=("$f")
  done
done

# 1) bash -n on every tracked shell file
if [[ ${#shell_files[@]} -eq 0 ]]; then
  _skip bash-syntax "no shell files found"
else
  bad=""
  for f in "${shell_files[@]}"; do
    bash -n "$f" 2>/dev/null || bad="$bad ${f##*/}"
  done
  if [[ -z "$bad" ]]; then _pass bash-syntax; else _fail bash-syntax "invalid:$bad"; fi
fi

# 2) shellcheck -S warning (skip if unavailable)
if ! command -v shellcheck >/dev/null 2>&1; then
  _skip shellcheck "not installed"
elif [[ ${#shell_files[@]} -eq 0 ]]; then
  _skip shellcheck "no shell files"
elif shellcheck -S warning "${shell_files[@]}" >/dev/null 2>&1; then
  _pass shellcheck
else
  _fail shellcheck "shellcheck -S warning reported findings"
fi

# 3) perl -c on the job-timeout supervisor
perl_file="$REPO/scripts/lib/job-timeout.pl"
if ! command -v perl >/dev/null 2>&1; then
  _skip perl-syntax "perl not installed"
elif [[ ! -f "$perl_file" ]]; then
  _skip perl-syntax "job-timeout.pl absent"
elif perl -c "$perl_file" >/dev/null 2>&1; then
  _pass perl-syntax
else
  _fail perl-syntax "perl -c failed"
fi

# 4) python compile of the UI and test modules
py_files=()
for rel in scripts/ui.py tests/test_ui.py tests/test_ci_policy.py tests/ui_browser_harness.py; do
  [[ -f "$REPO/$rel" ]] && py_files+=("$REPO/$rel")
done
if ! command -v python3 >/dev/null 2>&1; then
  _skip py-compile "python3 not installed"
elif [[ ${#py_files[@]} -eq 0 ]]; then
  _skip py-compile "no python files"
elif python3 -m py_compile "${py_files[@]}" >/dev/null 2>&1; then
  _pass py-compile
else
  _fail py-compile "py_compile failed"
fi

# 5) python unit tests (slow; --quick skips)
if [[ "$QUICK" -eq 1 ]]; then
  _skip unittest "--quick"
elif ! command -v python3 >/dev/null 2>&1; then
  _skip unittest "python3 not installed"
elif [[ ! -d "$REPO/tests" ]]; then
  _skip unittest "no tests dir"
elif ( cd "$REPO" && python3 -m unittest discover -s tests -p 'test_*.py' >/dev/null 2>&1 ); then
  _pass unittest
else
  _fail unittest "python unittest suite failed"
fi

# 6) shell test suite (slow; --quick skips)
if [[ "$QUICK" -eq 1 ]]; then
  _skip shell-suite "--quick"
elif [[ ! -f "$REPO/tests/run.sh" ]]; then
  _skip shell-suite "tests/run.sh absent"
elif bash "$REPO/tests/run.sh" >/dev/null 2>&1; then
  _pass shell-suite
else
  _fail shell-suite "bash tests/run.sh failed"
fi

# 7) effective routing table resolves
if [[ ! -f "$REPO/scripts/dispatch.sh" ]]; then
  _skip dispatch-list "dispatch.sh absent"
elif bash "$REPO/scripts/dispatch.sh" --list >/dev/null 2>&1; then
  _pass dispatch-list
else
  _fail dispatch-list "dispatch.sh --list failed"
fi

printf '\nsummary: %d passed, %d skipped, %d failed\n' "$PASS_N" "$SKIP_N" "$FAIL_N"
[[ "$FAIL_N" -eq 0 ]]
