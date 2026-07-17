#!/usr/bin/env bash
set -euo pipefail
# omnilane installer — wires the skill into the CLIs found on this machine.
# Conservative by design: symlinks + marked instruction-file blocks only;
# everything is reversed by --uninstall. Run from a checkout you have reviewed.
#
# Usage: ./install.sh [--uninstall] [--dry-run] | ./install.sh --check
# Env:   OMNILANE_LANG=en|zh-TW|zh-CN|ja|ko   force interface language
#        OMNILANE_HOOKS=ask|none|all|claude,codex,...   routing-reminder policy
#                                                       (default ask on a tty)

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
usage() {
  echo "usage: ./install.sh [--uninstall] [--dry-run] | ./install.sh --check" "${1:-}" >&2
}

UNINSTALL=""
DRY_RUN=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --uninstall)
      [[ -z "$UNINSTALL" ]] || { usage "(duplicate argument)"; exit 2; }
      UNINSTALL="--uninstall" ;;
    --dry-run)
      [[ "$DRY_RUN" -eq 0 ]] || { usage "(duplicate argument)"; exit 2; }
      DRY_RUN=1 ;;
    --check)
      [[ "$CHECK" -eq 0 ]] || { usage "(duplicate argument)"; exit 2; }
      CHECK=1 ;;
    --help|-h)
      [[ $# -eq 1 ]] || { usage "(unexpected extra arguments)"; exit 2; }
      usage; exit 0 ;;
    *) usage "(unknown argument)"; exit 2 ;;
  esac
done
if [[ "$CHECK" -eq 1 && ( -n "$UNINSTALL" || "$DRY_RUN" -eq 1 ) ]]; then
  usage "(--check cannot be combined with another mode)"
  exit 2
fi

source "$REPO/scripts/lib/i18n.sh"
SKILL_SRC="$REPO/skills/omnilane"

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
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'would remove routing reminder from %s\n' "$f"
    return 0
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
  if [[ "$DRY_RUN" -eq 1 ]]; then
    remove_hook "$f" quiet
    printf 'would write routing reminder to %s\n' "$f"
    return 0
  fi
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

owned_symlink() { # path, exact target written by this checkout
  [[ -L "$1" ]] && [[ "$(readlink "$1")" == "$2" ]]
}

preserve_foreign_symlink() {
  msgf foreign_link "$1" >&2; echo >&2
}

safe_owned_parent() { # final path; nearest existing parent must resolve below HOME
  local path="$1" parent probe next resolved_home resolved_probe
  parent="$(dirname "$path")"
  case "$parent" in
    "$HOME"|"$HOME"/*) ;;
    *) return 1 ;;
  esac
  probe="$parent"
  while [[ ! -e "$probe" && ! -L "$probe" ]]; do
    next="$(dirname "$probe")"
    [[ "$next" != "$probe" ]] || return 1
    probe="$next"
  done
  [[ -d "$probe" ]] || return 1
  resolved_home="$(cd "$HOME" 2>/dev/null && pwd -P)" || return 1
  resolved_probe="$(cd "$probe" 2>/dev/null && pwd -P)" || return 1
  case "$resolved_probe" in
    "$resolved_home"|"$resolved_home"/*) return 0 ;;
    *) return 1 ;;
  esac
}

reject_unsafe_parent() {
  printf 'omnilane: unsafe parent path below HOME: %s\n' "$(dirname "$1")" >&2
  return 1
}

link_skill() { # $1 = target skills dir
  local dst="$1/omnilane"
  safe_owned_parent "$dst" || {
    reject_unsafe_parent "$dst"
    return 1
  }
  if [[ "$UNINSTALL" == "--uninstall" ]]; then
    if owned_symlink "$dst" "$SKILL_SRC"; then
      if [[ "$DRY_RUN" -eq 1 ]]; then
        printf 'would remove owned link %s\n' "$dst"
      else
        rm "$dst"; echo "$(msg removed) $dst"
      fi
    elif [[ -L "$dst" ]]; then
      preserve_foreign_symlink "$dst"
    fi
    return 0
  fi
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    msgf skip_exists "$dst"; echo; return 0
  fi
  if [[ -L "$dst" ]] && ! owned_symlink "$dst" "$SKILL_SRC"; then
    preserve_foreign_symlink "$dst"; return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'would link %s -> %s\n' "$dst" "$SKILL_SRC"
    return 0
  fi
  mkdir -p "$1"
  ln -sfn "$SKILL_SRC" "$dst"
  echo "$(msg linked) $dst -> $SKILL_SRC"
}

CHECK_FAILURES=0
check_link() { # label, path, target
  local label="$1" path="$2" target="$3"
  if ! safe_owned_parent "$path"; then
    printf 'DRIFT %s %s (unsafe parent path)\n' "$label" "$path"
    CHECK_FAILURES=$((CHECK_FAILURES + 1))
  elif owned_symlink "$path" "$target"; then
    printf 'PASS %s %s\n' "$label" "$path"
  elif [[ ! -e "$path" && ! -L "$path" ]]; then
    printf 'MISSING %s %s\n' "$label" "$path"
    CHECK_FAILURES=$((CHECK_FAILURES + 1))
  else
    printf 'DRIFT %s %s (not an owned link)\n' "$label" "$path"
    CHECK_FAILURES=$((CHECK_FAILURES + 1))
  fi
}

hook_selected() {
  local vendor="$1"
  case "$HOOKS_MODE" in
    none|ask) return 1 ;;
    all) return 0 ;;
    *) [[ ",$HOOKS_MODE," == *",$vendor,"* ]] ;;
  esac
}

check_hook() {
  local vendor="$1" f starts ends actual expected
  f="$(instruction_file "$vendor")"
  if [[ ! -f "$f" ]]; then
    printf 'MISSING %s-hook %s\n' "$vendor" "$f"
    CHECK_FAILURES=$((CHECK_FAILURES + 1))
    return
  fi
  starts="$(grep -xcF "$HOOK_START" "$f" || true)"
  ends="$(grep -xcF "$HOOK_END" "$f" || true)"
  if [[ "$starts" -eq 1 && "$ends" -eq 1 ]]; then
    actual="$(awk -v s="$HOOK_START" -v e="$HOOK_END" \
      '$0 == s {inside=1} inside {print} $0 == e {exit}' "$f")"
    expected="$(cat "$HOOK_SRC")"
    if [[ "$actual" == "$expected" ]]; then
      printf 'PASS %s-hook %s\n' "$vendor" "$f"
      return
    fi
  fi
  printf 'DRIFT %s-hook %s (routing reminder differs)\n' "$vendor" "$f"
  CHECK_FAILURES=$((CHECK_FAILURES + 1))
}

if [[ "$CHECK" -eq 1 ]]; then
  check_link wrapper "$HOME/.local/bin/omnilane" "$REPO/bin/omnilane"
  if command -v claude >/dev/null 2>&1; then
    check_link claude-skill "$HOME/.claude/skills/omnilane" "$SKILL_SRC"
    hook_selected claude && check_hook claude
  fi
  if command -v codex >/dev/null 2>&1; then
    check_link codex-skill "$HOME/.codex/skills/omnilane" "$SKILL_SRC"
    hook_selected codex && check_hook codex
  fi
  if command -v grok >/dev/null 2>&1 && hook_selected grok; then check_hook grok; fi
  if command -v agy >/dev/null 2>&1 && hook_selected agy; then check_hook agy; fi
  [[ "$CHECK_FAILURES" -eq 0 ]]
  exit $?
fi

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
safe_owned_parent "$BIN_DST" || reject_unsafe_parent "$BIN_DST"
if [[ "$UNINSTALL" == "--uninstall" ]]; then
  if owned_symlink "$BIN_DST" "$REPO/bin/omnilane"; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf 'would remove owned link %s\n' "$BIN_DST"
    else
      rm "$BIN_DST"; echo "$(msg removed) $BIN_DST"
    fi
  elif [[ -L "$BIN_DST" ]]; then
    preserve_foreign_symlink "$BIN_DST"
  fi
else
  if [[ -e "$BIN_DST" && ! -L "$BIN_DST" ]]; then
    msgf skip_exists "$BIN_DST"; echo
  elif [[ -L "$BIN_DST" ]] && ! owned_symlink "$BIN_DST" "$REPO/bin/omnilane"; then
    preserve_foreign_symlink "$BIN_DST"
  elif [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'would link %s -> %s\n' "$BIN_DST" "$REPO/bin/omnilane"
  else
    mkdir -p "$HOME/.local/bin"
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
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would inspect effective routing after installation"
  else
    bash "$REPO/scripts/dispatch.sh" --list
  fi
  if [[ "$DRY_RUN" -eq 0 && -t 0 && -t 1 ]]; then
    echo
    read -rp "$(msg customize_prompt)" ans || ans=""
    case "$ans" in y|Y|yes|YES) bash "$REPO/scripts/configure.sh" ;; esac
  fi
fi
