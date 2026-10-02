# Contributing to Omnilane

Thank you for improving Omnilane. Keep changes small, reversible, and usable on
the stock macOS and Linux environments the project supports.

## Compatibility boundaries

- Shell code must run on Bash 3.2, which is still the system Bash on macOS.
- The optional Live UI must remain compatible with Python 3.9 or newer and use
  only the standard library at runtime.
- Core routing must not require Node.js, a package manager, or an API key.
- Never commit provider credentials, cookies, local routing overlays, prompts,
  model outputs, or files from `~/.omnilane`.
- `advise` remains the default mode. Do not broaden write access, network
  exposure, fallback behavior, or provider spending without an explicit design.

## Development workflow

1. Start from the latest `main` and create a focused branch.
2. Add a failing test for behavior changes before implementing the fix.
3. Update all five READMEs when public CLI behavior or user guidance changes.
4. Keep machine-specific paths and binaries in `~/.omnilane/local.sh`, never in
   tracked defaults.
5. Include the real runtime evidence appropriate to the change: CLI exit code,
   background job state, loopback API response, or browser behavior.

## Required checks

Run the closest local equivalent of CI:

```bash
for file in bin/omnilane scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh install.sh; do
  bash -n "$file"
done
shellcheck -S warning bin/omnilane scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh install.sh
perl -c scripts/lib/job-timeout.pl
python3 -m py_compile scripts/ui.py tests/test_ui.py tests/test_ci_policy.py tests/ui_browser_harness.py
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/run.sh
bash scripts/dispatch.sh --list
```

Or run the whole set in one command with `bash scripts/check.sh` (append
`--quick` to skip the two slow suite runs — `unittest` and `tests/run.sh` — for a
fast pre-commit pass). A `SKIP` (an unavailable tool such as ShellCheck, or an
absent target) is not a failure; it exits non-zero only on a real `FAIL`.

The browser CI job installs `tests/requirements-browser.txt`, installs bundled
Chromium with Playwright, sets `OMNILANE_TEST_USE_PLAYWRIGHT_BROWSER=1`, and runs
`tests.test_ui.FrontendBrowserBehaviorTests`. If a dependency such as
ShellCheck or a real browser is unavailable locally, say so in the pull request
instead of treating the missing check as passed.

Shell test entrypoints run through `tests/offline_env.py`: a temporary HOME and
an allowlisted utility PATH keep installed provider CLIs, credentials and local
configuration outside the fixtures. A fixture must explicitly supply its fake
provider binary. Unmocked `curl`/`wget` calls fail the outer run even if a negative
test ignores their exit code. Use `$OMNILANE_TEST_UTIL_PATH` when a fixture needs
to replace PATH; do not restore host/system PATH directories. This is fixture
isolation, not an operating-system network sandbox: direct HTTP code still needs
an explicit fake in its own test.

For CI parity, use ShellCheck 0.11.0 and run `bash scripts/check.sh`, `python3.9 -m unittest discover -s tests -p 'test_*.py'`, and `python3.14 -m unittest discover -s tests -p 'test_*.py'`; unavailable checks are not passes.
Also run `! grep -ri 'omni''route' --exclude-dir=.git --exclude-dir=.github .` and the `routing table smoke` and offline `Strict doctor acceptance` command blocks in `.github/workflows/ci.yml` (set its runner temporary-directory/run-ID variables locally).
For browser parity, run `python -m pip install -r tests/requirements-browser.txt`, `python -m playwright install chromium`, then `OMNILANE_TEST_USE_PLAYWRIGHT_BROWSER=1 python -c "from tests.ui_browser_harness import browser_available; assert browser_available"` and `OMNILANE_TEST_USE_PLAYWRIGHT_BROWSER=1 python -m unittest tests.test_ui.FrontendBrowserBehaviorTests` with Python 3.12.

## Pull requests

Explain the user-visible problem, the chosen boundary, test evidence, risks,
and rollback. Keep unrelated refactors separate. Claims about current model
capabilities, pricing, CLI flags, or provider behavior need dated primary-source
evidence because those details change frequently.

Security issues follow [SECURITY.md](SECURITY.md), not the normal public issue
workflow.
