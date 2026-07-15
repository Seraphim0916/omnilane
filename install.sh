#!/usr/bin/env bash
set -euo pipefail
# omnilane installer — wires the skill into the CLIs found on this machine.
# Conservative by design: symlinks + marked instruction-file blocks only;
# everything is reversed by --uninstall. Run from a checkout you have reviewed.
#
# Usage: ./install.sh [--uninstall]
# Env:   OMNILANE_LANG=en|zh-TW|zh-CN|ja|ko   force interface language
#        OMNILANE_HOOKS=ask|none|all|claude,codex,...   routing-reminder policy
#                                                       (default ask on a tty)

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$REPO/scripts/lib/i18n.sh"
SKILL_SRC="$REPO/skills/omnilane"
UNINSTALL="${1:-}"

HOOK_SRC="$REPO/hooks/routing-instruction.md"
HOOK_START='<!-- omnilane-routing:start -->'
HOOK_END='<!-- omnilane-routing:end -->'
HOOKS_MODE="${OMNILANE_HOOKS:-ask}"

instruction_file() { # vendor -> the CLI's global instruction file (may vary by CLI version)
  case "$1" in
    claude) echo "$HOME/.claude/CLAUDE.md" ;;
    codex)  echo "$HOME/.codex/AGENTS.md" ;;
    grok)   echo "$HOME/.grok/Agents.md" ;;
    agy)    echo "$HOME/.gemini/GEMINI.md" ;;
    *)      echo "" ;;
  esac
}

remove_hook() { # file [quiet]
  local f="$1" tmp starts ends start_line end_line
  [[ -f "$f" ]] || return 0
  starts="$(grep -xcF "$HOOK_START" "$f" || true)"
  ends="$(grep -xcF "$HOOK_END" "$f" || true)"
  [[ "$starts" -eq 0 && "$ends" -eq 0 ]] && return 0
  if [[ "$starts" -ne 1 || "$ends" -ne 1 ]]; then
    echo "omnilane: warning: malformed routing reminder markers in $f (start=$starts end=$ends); file unchanged" >&2
    return 2
  fi
  start_line="$(grep -nxF "$HOOK_START" "$f" | cut -d: -f1)"
  end_line="$(grep -nxF "$HOOK_END" "$f" | cut -d: -f1)"
  if [[ "$start_line" -ge "$end_line" ]]; then
    echo "omnilane: warning: routing reminder markers out of order in $f; file unchanged" >&2
    return 2
  fi
  tmp="$(mktemp)"
  awk -v s="$HOOK_START" -v e="$HOOK_END" \
    '$0 == s {
       # A non-empty preceding record means install supplied the line ending;
       # restore that original last line without adding one.
       if (pending && previous != "") printf "%s", previous
       pending = 0; skip = 1; next
     }
     $0 == e {skip = 0; next}
     skip {next}
     {if (pending) print previous; previous = $0; pending = 1}
     END {if (pending) print previous}' "$f" > "$tmp"
  # cat-over instead of mv: keeps the inode, so instruction files that are
  # symlinks (common in synced setups) stay symlinks.
  cat "$tmp" > "$f"
  rm "$tmp"
  [[ "${2:-}" == "quiet" ]] || echo "$(msg hook_removed) $f"
}

install_hook() { # vendor
  local f; f="$(instruction_file "$1")"
  [[ -n "$f" ]] || return 0
  mkdir -p "$(dirname "$f")"
  remove_hook "$f" quiet   # refresh: old block out, current block in
  { [[ -s "$f" ]] && echo; cat "$HOOK_SRC"; } >> "$f"
  echo "$(msg hook_written) $f"
}

offer_hook() { # vendor
  local v="$1" ans f
  case "$HOOKS_MODE" in
    none) return 0 ;;
    all)  install_hook "$v"; return 0 ;;
    ask)  ;;
    *)    [[ ",$HOOKS_MODE," == *",$v,"* ]] && install_hook "$v"; return 0 ;;
  esac
  [[ -t 0 && -t 1 ]] || return 0
  f="$(instruction_file "$v")"
  read -rp "$(msgf hook_prompt "$f")" ans || ans=""
  case "$ans" in y|Y|yes|YES) install_hook "$v" ;; esac
}

link_skill() { # $1 = target skills dir
  local dst="$1/omnilane"
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    [[ -L "$dst" ]] && rm "$dst" && echo "$(msg removed) $dst"
    return 0
  fi
  mkdir -p "$1"
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    msgf skip_exists "$dst"; echo; return 0
  fi
  ln -sfn "$SKILL_SRC" "$dst"
  echo "$(msg linked) $dst -> $SKILL_SRC"
}

if [[ "$UNINSTALL" == "--uninstall" ]]; then
  for v in claude codex grok agy; do remove_hook "$(instruction_file "$v")"; done
fi

found=()
if command -v claude >/dev/null 2>&1; then
  found+=(claude); echo "[claude] $(msg found)"
  link_skill "$HOME/.claude/skills"
  echo "  $(msg plugin_hint)"
fi
if command -v codex >/dev/null 2>&1; then
  found+=(codex); echo "[codex] $(msg found)"
  link_skill "$HOME/.codex/skills"
fi
if command -v grok >/dev/null 2>&1; then
  found+=(grok); echo "[grok] $(msg run_manually)"
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    echo "  grok plugin uninstall omnilane"
  else
    echo "  grok plugin install \"$REPO\" --trust"
  fi
fi
if command -v agy >/dev/null 2>&1; then
  found+=(agy); echo "[antigravity] $(msg run_manually)"
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    echo "  agy plugin uninstall omnilane"
  else
    echo "  agy plugin validate \"$REPO\" && agy plugin install \"$REPO\""
  fi
fi
# Global wrapper so commands work from any directory: omnilane list|route|jobs|configure
BIN_DST="$HOME/.local/bin/omnilane"
if [[ "$UNINSTALL" == "--uninstall" ]]; then
  [[ -L "$BIN_DST" ]] && rm "$BIN_DST" && echo "$(msg removed) $BIN_DST"
else
  mkdir -p "$HOME/.local/bin"
  if [[ -e "$BIN_DST" && ! -L "$BIN_DST" ]]; then
    msgf skip_exists "$BIN_DST"; echo
  else
    ln -sfn "$REPO/bin/omnilane" "$BIN_DST"
    echo "$(msg linked) $BIN_DST  $(msg path_hint)"
  fi
fi

if [[ ${#found[@]} -eq 0 ]]; then
  # Uninstall is cleanup, not capability detection. A user may remove their
  # provider CLIs before removing omnilane; completed cleanup must still be a
  # successful operation.
  [[ "$UNINSTALL" == "--uninstall" ]] && exit 0
  echo "$(msg no_cli)"
  exit 1
fi

if [[ "$UNINSTALL" != "--uninstall" ]]; then
  echo
  echo "$(msg hook_section)"
  for v in "${found[@]}"; do offer_hook "$v"; done
  echo
  echo "$(msg overrides_header)"
  echo "  $(msg overrides_routing)"
  echo "  $(msg overrides_local)"
  echo
  echo "$(msg effective)"
  bash "$REPO/scripts/dispatch.sh" --list
  if [[ -t 0 && -t 1 ]]; then
    echo
    read -rp "$(msg customize_prompt)" ans || ans=""
    case "$ans" in y|Y|yes|YES) bash "$REPO/scripts/configure.sh" ;; esac
  fi
fi
