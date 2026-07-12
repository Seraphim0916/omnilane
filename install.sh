#!/usr/bin/env bash
set -euo pipefail
# omnilane installer — wires the skill into the CLIs found on this machine.
# Conservative by design: symlinks + printed commands only, no config edits,
# no hooks. Run from a git checkout pinned to a tag you have reviewed.
#
# Usage: ./install.sh [--uninstall]

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$REPO/skills/omnilane"
UNINSTALL="${1:-}"

link_skill() { # $1 = target skills dir
  local dst="$1/omnilane"
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    [[ -L "$dst" ]] && rm "$dst" && echo "removed $dst"
    return 0
  fi
  mkdir -p "$1"
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    echo "skip $dst (exists and is not a symlink — resolve manually)"; return 0
  fi
  ln -sfn "$SKILL_SRC" "$dst"
  echo "linked $dst -> $SKILL_SRC"
}

found_any=0
if command -v claude >/dev/null 2>&1; then
  found_any=1; echo "[claude] found"
  link_skill "$HOME/.claude/skills"
  echo "  (optional plugin shell: add this repo as a Claude Code plugin for /route commands)"
fi
if command -v codex >/dev/null 2>&1; then
  found_any=1; echo "[codex] found"
  link_skill "$HOME/.codex/skills"
fi
if command -v grok >/dev/null 2>&1; then
  found_any=1; echo "[grok] found — run manually:"
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    echo "  grok plugin uninstall omnilane"
  else
    echo "  grok plugin install \"$REPO\" --trust"
  fi
fi
if command -v agy >/dev/null 2>&1; then
  found_any=1; echo "[antigravity] found — run manually:"
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    echo "  agy plugin uninstall omnilane"
  else
    echo "  agy plugin validate \"$REPO\" && agy plugin install \"$REPO\""
  fi
fi
[[ "$found_any" == 1 ]] || { echo "no supported CLI (claude/codex/grok/agy) on PATH"; exit 1; }

if [[ "$UNINSTALL" != "--uninstall" ]]; then
  echo
  echo "Per-machine overrides live in ~/.omnilane/ (never committed):"
  echo "  routing.local.yaml — override any lane (see routing.local.yaml.example)"
  echo "  local.sh           — binaries/env for the runners (see local.sh.example)"
fi
