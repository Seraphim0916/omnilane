#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-usage-doc-tests.XXXXXX")"
PASS=0
FAIL=0

cleanup() {
  /bin/rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

pass() {
  PASS=$((PASS + 1))
  printf 'ok - %s\n' "$1"
}

fail() {
  FAIL=$((FAIL + 1))
  printf 'not ok - %s: %s\n' "$1" "$2"
}

version="$(<"$ROOT/VERSION")"

test_usage_doc_unset_skips_without_affecting_result() {
  local name="release audit usage doc unset" out rc=0 skip_count

  out="$(env -u OMNILANE_USAGE_DOC /bin/bash "$ROOT/scripts/release-audit.sh" \
    --target "$version" --allow-dirty 2>&1)" || rc=$?
  skip_count="$(printf '%s\n' "$out" |
    grep -c '^SKIP usage-doc-drift OMNILANE_USAGE_DOC is not configured$' || true)"

  if [[ "$rc" -ne 0 || "$skip_count" -ne 1 ||
    "$out" != *"release-audit: PASS target=$version"* ]]; then
    fail "$name" "rc=$rc skip_count=$skip_count out=$out"
  else
    pass "$name"
  fi
}

write_expected_tokens() {
  local output="$1"
  {
    {
      /bin/bash "$ROOT/scripts/dispatch.sh" --help 2>&1
      /bin/bash "$ROOT/scripts/jobs.sh" --help 2>&1
    } | awk '
      {
        text = $0
        while (match(text, /--[a-z][a-z-]*/)) {
          token = substr(text, RSTART, RLENGTH)
          if (token != "--help") print token
          text = substr(text, RSTART + RLENGTH)
        }
      }
    '
    /bin/bash "$ROOT/bin/omnilane" help 2>&1 |
      awk '$1 == "omnilane" && $2 ~ /^[a-z][a-z-]*$/ { print $2 }'
  } | awk 'NF' | LC_ALL=C sort -u > "$output"
}

test_usage_doc_complete_passes() {
  local name="release audit complete usage doc" doc="$TEST_ROOT/complete.txt" out rc=0

  write_expected_tokens "$doc"
  out="$(OMNILANE_USAGE_DOC="$doc" /bin/bash "$ROOT/scripts/release-audit.sh" \
    --target "$version" --allow-dirty 2>&1)" || rc=$?

  if [[ "$rc" -ne 0 ||
    "$out" != *"PASS usage-doc-drift:expected="* ||
    "$out" != *"release-audit: PASS target=$version"* ]]; then
    fail "$name" "rc=$rc out=$out"
  else
    pass "$name"
  fi
}

test_usage_doc_missing_token_fails_and_names_it() {
  local name="release audit missing usage token" complete="$TEST_ROOT/all.txt"
  local doc="$TEST_ROOT/missing.txt" missing_token out rc=0

  write_expected_tokens "$complete"
  missing_token="$(sed -n '1p' "$complete")"
  sed '1d' "$complete" > "$doc"
  out="$(OMNILANE_USAGE_DOC="$doc" /bin/bash "$ROOT/scripts/release-audit.sh" \
    --target "$version" --allow-dirty 2>&1)" || rc=$?

  if [[ "$rc" -ne 1 ||
    "$out" != *"FAIL usage-doc-drift:"* ||
    "$out" != *"missing=$missing_token"* ||
    "$out" != *"release-audit: FAIL target=$version"* ]]; then
    fail "$name" "rc=$rc missing=$missing_token out=$out"
  else
    pass "$name"
  fi
}

test_usage_doc_unset_skips_without_affecting_result
test_usage_doc_complete_passes
test_usage_doc_missing_token_fails_and_names_it

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
