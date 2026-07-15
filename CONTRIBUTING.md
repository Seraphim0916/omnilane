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
python3 -m py_compile scripts/ui.py tests/test_ui.py
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/run.sh
bash scripts/dispatch.sh --list
```

If a dependency such as ShellCheck or a real browser is unavailable, say so in
the pull request instead of treating the missing check as passed.

## Pull requests

Explain the user-visible problem, the chosen boundary, test evidence, risks,
and rollback. Keep unrelated refactors separate. Claims about current model
capabilities, pricing, CLI flags, or provider behavior need dated primary-source
evidence because those details change frequently.

Security issues follow [SECURITY.md](SECURITY.md), not the normal public issue
workflow.
