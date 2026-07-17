#!/usr/bin/env bash
set -euo pipefail
# Offline, read-only release gate. It never creates tags, archives, or releases.

SELF="${BASH_SOURCE[0]}"
while [[ -L "$SELF" ]]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd -P)"

usage() {
  echo "usage: release-audit.sh [--target VERSION] [--allow-dirty] [--require-tag] [--manifest]" >&2
  exit 2
}

target=""
allow_dirty=0
require_tag=0
show_manifest=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || usage
      target="$2"; shift 2 ;;
    --allow-dirty) allow_dirty=1; shift ;;
    --require-tag) require_tag=1; shift ;;
    --manifest) show_manifest=1; shift ;;
    *) usage ;;
  esac
done

if [[ -z "$target" ]]; then
  [[ -f "$ROOT/VERSION" && ! -L "$ROOT/VERSION" ]] || {
    echo "FAIL version-file-unavailable"
    echo "release-audit: FAIL target=unknown findings=1"
    exit 1
  }
  target="$(<"$ROOT/VERSION")"
fi
[[ "$target" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || usage
escaped_target="${target//./\\.}"

findings=0
fail() {
  printf 'FAIL %s\n' "$1"
  findings=$((findings + 1))
}
pass() { printf 'PASS %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; }

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
  printf 'release-audit: FAIL target=%s findings=%s\n' "$target" "$findings"
  exit 1
}

git_root="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$git_root" || "$(cd "$git_root" 2>/dev/null && pwd -P)" != "$ROOT" ]]; then
  fail repository-boundary
  printf 'release-audit: FAIL target=%s findings=%s\n' "$target" "$findings"
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
  scripts/ui.py skills/omnilane/SKILL.md
)
for path in "${required[@]}"; do
  if git -C "$ROOT" ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    pass "tracked-$path"
  else
    fail "missing-required-$path"
  fi
done

for path in install.sh bin/omnilane scripts/dispatch.sh scripts/jobs.sh \
  scripts/doctor.sh scripts/ui.py scripts/lib/job-timeout.pl \
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
  while IFS= read -r entry; do printf 'MANIFEST %s\n' "$entry"; done \
    < <(git -C "$ROOT" ls-files -s)
fi

if [[ "$findings" -eq 0 ]]; then
  printf 'release-audit: PASS target=%s tracked=%s manifest_sha256=%s archive_sha256=%s\n' \
    "$target" "$tracked" "$manifest_sha" "$archive_sha"
  exit 0
fi
printf 'release-audit: FAIL target=%s findings=%s tracked=%s manifest_sha256=%s archive_sha256=%s\n' \
  "$target" "$findings" "$tracked" "$manifest_sha" "$archive_sha"
exit 1
