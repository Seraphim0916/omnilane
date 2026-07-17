#!/usr/bin/env bash
set -euo pipefail
# Offline, read-only release gate. It never creates tags, archives, or releases.

SELF="${BASH_SOURCE[0]}"
while [[ -L "$SELF" ]]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd -P)"

usage() {
  echo "usage: release-audit.sh [--target VERSION] [--allow-dirty] [--require-tag] [--manifest] [--json]" >&2
  exit 2
}

target=""
allow_dirty=0
require_tag=0
show_manifest=0
json_output=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || usage
      target="$2"; shift 2 ;;
    --allow-dirty) allow_dirty=1; shift ;;
    --require-tag) require_tag=1; shift ;;
    --manifest) show_manifest=1; shift ;;
    --json) json_output=1; shift ;;
    *) usage ;;
  esac
done

findings=0
tracked=0
manifest_sha=""
archive_sha=""
finding_codes=()
pass_codes=()
warning_codes=()
manifest_entries=()
fail() {
  finding_codes+=("$1")
  [[ "$json_output" -eq 1 ]] || printf 'FAIL %s\n' "$1"
  findings=$((findings + 1))
}
pass() {
  pass_codes+=("$1")
  [[ "$json_output" -eq 1 ]] || printf 'PASS %s\n' "$1"
}
warn() {
  warning_codes+=("$1")
  [[ "$json_output" -eq 1 ]] || printf 'WARN %s\n' "$1"
}

json_escape() {
  local value="$1" out="" ch code escaped i
  for ((i = 0; i < ${#value}; i++)); do
    ch="${value:i:1}"
    case "$ch" in
      '"') out="$out\\\"" ;;
      '\\') out="$out\\\\" ;;
      $'\b') out="$out\\b" ;;
      $'\f') out="$out\\f" ;;
      $'\n') out="$out\\n" ;;
      $'\r') out="$out\\r" ;;
      $'\t') out="$out\\t" ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'$ch"
        if [[ "$code" -ge 0 && "$code" -lt 32 ]]; then
          printf -v escaped '\\u%04x' "$code"
          out="$out$escaped"
        else
          out="$out$ch"
        fi ;;
    esac
  done
  printf '%s' "$out"
}

render_json() {
  local status="$1" index value
  printf '{"schema_version":1,"command":"release-audit","target":'
  if [[ -n "$target" ]]; then printf '"%s"' "$(json_escape "$target")"; else printf 'null'; fi
  printf ',"status":"%s","findings":[' "$status"
  for ((index = 0; index < ${#finding_codes[@]}; index++)); do
    [[ "$index" -eq 0 ]] || printf ','
    printf '"%s"' "$(json_escape "${finding_codes[$index]}")"
  done
  printf '],"warnings":['
  for ((index = 0; index < ${#warning_codes[@]}; index++)); do
    [[ "$index" -eq 0 ]] || printf ','
    printf '"%s"' "$(json_escape "${warning_codes[$index]}")"
  done
  printf '],"passes":['
  for ((index = 0; index < ${#pass_codes[@]}; index++)); do
    [[ "$index" -eq 0 ]] || printf ','
    printf '"%s"' "$(json_escape "${pass_codes[$index]}")"
  done
  printf '],"tracked":%s,"manifest_sha256":' "$tracked"
  if [[ "$manifest_sha" =~ ^[0-9a-f]{64}$ ]]; then printf '"%s"' "$manifest_sha"; else printf 'null'; fi
  printf ',"archive_sha256":'
  if [[ "$archive_sha" =~ ^[0-9a-f]{64}$ ]]; then printf '"%s"' "$archive_sha"; else printf 'null'; fi
  [[ "$require_tag" -eq 1 ]] && value=true || value=false
  printf ',"tag_required":%s,"manifest":' "$value"
  if [[ "$show_manifest" -eq 1 ]]; then
    printf '['
    for ((index = 0; index < ${#manifest_entries[@]}; index++)); do
      [[ "$index" -eq 0 ]] || printf ','
      printf '"%s"' "$(json_escape "${manifest_entries[$index]}")"
    done
    printf ']'
  else
    printf 'null'
  fi
  printf '}\n'
}

if [[ -z "$target" ]]; then
  if [[ -f "$ROOT/VERSION" && ! -L "$ROOT/VERSION" ]]; then
    target="$(<"$ROOT/VERSION")"
  else
    fail version-file-unavailable
    if [[ "$json_output" -eq 1 ]]; then
      render_json FAIL
    else
      echo "release-audit: FAIL target=unknown findings=1"
    fi
    exit 1
  fi
fi
[[ "$target" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || usage
escaped_target="${target//./\\.}"

sha256_stdin() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    return 1
  fi
}

command -v git >/dev/null 2>&1 || {
  fail git-unavailable
  if [[ "$json_output" -eq 1 ]]; then render_json FAIL;
  else printf 'release-audit: FAIL target=%s findings=%s\n' "$target" "$findings"; fi
  exit 1
}

git_root="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$git_root" || "$(cd "$git_root" 2>/dev/null && pwd -P)" != "$ROOT" ]]; then
  fail repository-boundary
  if [[ "$json_output" -eq 1 ]]; then render_json FAIL;
  else printf 'release-audit: FAIL target=%s findings=%s\n' "$target" "$findings"; fi
  exit 1
fi
pass repository-boundary

dirty="$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)"
if [[ -n "$dirty" ]]; then
  if [[ "$allow_dirty" -eq 1 ]]; then
    warn dirty-worktree-allowed
  else
    fail dirty-worktree
  fi
else
  pass clean-worktree
fi

version=""
if [[ -f "$ROOT/VERSION" && ! -L "$ROOT/VERSION" ]]; then
  version="$(<"$ROOT/VERSION")"
fi
if [[ "$version" == "$target" ]]; then
  pass version-file
else
  fail version-mismatch
fi

wrapper_version="$(/bin/bash "$ROOT/bin/omnilane" version 2>/dev/null || true)"
if [[ "$wrapper_version" == "omnilane $target" ]]; then
  pass wrapper-version
else
  fail wrapper-version-mismatch
fi

if grep -Eq "^## \\[$escaped_target\\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" "$ROOT/CHANGELOG.md"; then
  pass changelog-release
else
  fail missing-changelog-release
fi
if grep -Fq "[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v$target...HEAD" \
  "$ROOT/CHANGELOG.md"; then
  pass changelog-unreleased-link
else
  fail changelog-unreleased-link
fi
if grep -Eq "^\\[$escaped_target\\]: https://github.com/Seraphim0916/omnilane/(compare/.+\\.\\.\\.v$escaped_target|releases/tag/v$escaped_target)$" \
  "$ROOT/CHANGELOG.md"; then
  pass changelog-release-link
else
  fail changelog-release-link
fi

required=(
  VERSION LICENSE CHANGELOG.md README.md README.zh-TW.md README.zh-CN.md
  README.ja.md README.ko.md install.sh routing.yaml
  bin/omnilane scripts/dispatch.sh scripts/jobs.sh scripts/doctor.sh
  scripts/ui.py scripts/release-audit.sh skills/omnilane/SKILL.md
)
for path in "${required[@]}"; do
  if git -C "$ROOT" ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    pass "tracked-$path"
  else
    fail "missing-required-$path"
  fi
done

for path in install.sh bin/omnilane scripts/dispatch.sh scripts/jobs.sh \
  scripts/doctor.sh scripts/ui.py scripts/release-audit.sh scripts/lib/job-timeout.pl \
  scripts/lib/job-worker.sh scripts/runners/run-claude.sh \
  scripts/runners/run-codex.sh scripts/runners/run-exec.sh \
  scripts/runners/run-gemini.sh scripts/runners/run-grok.sh \
  scripts/runners/run-vote.sh; do
  mode="$(git -C "$ROOT" ls-files -s -- "$path" | awk 'NR == 1 {print $1}')"
  if [[ "$mode" == "100755" ]]; then
    pass "executable-$path"
  else
    fail "nonexecutable-$path"
  fi
done

forbidden=0
symlinks=0
unsafe_names=0
while IFS= read -r -d '' path; do
  case "$path" in
    .DS_Store|*/.DS_Store|.env|.env.*|*/.env|*/.env.*|*/__pycache__/*|*.pyc|*/node_modules/*|\
    *.pem|*.key|*.p12|*.pfx|*/id_rsa|*/id_ed25519|*/credentials.json|*/secrets.*)
      forbidden=$((forbidden + 1)) ;;
  esac
  if [[ "$path" == *$'\n'* || "$path" == *$'\r'* || "$path" == *$'\t'* ]]; then
    unsafe_names=$((unsafe_names + 1))
  fi
done < <(git -C "$ROOT" ls-files -z)
while IFS= read -r entry; do
  [[ "$entry" == 120000\ * ]] && symlinks=$((symlinks + 1))
done < <(git -C "$ROOT" ls-files -s)
[[ "$forbidden" -eq 0 ]] && pass no-forbidden-tracked-paths || fail forbidden-tracked-paths
[[ "$unsafe_names" -eq 0 ]] && pass safe-tracked-path-names || fail unsafe-tracked-path-names
[[ "$symlinks" -eq 0 ]] && pass no-tracked-symlinks || fail tracked-symlinks
if git -C "$ROOT" grep -I -q -E -- \
  '-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----' -- . 2>/dev/null; then
  fail private-key-material
else
  pass no-private-key-material
fi

if grep -Fq 'install.sh --uninstall' "$ROOT/README.md"; then
  pass rollback-documented
else
  fail rollback-undocumented
fi

tracked="$(git -C "$ROOT" ls-files | wc -l | tr -d '[:space:]')"
manifest_sha="$(git -C "$ROOT" ls-files -s | sha256_stdin 2>/dev/null || true)"
archive_sha="$(git -C "$ROOT" archive --format=tar HEAD 2>/dev/null | sha256_stdin 2>/dev/null || true)"
if [[ "$manifest_sha" =~ ^[0-9a-f]{64}$ ]]; then
  pass manifest-hash
else
  fail manifest-hash-unavailable
fi
if [[ "$archive_sha" =~ ^[0-9a-f]{64}$ ]]; then
  pass archive-dry-run
else
  fail archive-dry-run
fi

if [[ "$require_tag" -eq 1 ]]; then
  tag_type="$(git -C "$ROOT" cat-file -t "refs/tags/v$target" 2>/dev/null || true)"
  tag_commit="$(git -C "$ROOT" rev-parse "refs/tags/v$target^{}" 2>/dev/null || true)"
  head_commit="$(git -C "$ROOT" rev-parse HEAD)"
  if [[ "$tag_type" == "tag" && "$tag_commit" == "$head_commit" ]]; then
    pass annotated-release-tag
  else
    fail annotated-release-tag
  fi
else
  warn release-tag-not-required
fi

if [[ "$show_manifest" -eq 1 ]]; then
  while IFS= read -r entry; do
    manifest_entries+=("$entry")
    [[ "$json_output" -eq 1 ]] || printf 'MANIFEST %s\n' "$entry"
  done < <(git -C "$ROOT" ls-files -s)
fi

if [[ "$findings" -eq 0 ]]; then
  if [[ "$json_output" -eq 1 ]]; then
    render_json PASS
  else
    printf 'release-audit: PASS target=%s tracked=%s manifest_sha256=%s archive_sha256=%s\n' \
      "$target" "$tracked" "$manifest_sha" "$archive_sha"
  fi
  exit 0
fi
if [[ "$json_output" -eq 1 ]]; then
  render_json FAIL
else
  printf 'release-audit: FAIL target=%s findings=%s tracked=%s manifest_sha256=%s archive_sha256=%s\n' \
    "$target" "$findings" "$tracked" "$manifest_sha" "$archive_sha"
fi
exit 1
